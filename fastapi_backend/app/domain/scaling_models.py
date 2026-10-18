"""Model hosts auto scaling rents GPUs on (scaling_drivers.py, docs/scaling.md): each unit is an OpenAI-compatible
model server (vLLM) on a rented GPU, reached over HTTPS at the host's address with an API key made for that unit.
Units of this kind are `model` units: they don't join the mesh, so they need nothing from it, and terminating one
deletes the GPU.

RunPod: a pod running vLLM's image, its port 8000 behind RunPod's HTTPS proxy (`https://<pod>-8000.proxy.runpod.net`).
The unit's API key goes to vLLM as `VLLM_API_KEY` in the pod's environment, which RunPod keeps with the pod; Lens
keeps it sealed on the unit.

Hugging Face: an Inference Endpoint serving the model with vLLM on hardware from Hugging Face's catalogue (an id like
`aws-us-east-1-nvidia-l4-x1`, which names the cloud, region, GPU and count, and has a price per hour). The endpoint is
public at Hugging Face's address and vLLM asks for the unit's own key, kept as an encrypted secret of the endpoint, so
Lens's model calls never carry the Hugging Face token.

They call the hosts' REST APIs with httpx, which Lens already has, so a provider of these kinds installs nothing.
"""

from __future__ import annotations

import secrets

import httpx

from .scaling_clouds import Rest
from .scaling_drivers import DriverError, _field, register

VLLM_IMAGE = "vllm/vllm-openai:v0.10.1"
PORT = 8000
USE_AS_PROVIDER = _field(
    "Serve Lens's model calls",
    "boolean",
    default=True,
    help="while a unit runs, it is the LLM provider; the one set before comes back when none runs",
)


class RunPod(Rest):
    id = "runpod"
    kind = "model"
    label = "RunPod"
    about = "GPU pods running a model with vLLM, reached over HTTPS with a key of their own. Billed by the second by RunPod."
    base = "https://rest.runpod.io/v1"
    needs_join = False
    fields = {
        "token": _field("API key", required=True, secret=True, help="a RunPod API key with read and write access"),
        "cloud_type": _field("Cloud", default="SECURE", options=["SECURE", "COMMUNITY"], help="Secure Cloud or Community Cloud"),
        "data_centers": _field("Data centers", "list", help="where pods may start, like EU-RO-1; none: anywhere"),
        "hf_token": _field("Hugging Face token", secret=True, help="for gated models; it goes into each pod's environment"),
    }
    template = {
        "model": _field("Model", required=True, help="a Hugging Face model vLLM serves, like Qwen/Qwen2.5-7B-Instruct"),
        "gpu_types": _field("GPU types", "list", required=True, default=["NVIDIA L4"], help="RunPod GPU ids, in order of preference"),
        "gpu_count": _field("GPUs", "integer", default=1, help="GPUs per pod; the model is split across them"),
        "disk_gb": _field("Disk (GB)", "integer", default=60, help="room for the model's weights"),
        "max_model_len": _field("Context length", "integer", help="the most tokens a request may use; none: the model's"),
        "interruptible": _field("Spot", "boolean", default=False, help="cheaper, but RunPod may stop the pod at any time"),
        "image": _field("Image", default=VLLM_IMAGE, help="a vLLM OpenAI-compatible server image"),
        "args": _field("vLLM options", "list", help="more of vLLM's options, like --quantization awq"),
        "use_as_provider": USE_AS_PROVIDER,
    }
    probe: httpx.BaseTransport | None = None  # tests hand in an httpx.MockTransport for the pods' own addresses

    def _error(self, r):
        try:
            said = r.json()
            said = said.get("error") or said.get("message") if isinstance(said, dict) else None
        except ValueError:
            said = None
        return f"RunPod said {r.status_code}" + (f": {str(said)[:200]}" if said else "")

    def url(self, pod_id):
        return f"https://{pod_id}-{PORT}.proxy.runpod.net"

    def check(self, p):
        pods = self.call(p, "GET", "/pods")
        return {"pods": len(pods) if isinstance(pods, list) else 0}

    def launch(self, p, template, name, boot):
        s, key = p["settings"], secrets.token_urlsafe(32)
        model = template["model"]
        args = ["--model", model, "--served-model-name", model, "--port", str(PORT)]
        if (template.get("gpu_count") or 1) > 1:
            args += ["--tensor-parallel-size", str(template["gpu_count"])]
        if template.get("max_model_len"):
            args += ["--max-model-len", str(template["max_model_len"])]
        args += list(template.get("args") or [])
        env = {"VLLM_API_KEY": key, "LENS_SCALING_UNIT": name}
        if p["secrets"].get("hf_token"):
            env["HF_TOKEN"] = p["secrets"]["hf_token"]
        body = {
            "name": name,
            "imageName": template.get("image") or VLLM_IMAGE,
            "cloudType": s.get("cloud_type") or "SECURE",
            "computeType": "GPU",
            "gpuTypeIds": list(template.get("gpu_types") or []),
            "gpuTypePriority": "custom",
            "gpuCount": template.get("gpu_count") or 1,
            "containerDiskInGb": template.get("disk_gb") or 60,
            "volumeInGb": 0,
            "ports": [f"{PORT}/http"],
            "env": env,
            "dockerStartCmd": args,
            "interruptible": bool(template.get("interruptible")),
        }
        if s.get("data_centers"):
            body |= {"dataCenterIds": list(s["data_centers"]), "dataCenterPriority": "custom"}
        pod = self.call(p, "POST", "/pods", json=body) or {}
        if not pod.get("id"):
            raise DriverError("RunPod made no pod: none of the GPU types may be free right now")
        got = {"id": pod["id"], "endpoint": {"base_url": self.url(pod["id"]) + "/v1", "model": model, "api_key": key}}
        price = pod.get("adjustedCostPerHr") or pod.get("costPerHr")
        if price:
            got["price"] = float(price)
        return got

    def _ready(self, pod_id):
        """vLLM answers its /health (no key needed) once the model is loaded."""
        try:
            with httpx.Client(timeout=10.0, transport=self.probe) as c:
                return c.get(self.url(pod_id) + "/health").status_code == 200
        except httpx.HTTPError:
            return False

    def describe(self, p, node):
        pid = node.get("external_id")
        if not pid:
            return {"state": "gone"}
        pod = self.call(p, "GET", f"/pods/{pid}", ok404=True)
        if not pod or pod.get("desiredStatus") != "RUNNING":
            return {"state": "gone"}  # stopped (a spot pod RunPod took back) or deleted
        return {"state": "running" if self._ready(pid) else "pending"}

    def terminate(self, p, node):
        if node.get("external_id"):
            self.call(p, "DELETE", f"/pods/{node['external_id']}", ok404=True)

    def listing(self, p, tag):
        pods = self.call(p, "GET", "/pods") or []
        return [{"id": x.get("id"), "name": x.get("name")} for x in pods if (x.get("name") or "").startswith(f"{tag}-")]


register(RunPod())


class HuggingFace(Rest):
    id = "huggingface"
    kind = "model"
    label = "Hugging Face"
    about = (
        "Inference Endpoints running a model with vLLM, reached over HTTPS with a key of their own. Billed by the minute by Hugging Face."
    )
    base = "https://api.endpoints.huggingface.cloud/v2"
    whoami = "https://huggingface.co/api/whoami-v2"
    needs_join = False
    fields = {
        "token": _field("Access token", required=True, secret=True, help="a Hugging Face token that may manage Inference Endpoints"),
        "namespace": _field("Account", help="the user or organization the endpoints belong to; none: the token's own user"),
    }
    template = {
        "model": _field("Model", required=True, help="a Hugging Face model vLLM serves, like Qwen/Qwen2.5-7B-Instruct"),
        "hardware": _field(
            "Hardware",
            required=True,
            default="aws-us-east-1-nvidia-l4-x1",
            help="an id from Hugging Face's hardware list: cloud, region, GPU and how many, like aws-us-east-1-nvidia-l4-x1",
        ),
        "max_model_len": _field("Context length", "integer", help="the most tokens a request may use; none: the model's"),
        "image": _field("Image", default=VLLM_IMAGE, help="a vLLM OpenAI-compatible server image"),
        "args": _field("vLLM options", "list", help="more of vLLM's options, like --quantization awq"),
        "use_as_provider": USE_AS_PROVIDER,
    }

    def _error(self, r):
        try:
            said = r.json()
            said = said.get("error") or said.get("message") if isinstance(said, dict) else None
        except ValueError:
            said = None
        return f"Hugging Face said {r.status_code}" + (f": {str(said)[:200]}" if said else "")

    def namespace(self, p):
        if p["settings"].get("namespace"):
            return p["settings"]["namespace"]
        me = self.call(p, "GET", self.whoami)
        if not me.get("name"):
            raise DriverError("Hugging Face didn't say whose token this is; set the account")
        return me["name"]

    def hardware(self, p, ns, hw_id):
        """The catalogue entry for `hw_id`: vendor, region, accelerator, instance type and size, GPUs and price."""
        for vendor in self.call(p, "GET", f"/provider/{ns}").get("vendors") or []:
            for region in vendor.get("regions") or []:
                for c in region.get("computes") or []:
                    if c.get("id") == hw_id:
                        return {**c, "vendor": vendor.get("name"), "region": region.get("name")}
        raise DriverError(f"Hugging Face has no hardware {hw_id!r} for {ns}")

    def check(self, p):
        ns = self.namespace(p)
        return {"account": ns, "endpoints": len(self.call(p, "GET", f"/endpoint/{ns}").get("items") or [])}

    def price(self, p, template):
        hw = self.hardware(p, self.namespace(p), template.get("hardware"))
        return float(hw["pricePerHour"]) if hw.get("pricePerHour") is not None else None

    def launch(self, p, template, name, boot):
        ns, key, model = self.namespace(p), secrets.token_urlsafe(32), template["model"]
        hw = self.hardware(p, ns, template["hardware"])
        if len(name) > 32:  # endpoint names are at most 32 characters: keep the unit's number on the end
            n = name.rsplit("-", 1)[-1]
            name = f"{name[: 31 - len(n)].rstrip('-')}-{n}"
        args = ["--served-model-name", model]
        if (hw.get("numAccelerators") or 1) > 1:
            args += ["--tensor-parallel-size", str(hw["numAccelerators"])]
        if template.get("max_model_len"):
            args += ["--max-model-len", str(template["max_model_len"])]
        args += list(template.get("args") or [])
        body = {
            "name": name,
            "type": "public",  # vLLM asks for the unit's key; Hugging Face's own check would want the account token
            "provider": {"vendor": hw["vendor"], "region": hw["region"]},
            "compute": {
                "accelerator": hw.get("accelerator") or "gpu",
                "instanceType": hw["instanceType"],
                "instanceSize": hw["instanceSize"],
                "scaling": {"minReplica": 1, "maxReplica": 1},  # Lens scales by units, not replicas
            },
            "model": {
                "repository": model,
                "framework": "pytorch",
                "task": "text-generation",
                "image": {"vLLM": {"url": template.get("image") or VLLM_IMAGE, "port": PORT, "healthRoute": "/health"}},
                "args": args,
                "env": {"LENS_SCALING_UNIT": name},
                "secrets": {"VLLM_API_KEY": key},
            },
            "tags": [boot["tag"]],
        }
        made = self.call(p, "POST", f"/endpoint/{ns}", json=body) or {}
        if not made.get("name"):
            raise DriverError("Hugging Face made no endpoint")
        got = {"id": f"{ns}/{made['name']}", "endpoint": {"base_url": None, "model": model, "api_key": key}}
        if hw.get("pricePerHour") is not None:
            got["price"] = float(hw["pricePerHour"])
        return got

    def describe(self, p, node):
        eid = node.get("external_id")
        if not eid:
            return {"state": "gone"}
        e = self.call(p, "GET", f"/endpoint/{eid}", ok404=True)
        if not e:
            return {"state": "gone"}
        st = e.get("status") or {}
        state = st.get("state")
        if state in ("failed", "updateFailed", "paused", "scaledToZero"):
            why = st.get("errorMessage") or st.get("message") or state
            return {"state": "gone", "why": f"Hugging Face: {why}"[:300]}
        got = {"state": "running" if state == "running" and st.get("url") else "pending"}
        if st.get("url"):
            got["endpoint"] = {"base_url": st["url"].rstrip("/") + "/v1", "model": (e.get("model") or {}).get("repository")}
        return got

    def terminate(self, p, node):
        if node.get("external_id"):
            self.call(p, "DELETE", f"/endpoint/{node['external_id']}", ok404=True)

    def listing(self, p, tag):
        ns = self.namespace(p)
        items = self.call(p, "GET", f"/endpoint/{ns}").get("items") or []
        return [{"id": f"{ns}/{x['name']}", "name": x["name"]} for x in items if (x.get("name") or "").startswith(f"{tag}-")]


register(HuggingFace())
