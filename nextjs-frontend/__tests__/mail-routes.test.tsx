import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState } from "react";

import { Sources } from "@/app/openapi-client";
import {
  blankRule,
  move,
  type RuleForm,
  ruleProblem,
  rulesFromWatch,
  rulesToApi,
  SKIP,
} from "@/components/sources/route-model";
import { RoutingRules } from "@/components/sources/routing-rules";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Sources: { previewRoutes: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });

describe("routing rules model", () => {
  it("round-trips saved rules, skips and deleted namespaces", () => {
    const form = rulesFromWatch([
      { match: { from: ["*@acme.com", "boss@"], subject: ["invoice"] }, namespace: "finance", skip: false },
      { match: { list: ["news"] }, namespace: null, skip: true },
      { match: { to: ["support@"] }, namespace: null, skip: false }, // its namespace was deleted
    ]);
    expect(form[0].conditions).toEqual([
      { field: "from", patterns: "*@acme.com, boss@" },
      { field: "subject", patterns: "invoice" },
    ]);
    expect(form[1].target).toBe(SKIP);
    expect(ruleProblem(form[2])).toBe("Choose a namespace, or skip.");
    expect(rulesToApi(form.slice(0, 2))).toEqual([
      { match: { from: ["*@acme.com", "boss@"], subject: ["invoice"] }, namespace: "finance" },
      { match: { list: ["news"] }, skip: true },
    ]);
  });

  it("needs a pattern, and moves rules within bounds", () => {
    expect(ruleProblem(blankRule("finance"))).toBe("Add a pattern to match.");
    expect(move([1, 2, 3], 2, 1)).toEqual([1, 3, 2]);
    expect(move([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
  });
});

function Harness({ onRules }: { onRules: (r: RuleForm[]) => void }) {
  const [rules, setRules] = useState<RuleForm[]>([]);
  return (
    <RoutingRules
      rules={rules}
      onChange={(r) => {
        setRules(r);
        onRules(r);
      }}
      namespaces={["family", "finance"]}
      home="family"
      source={3}
      path="INBOX"
      showProblems
    />
  );
}

describe("routing rules editor", () => {
  it("adds a rule and shows where the latest messages would go", async () => {
    (Sources.previewRoutes as jest.Mock).mockReset().mockImplementation(() =>
      ok([
        { path: "INBOX/7-1.eml", title: "Invoice 2231", namespace: "finance", skipped: false, rule: 1 },
        { path: "INBOX/7-2.eml", title: "Lunch?", namespace: "family", skipped: false, rule: null },
      ]),
    );
    const seen = jest.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TooltipProvider>
          <Harness onRules={seen} />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Add a pattern to match.");
    expect(screen.getByLabelText("Send to")).toHaveValue("finance"); // another namespace than the watch's
    fireEvent.change(screen.getByLabelText("Patterns"), { target: { value: "*@acme.com" } });
    expect(rulesToApi(seen.mock.calls.at(-1)[0])).toEqual([{ match: { from: ["*@acme.com"] }, namespace: "finance" }]);
    expect(await screen.findByText("→ finance")).toBeInTheDocument();
    expect(screen.getByText("rule 1")).toBeInTheDocument();
    await waitFor(() =>
      expect((Sources.previewRoutes as jest.Mock).mock.calls.at(-1)[0].body).toEqual({
        source: 3,
        path: "INBOX",
        namespace: "family",
        routes: [{ match: { from: ["*@acme.com"] }, namespace: "finance" }],
      }),
    );
  });
});
