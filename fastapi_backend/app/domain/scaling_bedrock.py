"""Amazon Bedrock for auto scaling (scaling.py, docs/scaling.md): a model group's unit is a Bedrock provisioned
throughput, model units of one model held for Lens, which serves Lens's model calls while it runs (llm.py sends them to
Bedrock's Converse API with the provisioned throughput's ARN as the model, signed with the provider's AWS key).

Lens makes each one with no commitment, so it is billed by the hour until Lens deletes it, named after the unit and
tagged `lens-unit` with its name and `lens` with Lens's tag. Bedrock lists no price for it through the AWS Price List,
so the provider needs the admin's price per unit-hour, from Bedrock's pricing page; Lens launches nothing without one.

It talks to AWS through botocore (Apache-2.0), the `aws` extra, fetched when the first AWS provider is saved.
"""

from __future__ import annotations

from .scaling_aws import TAG, AwsApi, AwsError
from .scaling_drivers import Driver, DriverError, _field, register
from .scaling_models import USE_AS_PROVIDER

NAME_MAX = 63  # Bedrock's names: letters and digits, a single - or _ between them, at most 63
PENDING = {"Creating", "Updating"}


def _missing(e):
    return isinstance(e, AwsError) and e.code == "ResourceNotFoundException"


class Bedrock(AwsApi, Driver):
    id = "bedrock"
    kind = "model"
    label = "Amazon Bedrock"
    about = (
        "Provisioned throughput of a Bedrock model in your AWS account, held for Lens with no commitment while the "
        "group needs it. Lens signs its model calls with the provider's AWS key. Billed by the hour by AWS."
    )
    priced = True
    needs_join = False  # Bedrock's runtime is reached at AWS's address, not over the mesh
    fields = {
        "region": _field("Region", required=True, default="us-east-1", help="the AWS region the model runs in, such as us-east-1"),
        "access_key_id": _field(
            "Access key ID", required=True, secret=True, help="an IAM user's key; docs/scaling.md has the policy it needs"
        ),
        "secret_access_key": _field("Secret access key", required=True, secret=True),
    }
    template = {
        "model": _field(
            "Model",
            required=True,
            help="a Bedrock model that takes provisioned throughput, by id or ARN, or your custom or imported model's ARN",
        ),
        "model_units": _field("Model units", "integer", default=1, help="how much throughput each unit holds"),
        "use_as_provider": USE_AS_PROVIDER,
    }

    def bedrock(self, p):
        return self.client(p, "bedrock")

    def price(self, p, template):
        return None  # not in the Price List: the provider's price per unit-hour

    def check(self, p):
        """Prove the keys work in the region with a read-only call."""
        out = self._call(self.bedrock(p), "list_provisioned_model_throughputs", maxResults=100)
        return {"region": self._region(p), "provisioned": len(out.get("provisionedModelSummaries") or [])}

    def launch(self, p, template, name, boot):
        sec, region = p.get("secrets") or {}, self._region(p)
        if p.get("price") is None:
            raise DriverError(
                "Bedrock doesn't list what provisioned throughput costs: give the provider its price per unit-hour, "
                "from Bedrock's pricing page"
            )
        units = int(template.get("model_units") or 1)
        if units < 1:
            raise DriverError("a unit holds at least one model unit")
        if len(name) > NAME_MAX:  # keep the unit's number on the end
            n = name.rsplit("-", 1)[-1]
            name = f"{name[: NAME_MAX - 1 - len(n)].rstrip('-')}-{n}"
        tags = [{"key": TAG, "value": name}]
        if boot.get("tag"):
            tags.append({"key": "lens", "value": boot["tag"]})
        out = self._call(
            self.bedrock(p),
            "create_provisioned_model_throughput",
            clientRequestToken=name,  # a retried launch makes no second one
            modelUnits=units,
            provisionedModelName=name,
            modelId=template["model"],
            tags=tags,
        )
        arn = out["provisionedModelArn"]
        key = f"{sec.get('access_key_id')}:{sec.get('secret_access_key')}"  # what llm.py signs Bedrock calls with
        endpoint = {"base_url": f"https://bedrock-runtime.{region}.amazonaws.com", "model": arn, "api_key": key}
        return {"id": arn, "endpoint": endpoint, "price": round(float(p["price"]), 6)}

    def describe(self, p, node):
        arn = node.get("external_id")
        if not arn:
            return {"state": "gone"}
        try:
            t = self._call(self.bedrock(p), "get_provisioned_model_throughput", provisionedModelId=arn)
        except AwsError as e:
            if _missing(e):
                return {"state": "gone"}
            raise
        status = t.get("status")
        if status == "InService":  # its address and key are the ones launch gave
            return {"state": "running"}
        if status in PENDING:
            return {"state": "pending"}
        why = t.get("failureMessage") or f"it's {status} at Bedrock"
        return {"state": "gone", "why": f"Bedrock: {why}"[:300]}

    def terminate(self, p, node):
        arn = node.get("external_id")
        if not arn:
            return
        try:
            self._call(self.bedrock(p), "delete_provisioned_model_throughput", provisionedModelId=arn)
        except AwsError as e:
            if not _missing(e):
                raise

    def listing(self, p, tag):
        client, found, token = self.bedrock(p), [], None
        while True:
            params = {"nameContains": f"{tag}-", "maxResults": 100, **({"nextToken": token} if token else {})}
            out = self._call(client, "list_provisioned_model_throughputs", **params)
            found += [
                {"id": t["provisionedModelArn"], "name": t["provisionedModelName"]}
                for t in out.get("provisionedModelSummaries") or []
                if t["provisionedModelName"].startswith(f"{tag}-")
            ]
            token = out.get("nextToken")
            if not token:
                return found


register(Bedrock())
