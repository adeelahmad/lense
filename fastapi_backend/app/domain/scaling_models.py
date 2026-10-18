"""Model hosts auto scaling rents GPUs on (scaling_drivers.py, docs/scaling.md): each unit is an OpenAI-compatible
model server (vLLM) on a rented GPU, reached over HTTPS at the host's address with an API key made for that unit.
Units of this kind are `model` units: they don't join the mesh, so they need nothing from it, and terminating one
deletes the GPU.

RunPod: a pod running vLLM's image, its port 8000 behind RunPod's HTTPS proxy (`https://<pod>-8000.proxy.runpod.net`).
The unit's API key goes to vLLM as `VLLM_API_KEY` in the pod's environment, which RunPod keeps with the pod; Lens
keeps it sealed on the unit.

They call the hosts' REST APIs with httpx, which Lens already has, so a provider of these kinds installs nothing.
"""

from __future__ import annotations

import secrets

import httpx

from .scaling_clouds import Rest
from .scaling_drivers import DriverError, _field, register

VLLM_IMAGE = "vllm/vllm-openai:v0.10.1"
PORT = 8000


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
        "use_as_provider": _field(
            "Serve Lens's model calls",
            "boolean",
            default=True,
            help="while a unit runs, it is the LLM provider; the one set before comes back when none runs",
        ),
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
