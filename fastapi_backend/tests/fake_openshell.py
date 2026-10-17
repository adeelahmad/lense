"""A fake NVIDIA OpenShell gateway, for the code sandbox's tests (app/domain/sandbox.py).

Lens's own OpenShell client (the openshell SDK) talks gRPC to it, as it would to a real gateway. Each sandbox is a
folder here, and the commands run in it are local processes started in that folder (python3 is this test's Python).
It keeps what it was asked, for tests to check: each sandbox's spec, and every command."""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import shutil
import subprocess
import sys
import threading

import grpc
from openshell._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc

READY, STOPPED = openshell_pb2.SANDBOX_PHASE_READY, openshell_pb2.SANDBOX_PHASE_STOPPED
CHUNK = 64 * 1024


class Gateway(openshell_pb2_grpc.OpenShellServicer):
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.boxes: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []  # (method, sandbox)
        self.execs: list[tuple[str, list[str]]] = []  # (sandbox, command)
        self.lock = threading.Lock()
        self.n = 0

    def _box(self, name, context):
        b = self.boxes.get(name)
        if not b:
            context.abort(grpc.StatusCode.NOT_FOUND, f"sandbox {name} not found")
        return b

    def _view(self, name):
        b = self.boxes[name]
        return openshell_pb2.SandboxResponse(
            sandbox=openshell_pb2.Sandbox(
                metadata=datamodel_pb2.ObjectMeta(id=b["id"], name=name, workspace="default"),
                spec=b["spec"],
                status=openshell_pb2.SandboxStatus(phase=b["phase"]),
            )
        )

    def Health(self, request, context):
        return openshell_pb2.HealthResponse(status=openshell_pb2.SERVICE_STATUS_HEALTHY, version="fake")

    def CreateSandbox(self, request, context):
        with self.lock:
            self.calls.append(("create", request.name))
            if request.name in self.boxes:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, f"sandbox {request.name} already exists")
            self.n += 1
            folder = self.root / request.name
            folder.mkdir(parents=True)
            self.boxes[request.name] = {
                "id": f"sb-{self.n}",
                "spec": request.spec,
                "phase": READY,
                "dir": folder,
                "labels": dict(request.labels),
            }
            return self._view(request.name)

    def GetSandbox(self, request, context):
        self._box(request.name, context)
        return self._view(request.name)

    def StartSandbox(self, request, context):
        self.calls.append(("start", request.name))
        self._box(request.name, context)["phase"] = READY
        return self._view(request.name)

    def StopSandbox(self, request, context):
        self.calls.append(("stop", request.name))
        self._box(request.name, context)["phase"] = STOPPED
        return self._view(request.name)

    def DeleteSandbox(self, request, context):
        self.calls.append(("delete", request.name))
        b = self.boxes.pop(request.name, None)
        if b:
            shutil.rmtree(b["dir"], ignore_errors=True)
        outcome = openshell_pb2.DELETION_OUTCOME_COMPLETED if b else openshell_pb2.DELETION_OUTCOME_ALREADY_ABSENT
        return openshell_pb2.DeleteSandboxResponse(outcome=outcome, sandbox_id=b["id"] if b else "")

    def ExecSandbox(self, request, context):
        b = self._box(request.sandbox, context)
        if b["phase"] != READY:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, f"sandbox {request.sandbox} isn't running")
        command = list(request.command)
        self.execs.append((request.sandbox, command))
        run = [sys.executable if command[0] == "python3" else command[0], *command[1:]]
        timeout = request.execution_timeout.seconds or None
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(b["dir"]), "LANG": "C.UTF-8"}
        try:
            done = subprocess.run(run, cwd=b["dir"], input=request.stdin, capture_output=True, timeout=timeout, env=env)
            out, err, code = done.stdout, done.stderr, done.returncode
        except subprocess.TimeoutExpired as e:
            out, err, code = e.stdout or b"", e.stderr or b"", 124
        for i in range(0, len(out), CHUNK):
            yield openshell_pb2.ExecSandboxEvent(stdout=openshell_pb2.ExecSandboxStdout(data=out[i : i + CHUNK]))
        for i in range(0, len(err), CHUNK):
            yield openshell_pb2.ExecSandboxEvent(stderr=openshell_pb2.ExecSandboxStderr(data=err[i : i + CHUNK]))
        yield openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=code))


def start(root: pathlib.Path):
    """A fake gateway on a free port: (server, gateway, "host:port")."""
    gw = Gateway(root)
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=8))
    openshell_pb2_grpc.add_OpenShellServicer_to_server(gw, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, gw, f"127.0.0.1:{port}"
