import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState, type ReactNode } from "react";

import { Rdf } from "@/app/openapi-client";
import type { RdfImportResult } from "@/app/openapi-client/types.gen";
import { DublinCore } from "@/components/iiif/metadata-fields";
import { describeChange, dirtyFields, patchFor, validate, type Meta } from "@/components/iiif/metadata-model";
import { importSummary, linkedDataUri, RdfImportDialog, rdfFileName } from "@/components/iiif/rdf";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Rdf: { importNamespaceRdf: jest.fn(), getRecordingRdf: jest.fn(), getNamespaceRdf: jest.fn() },
  Entities: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const result = (over: Partial<RdfImportResult>): RdfImportResult => ({
  dry_run: true,
  triples: 4,
  matched: 1,
  changed: 1,
  items: [{ subject: "https://x/id/recording/3", recording: 3, fields: ["terms"], notes: [], statements: 0 }],
  unmatched: [],
  ...over,
});

describe("Dublin Core terms in the metadata model", () => {
  it("counts terms as a field: dirty, saved, validated and described", () => {
    const before: Meta = { terms: { spatial: ["Berlin"] } };
    const after: Meta = { terms: { spatial: ["Berlin"], temporal: ["2026"] } };
    expect(dirtyFields(before, after)).toEqual(["terms"]);
    expect(patchFor(after, ["terms"])).toEqual({ terms: { spatial: ["Berlin"], temporal: ["2026"] } });
    expect(validate({ terms: { colour: ["blue"] } }).terms).toMatch(/Dublin Core term/);
    expect(validate(after).terms).toBeUndefined();
    expect(describeChange("terms", before.terms, after.terms)).toBe("Dublin Core: Period");
  });
});

describe("RDF helpers", () => {
  it("names URIs and files", () => {
    expect(linkedDataUri("https://lens.example", { recording: 12 })).toBe("https://lens.example/id/recording/12");
    expect(linkedDataUri("https://lens.example", { namespace: "my pods" })).toBe(
      "https://lens.example/id/namespace/my%20pods",
    );
    expect(rdfFileName({ namespace: "my pods" }, "json-ld")).toBe("my_pods.jsonld");
    expect(rdfFileName({ recording: 4 }, "turtle")).toBe("recording-4.ttl");
  });
  it("sums up an import", () => {
    expect(importSummary(result({}))).toBe("1 recording would change");
    expect(
      importSummary(
        result({ dry_run: false, matched: 3, changed: 2, unmatched: [{ subject: "https://e/1", title: null }] }),
      ),
    ).toBe("2 recordings changed, 1 description already said, 1 description matched nothing");
  });
});

function Harness({ initial }: { initial: Meta }) {
  const [draft, setDraft] = useState<Meta>(initial);
  return (
    <>
      <DublinCore draft={draft} set={(f, v) => setDraft((d) => ({ ...d, [f]: v }))} errors={{}} readOnly={false} />
      <output data-testid="draft">{JSON.stringify(draft)}</output>
    </>
  );
}

describe("the More Dublin Core section", () => {
  it("adds, edits and removes terms, and removes kept statements", () => {
    wrap(
      <Harness
        initial={{
          terms: { spatial: ["Berlin"] },
          statements: [{ p: "https://example.org/vocab#rating", o: "5" }],
        }}
      />,
    );
    const draft = () => JSON.parse(screen.getByTestId("draft").textContent ?? "{}");
    expect(screen.getByDisplayValue("Berlin")).toBeInTheDocument();
    expect(screen.getByText("rating")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /add term/i }));
    const terms = screen.getAllByLabelText("Dublin Core term");
    fireEvent.change(terms[1], { target: { value: "audience" } });
    fireEvent.change(screen.getByLabelText("Audience value"), { target: { value: "Researchers" } });
    expect(draft().terms).toEqual({ spatial: ["Berlin"], audience: ["Researchers"] });

    fireEvent.click(screen.getAllByRole("button", { name: "Remove term" })[0]);
    expect(draft().terms).toEqual({ audience: ["Researchers"] });
    fireEvent.click(screen.getByRole("button", { name: "Remove statement" }));
    expect(draft().statements).toEqual([]);
  });
});

describe("the RDF import dialog", () => {
  it("previews, then imports what would change", async () => {
    m(Rdf.importNamespaceRdf)
      .mockReturnValueOnce(ok(result({ unmatched: [{ subject: "https://e/9", title: "Elsewhere" }] })))
      .mockReturnValueOnce(ok(result({ dry_run: false })));
    const onClose = jest.fn();
    wrap(<RdfImportDialog ns="pods" open onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("RDF"), { target: { value: "<a> <b> <c> ." } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/1 recording would change, 1 description matched nothing/)).toBeInTheDocument();
    expect(screen.getByText(/No recording for “Elsewhere”/)).toBeInTheDocument();
    expect(m(Rdf.importNamespaceRdf).mock.calls[0][0]).toMatchObject({
      path: { name: "pods" },
      body: { data: "<a> <b> <c> .", dry_run: true },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import 1 change" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(m(Rdf.importNamespaceRdf).mock.calls[1][0].body.dry_run).toBe(false);
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "RDF imported" }));
  });
});
