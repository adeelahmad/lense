import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import type { Components } from "@/app/openapi-client/types.gen";
import type { BodyCtx } from "@/components/settings/bodies";
import { ComponentsStatus, machineLine } from "@/components/settings/components-status";

jest.mock("@/app/openapi-client", () => ({ Admin: { listComponents: jest.fn(), checkComponents: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const machine = { os: "linux", arch: "x86_64", cpus: 4, memory_gb: 15.7, gpus: [], disk_free_gb: 28 };
const VIEW: Components = {
  auto: true,
  machine,
  recommended: { engine: "sensevoice" },
  components: [
    {
      id: "chromium",
      label: "Chromium",
      purpose: "reads web pages",
      kind: "program",
      needed: true,
      here: false,
      hint: "in the full image",
    },
    {
      id: "sensevoice",
      label: "SenseVoice",
      purpose: "transcribes speech",
      kind: "package",
      needed: true,
      size_mb: 1100,
      steps: ["transcribe"],
    },
    { id: "faces", label: "Face models", purpose: "finds faces", kind: "package", needed: true },
    {
      id: "msg",
      label: "Outlook .msg emails",
      purpose: "reads .msg",
      kind: "package",
      needed: false,
      optional: true,
      license: "GPL-3.0",
    },
    { id: "whisper-model", label: "Whisper model", purpose: "x", kind: "model", needed: false },
  ],
  workers: [
    {
      name: "worker-1",
      machine,
      components: {
        sensevoice: { state: "fetching", detail: "installing 16 package(s) for sensevoice" },
        faces: { state: "failed", error: "RuntimeError: no network" },
      },
    },
  ],
};

function ctxWith(onChange: jest.Mock): BodyCtx {
  return { state: () => ({ value: [], onChange }) } as unknown as BodyCtx;
}

test("each component shows where it is, and optional ones can be added", async () => {
  m(Admin.listComponents).mockReturnValue(ok(VIEW));
  m(Admin.checkComponents).mockReturnValue(ok({ ok: true }));
  const onChange = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ComponentsStatus ctx={ctxWith(onChange)} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("SenseVoice")).toBeInTheDocument();
  expect(screen.getByText("4 CPUs · 15.7 GB memory · no GPU · 28 GB free")).toBeInTheDocument();
  expect(screen.getByText("Fetching")).toBeInTheDocument();
  expect(screen.getByText("installing 16 package(s) for sensevoice")).toBeInTheDocument();
  expect(screen.getByText("RuntimeError: no network")).toBeInTheDocument();
  expect(screen.getByText("Not here")).toBeInTheDocument();
  expect(screen.getByText("in the full image")).toBeInTheDocument();
  expect(screen.queryByText("Whisper model")).not.toBeInTheDocument(); // not needed here
  fireEvent.click(screen.getByRole("switch", { name: "Fetch Outlook .msg emails" }));
  expect(onChange).toHaveBeenCalledWith(["msg"]);
  fireEvent.click(screen.getByRole("button", { name: /Check again now/ }));
  await waitFor(() => expect(Admin.checkComponents).toHaveBeenCalled());
});

test("a machine reads as one line", () => {
  expect(machineLine({ ...machine, gpus: [{ name: "RTX 4090", memory_gb: 24 }] })).toBe(
    "4 CPUs · 15.7 GB memory · RTX 4090 · 28 GB free",
  );
  expect(machineLine(null)).toBe("not reported yet");
});
