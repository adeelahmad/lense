import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Sensors } from "@/app/openapi-client";
import type { SensorCatalog } from "@/app/openapi-client/types.gen";
import { HandlingEditor } from "@/components/sensors/parts";
import { SensorsPage } from "@/components/sensors/sensors-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Sensors: {
    listSensors: jest.fn(),
    applySuggestion: jest.fn(),
    reviewNew: jest.fn(),
    updateSensor: jest.fn(),
  },
  Sources: {},
  Pipelines: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ admin: true, me: { user: { admin: true } }, namespaces: [{ id: 3, name: "home" }] }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const CATALOG: SensorCatalog = {
  sensors: [
    { id: 1, name: "NAS", type: "smb", label: "SMB", family: "files", channels: 2 },
    {
      id: 2,
      name: "router",
      type: "syslog",
      label: "Syslog sender",
      family: "stream",
      status: "new",
      device: "192.168.1.1",
      channels: 3,
      readings: 120,
      suggested: { handling: { raw_days: 14 }, reason: "Log lines: keep everything for two weeks." },
    },
    {
      id: 4,
      name: "kitchen",
      type: "mqtt",
      label: "MQTT device",
      family: "stream",
      status: "active",
      namespace: "home",
      handling: { store: "changes", raw_days: 7, rollup_days: 730 },
      channels: 2,
      readings: 40,
    },
  ],
  types: {},
  hub: { enabled: false, mqtt: true, syslog: true, processes: [], new: 1 },
};

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ToastProvider>{ui}</ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

test("the Sensors page shows the hub, new sensors with their suggestion, streams and files", async () => {
  m(Sensors.listSensors).mockReturnValue(ok(CATALOG));
  m(Sensors.applySuggestion).mockReturnValue(ok({ handling: {}, reason: "" }));
  m(Sensors.updateSensor).mockReturnValue(ok({ ok: true }));
  wrap(<SensorsPage />);
  expect(await screen.findByText("1 new sensor to look at")).toBeInTheDocument();
  expect(screen.getByText("The hub is off.")).toBeInTheDocument();
  expect(screen.getByText(/keep everything for two weeks/)).toBeInTheDocument();
  expect(screen.getByText("Changes for a week, hourly summaries for 2 years")).toBeInTheDocument();
  expect(screen.getByText("NAS")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(Sensors.applySuggestion).toHaveBeenCalled());
  expect(m(Sensors.applySuggestion).mock.calls[0][0].path).toEqual({ sid: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Ignore" }));
  await waitFor(() => expect(Sensors.updateSensor).toHaveBeenCalled());
  expect(m(Sensors.updateSensor).mock.calls[0][0].body).toEqual({ status: "ignored" });
});

test("the handling editor sends only what changed, and empty goes back to the default", () => {
  const onSave = jest.fn();
  wrap(
    <HandlingEditor
      own={{ raw_days: 14 }}
      resolved={{ store: "all", raw_days: 14, rollup_days: 365, max_per_minute: 600 }}
      log={false}
      hasNamespace={false}
      onSave={onSave}
    />,
  );
  const save = screen.getByRole("button", { name: "Save handling" });
  expect(save).toBeDisabled();
  expect(screen.getByLabelText("Write a daily digest into its namespace")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Readings for (days)"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Hourly summaries for (days)"), { target: { value: "0" } });
  fireEvent.click(save);
  expect(onSave).toHaveBeenCalledWith({ raw_days: null, rollup_days: 0 });
  fireEvent.change(screen.getByLabelText("Readings for (days)"), { target: { value: "two" } });
  expect(screen.getByText("A whole number of days")).toBeInTheDocument();
  expect(save).toBeDisabled();
});
