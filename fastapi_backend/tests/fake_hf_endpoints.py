"""Hugging Face's Inference Endpoints API in memory, held to Hugging Face's own OpenAPI document
(tests/data/openapi/huggingface-endpoints.json): every request Lens makes must be one the real API takes, and every
answer is one the real API could give. Used as an httpx.MockTransport."""

from __future__ import annotations

import json

import httpx

from tests import openapi_contract as contract

SPEC = contract.load("huggingface-endpoints")
WHEN = "2026-10-18T12:00:00Z"
ME = {"id": "u1", "name": "ada"}


def compute(id_, type_, size, gpus, price):
    return {
        "id": id_,
        "accelerator": "gpu",
        "instanceType": type_,
        "instanceSize": size,
        "numAccelerators": gpus,
        "numCpus": 8 * gpus,
        "memoryGb": 32 * gpus,
        "gpuMemoryGb": 24 * gpus,
        "architecture": "Ada Lovelace",
        "status": "available",
        "pricePerHour": price,
        "quota": {"maxAccelerators": 8, "usedAccelerators": 0},
    }


HARDWARE = {
    "vendors": [
        {
            "name": "aws",
            "status": "available",
            "regions": [
                {
                    "name": "us-east-1",
                    "label": "N. Virginia",
                    "status": "available",
                    "computes": [
                        compute("aws-us-east-1-nvidia-l4-x1", "nvidia-l4", "x1", 1, 0.8),
                        compute("aws-us-east-1-nvidia-l4-x4", "nvidia-l4", "x4", 4, 3.8),
                    ],
                }
            ],
        }
    ]
}


class Endpoints:
    def __init__(self, namespace="ada"):
        self.namespace, self.calls, self.endpoints = namespace, [], {}

    def set_state(self, name, state, url=None, error=None):
        st = self.endpoints[name]["status"]
        st |= {"state": state, "url": url or "", "message": error or state, "errorMessage": error}
        st |= {"readyReplica": 1 if state == "running" else 0}

    def __call__(self, req):
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, str(req.url), body, req.headers.get("authorization")))
        if req.url.host == "huggingface.co":  # whoami-v2: not in the Endpoints document
            assert req.url.path == "/api/whoami-v2"
            return httpx.Response(200, json={"type": "user", **ME})
        contract.check_request(SPEC, req.method, req.url.path, body)
        status, answer = self.answer(req.method, req.url.path.removeprefix("/v2"), body)
        if status < 400:
            contract.check_response(SPEC, req.method, req.url.path, status, answer)
        return httpx.Response(status, json=answer)

    def answer(self, method, path, body):
        ns = self.namespace
        if path == f"/provider/{ns}":
            return 200, HARDWARE
        if path == f"/endpoint/{ns}" and method == "POST":
            e = {
                **body,
                "experimentalFeatures": {},
                "notifications": {"email": False, "push": False},
                "healthRoute": body["model"]["image"]["vLLM"].get("healthRoute") or "/health",
                "tags": body.get("tags") or [],
                "status": {
                    "createdAt": WHEN,
                    "createdBy": ME,
                    "updatedAt": WHEN,
                    "updatedBy": ME,
                    "state": "pending",
                    "message": "Endpoint waiting to be scheduled",
                    "url": "",  # a string even before there is one: the document doesn't let it be null
                    "readyReplica": 0,
                    "targetReplica": 1,
                },
            }
            self.endpoints[body["name"]] = e
            return 200, e
        if path == f"/endpoint/{ns}":
            return 200, {"items": list(self.endpoints.values()), "totalItems": len(self.endpoints)}
        name = path.rsplit("/", 1)[1]
        if name not in self.endpoints:
            return 404, {"error": "Endpoint not found"}
        if method == "DELETE":
            del self.endpoints[name]
            return 200, {}
        return 200, self.endpoints[name]
