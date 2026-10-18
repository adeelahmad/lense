"""Amazon SageMaker for auto scaling (scaling.py, docs/scaling.md): a model group's unit is a SageMaker real-time
endpoint running AWS's vLLM container on one ML instance, which serves Lens's model calls while it runs (llm.py signs
them with the provider's AWS key; SageMaker endpoints take no other).

Each unit is three things at SageMaker, all named after the unit and tagged `lens-unit` with its name and `lens` with
Lens's tag: a model (the container, the Hugging Face model it loads, and the execution role SageMaker pulls it with),
an endpoint configuration (one instance of the type the group picks) and the endpoint. Terminating deletes all three.

What a unit costs is an estimate: the instance type's hosting price in the provider's region from the AWS Price List
API, or the admin's price on the provider. Without either Lens doesn't launch.

It talks to AWS through botocore (Apache-2.0), the `aws` extra, fetched when the first AWS provider is saved.
"""

from __future__ import annotations

from .scaling_aws import PRICING_REGION, TAG, AwsApi, AwsError, on_demand_usd
from .scaling_drivers import Driver, DriverError, _field, register
from .scaling_models import USE_AS_PROVIDER

# AWS's vLLM container for SageMaker: its SM_VLLM_* settings are vLLM's options, and it answers OpenAI's chat schema at
# /invocations. 763104351884 publishes it in most regions; docs/scaling.md says where else to look.
VLLM_IMAGE = "763104351884.dkr.ecr.{region}.amazonaws.com/vllm:0.31.0-gpu-py312-cu130-ubuntu24.04-sagemaker"
NAME_MAX = 63  # SageMaker's names: letters, digits and hyphens, at most 63
PENDING = {"Creating", "Updating", "SystemUpdating", "RollingBack"}


def _missing(e):
    """SageMaker's answer for a model, configuration or endpoint it doesn't have: a ValidationException "Could not
    find ..."."""
    return isinstance(e, AwsError) and e.code == "ValidationException" and "could not find" in e.message.lower()


class SageMaker(AwsApi, Driver):
    id = "sagemaker"
    kind = "model"
    label = "Amazon SageMaker"
    about = (
        "Real-time endpoints running a model with vLLM on an ML instance, in your AWS account. Lens signs its model "
        "calls with the provider's AWS key. Billed by the hour by AWS."
    )
    priced = True
    needs_join = False  # an endpoint is reached at SageMaker's runtime address, not over the mesh
    fields = {
        "region": _field("Region", required=True, default="us-east-1", help="the AWS region endpoints run in, such as eu-west-1"),
        "access_key_id": _field(
            "Access key ID", required=True, secret=True, help="an IAM user's key; docs/scaling.md has the policy it needs"
        ),
        "secret_access_key": _field("Secret access key", required=True, secret=True),
        "role_arn": _field(
            "Execution role",
            required=True,
            help="the ARN of the IAM role SageMaker runs endpoints as (it pulls the container); docs/scaling.md has one",
        ),
        "hf_token": _field("Hugging Face token", secret=True, help="for gated models, such as Llama; none: public models only"),
    }
    template = {
        "model": _field("Model", required=True, help="a Hugging Face model vLLM serves, like Qwen/Qwen2.5-7B-Instruct"),
        "instance_type": _field(
            "Instance type", required=True, default="ml.g5.xlarge", help="an ML instance with a GPU, like ml.g5.xlarge"
        ),
        "max_model_len": _field("Context length", "integer", help="the most tokens a request may use; none: the model's"),
        "image": _field("Image", help="a vLLM container for SageMaker; none: AWS's, in the provider's region"),
        "env": _field("Container settings", "list", help="more settings as NAME=value, like SM_VLLM_QUANTIZATION=awq"),
        "use_as_provider": USE_AS_PROVIDER,
    }

    def sm(self, p):
        return self.client(p, "sagemaker")

    def hosting(self, p, itype):
        """(USD per hour, GPUs) of hosting on an instance of this type in the provider's region, from the AWS Price
        List API; (None, None) when it doesn't say."""

        def look():
            match = {"regionCode": self._region(p), "instanceName": itype, "component": "Hosting"}
            out = self._call(
                self.client(p, "pricing", PRICING_REGION),
                "get_products",
                ServiceCode="AmazonSageMaker",
                FormatVersion="aws_v1",
                Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in match.items()],
                MaxResults=10,
            )
            usd, attrs = on_demand_usd(out)
            gpus = attrs.get("gpu")
            return usd, int(gpus) if isinstance(gpus, str) and gpus.isdigit() else None

        try:
            return self._cached(("sagemaker", self._region(p), itype), look)
        except DriverError:
            return None, None  # no pricing:GetProducts permission, say: the admin's price, or none

    def price(self, p, template):
        return self.hosting(p, template.get("instance_type") or "ml.g5.xlarge")[0]

    def check(self, p):
        """Prove the keys work in the region with a read-only call."""
        out = self._call(self.sm(p), "list_endpoints", MaxResults=10)
        return {"region": self._region(p), "endpoints": len(out.get("Endpoints") or [])}

    def launch(self, p, template, name, boot):
        st, sec = p.get("settings") or {}, p.get("secrets") or {}
        region, itype, model = self._region(p), template.get("instance_type") or "ml.g5.xlarge", template["model"]
        usd, gpus = self.hosting(p, itype)
        price = p.get("price") if p.get("price") is not None else usd
        if price is None:
            raise DriverError(
                f"AWS didn't say what hosting on {itype} costs in {region}: allow pricing:GetProducts for these keys, "
                "or give the provider a price per hour"
            )
        if not st.get("role_arn"):
            raise DriverError("a SageMaker provider needs the execution role SageMaker runs endpoints as")
        if len(name) > NAME_MAX:  # keep the unit's number on the end
            n = name.rsplit("-", 1)[-1]
            name = f"{name[: NAME_MAX - 1 - len(n)].rstrip('-')}-{n}"
        env = {"SM_VLLM_MODEL": model, "SM_VLLM_SERVED_MODEL_NAME": model, "LENS_SCALING_UNIT": name}
        if gpus and gpus > 1:
            env["SM_VLLM_TENSOR_PARALLEL_SIZE"] = str(gpus)
        if template.get("max_model_len"):
            env["SM_VLLM_MAX_MODEL_LEN"] = str(template["max_model_len"])
        if sec.get("hf_token"):
            env["HF_TOKEN"] = sec["hf_token"]
        for line in template.get("env") or []:
            k, sep, v = str(line).partition("=")
            if not sep or not k.strip():
                raise DriverError(f"a container setting is NAME=value, not {line!r}")
            env[k.strip()] = v.strip()
        tags = [{"Key": TAG, "Value": name}]
        if boot.get("tag"):
            tags.append({"Key": "lens", "Value": boot["tag"]})
        sm = self.sm(p)
        self._call(
            sm,
            "create_model",
            ModelName=name,
            ExecutionRoleArn=st["role_arn"],
            PrimaryContainer={"Image": (template.get("image") or VLLM_IMAGE).format(region=region), "Environment": env},
            Tags=tags,
        )
        try:
            self._call(
                sm,
                "create_endpoint_config",
                EndpointConfigName=name,
                ProductionVariants=[
                    {
                        "VariantName": "lens",
                        "ModelName": name,
                        "InstanceType": itype,
                        "InitialInstanceCount": 1,  # Lens scales by units, not instances
                        "ContainerStartupHealthCheckTimeoutInSeconds": 1800,  # a large model takes a while to load
                    }
                ],
                Tags=tags,
            )
            self._call(sm, "create_endpoint", EndpointName=name, EndpointConfigName=name, Tags=tags)
        except DriverError:
            self.terminate(p, {"external_id": name})  # nothing half made stays behind
            raise
        key = f"{sec.get('access_key_id')}:{sec.get('secret_access_key')}"  # what llm.py signs SageMaker calls with
        endpoint = {"base_url": f"https://runtime.sagemaker.{region}.amazonaws.com/endpoints/{name}", "model": model, "api_key": key}
        return {"id": name, "endpoint": endpoint, "price": round(float(price), 6)}

    def describe(self, p, node):
        name = node.get("external_id")
        if not name:
            return {"state": "gone"}
        try:
            e = self._call(self.sm(p), "describe_endpoint", EndpointName=name)
        except AwsError as err:
            if _missing(err):
                return {"state": "gone"}
            raise
        status = e.get("EndpointStatus")
        if status == "InService":  # its address and key are the ones launch gave
            return {"state": "running"}
        if status in PENDING:
            return {"state": "pending"}
        why = e.get("FailureReason") or f"it's {status} at SageMaker"
        return {"state": "gone", "why": f"SageMaker: {why}"[:300]}

    def terminate(self, p, node):
        name = node.get("external_id")
        if not name:
            return
        sm = self.sm(p)
        for op, arg in (
            ("delete_endpoint", "EndpointName"),
            ("delete_endpoint_config", "EndpointConfigName"),
            ("delete_model", "ModelName"),
        ):
            try:
                self._call(sm, op, **{arg: name})
            except AwsError as e:
                if not _missing(e):
                    raise

    def listing(self, p, tag):
        sm, found, token = self.sm(p), [], None
        while True:
            params = {"NameContains": f"{tag}-", "MaxResults": 100}
            if token:
                params["NextToken"] = token
            out = self._call(sm, "list_endpoints", **params)
            found += [
                {"id": e["EndpointName"], "name": e["EndpointName"]}
                for e in out.get("Endpoints") or []
                if e["EndpointName"].startswith(f"{tag}-")
            ]
            token = out.get("NextToken")
            if not token:
                return found


register(SageMaker())
