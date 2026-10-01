import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Fields } from "@/app/openapi-client";
import type { FieldDef, FieldValues } from "@/app/openapi-client/types.gen";
import { FieldsDialog } from "@/components/fields/fields-dialog";
import { FieldValuesPanel } from "@/components/fields/fields-ui";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Fields: {
    listFields: jest.fn(),
    createField: jest.fn(),
    updateField: jest.fn(),
    deleteField: jest.fn(),
    getField: jest.fn(),
    getResourceFields: jest.fn(),
    saveResourceFields: jest.fn(),
    getCollectionFields: jest.fn(),
    saveCollectionFields: jest.fn(),
    getFileFields: jest.fn(),
    saveFileFields: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const def = (over: Partial<FieldDef>): FieldDef => ({
  id: 1,
  label: "Interviewer",
  type: "text",
  target: "resource",
  published: false,
  can_change: true,
  collection: null,
  collection_path: [],
  ...over,
});
const VALUES = (can: boolean): FieldValues => ({
  can_change: can,
  fields: [
    { field: def({ id: 1 }), value: "Ana Ruiz" },
    { field: def({ id: 2, label: "Year", type: "number" }), value: 1998 },
    {
      field: def({ id: 3, label: "Format", type: "choice", options: ["Lecture", "Interview"], published: true }),
      value: null,
    },
    { field: def({ id: 4, label: "Themes", type: "choices", options: ["Work", "War"] }), value: ["War"] },
    { field: def({ id: 5, label: "By hand", type: "boolean" }), value: true },
    { field: def({ id: 6, label: "Finding aid", type: "link" }), value: "https://example.org/aid" },
  ],
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  // Radix's checkbox measures itself; jsdom has no ResizeObserver
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => jest.clearAllMocks());

describe("a resource's custom fields", () => {
  it("saves only what changed, and says what won't do", async () => {
    m(Fields.getResourceFields).mockImplementation(() => ok(VALUES(true)));
    wrap(<FieldValuesPanel source={{ kind: "resource", rid: 9 }} />);
    const year = await screen.findByLabelText(/Year/);
    expect(year).toHaveValue("1998");
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(year, { target: { value: "about 1998" } });
    expect(screen.getByText("Year is a number.")).toBeInTheDocument();
    fireEvent.change(year, { target: { value: "1999" } });
    fireEvent.change(screen.getByLabelText(/Format/), { target: { value: "Lecture" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Work" }));
    fireEvent.change(screen.getByLabelText(/By hand/), { target: { value: "" } });
    m(Fields.saveResourceFields).mockImplementation(() => ok(VALUES(true)));
    fireEvent.click(screen.getByRole("button", { name: "Save 4 fields" }));
    await waitFor(() =>
      expect(m(Fields.saveResourceFields).mock.calls[0][0]).toMatchObject({
        path: { rid: 9 },
        body: { values: { "2": 1999, "3": "Lecture", "4": ["Work", "War"], "5": null } },
      }),
    );
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Fields saved" })));
  });

  it("shows viewers the values to read", async () => {
    m(Fields.getCollectionFields).mockImplementation(() => ok(VALUES(false)));
    wrap(<FieldValuesPanel source={{ kind: "collection", ns: "pods", cid: 3 }} title="Fields" />);
    const panel = await screen.findByRole("region", { name: "Fields" });
    expect(within(panel).getByText("Ana Ruiz")).toBeInTheDocument();
    expect(within(panel).getByText("Yes")).toBeInTheDocument();
    expect(within(panel).getByText("War")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: /example\.org/ })).toHaveAttribute(
      "href",
      "https://example.org/aid",
    );
    expect(within(panel).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
    expect(within(panel).getAllByLabelText("Published")).toHaveLength(1);
  });

  it("shows nothing when no field describes it", async () => {
    m(Fields.getFileFields).mockImplementation(() => ok({ can_change: true, fields: [] }));
    const { container } = wrap(<FieldValuesPanel source={{ kind: "file", rid: 9, fid: 2 }} />);
    await waitFor(() => expect(m(Fields.getFileFields)).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});

describe("defining fields", () => {
  /** Open a field's ⋯ menu and choose an item (items run once the menu has closed). */
  async function choose(row: string, item: RegExp | string) {
    fireEvent.keyDown(screen.getByRole("button", { name: `Actions for ${row}` }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: item }));
  }

  it("adds, edits and deletes the namespace's fields", async () => {
    m(Fields.listFields).mockImplementation(() =>
      ok([def({ id: 1 }), def({ id: 2, label: "Venue", collection: 4, collection_path: ["Talks"] })]),
    );
    wrap(<FieldsDialog ns="pods" canDefine open onOpenChange={() => {}} />);
    const list = await screen.findByRole("list", { name: "Fields of pods" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1); // Venue is defined on Talks
    expect(within(list).getByText("Internal")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add a field" }));
    const form = screen.getByRole("form", { name: "New field" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: " Format " } });
    fireEvent.change(within(form).getByLabelText("Type"), { target: { value: "choice" } });
    const add = within(form).getByRole("button", { name: "Add field" });
    expect(add).toHaveAttribute("aria-disabled", "true"); // a choice needs options
    fireEvent.change(within(form).getByLabelText("Options"), { target: { value: "Lecture\nInterview\nlecture" } });
    fireEvent.click(within(form).getByRole("checkbox"));
    m(Fields.createField).mockImplementation(() => ok(def({ id: 3, label: "Format", type: "choice" })));
    fireEvent.click(within(form).getByRole("button", { name: "Add field" })); // no longer wrapped in its reason
    await waitFor(() =>
      expect(m(Fields.createField).mock.calls[0][0]).toMatchObject({
        path: { name: "pods" },
        body: {
          label: "Format",
          type: "choice",
          target: "resource",
          collection: null,
          options: ["Lecture", "Interview"],
          published: true,
        },
      }),
    );

    await choose("Interviewer", "Edit…");
    const edit = await screen.findByRole("dialog", { name: "Edit Interviewer" });
    fireEvent.change(within(edit).getByLabelText("Name"), { target: { value: "Interviewed by" } });
    m(Fields.updateField).mockImplementation(() => ok(def({ label: "Interviewed by" })));
    fireEvent.click(within(edit).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(m(Fields.updateField).mock.calls[0][0]).toMatchObject({
        path: { name: "pods", fid: 1 },
        body: { label: "Interviewed by" },
      }),
    );

    m(Fields.getField).mockImplementation(() => ok(def({ uses: 3 })));
    await choose("Interviewer", "Delete…");
    const ask = await screen.findByRole("dialog", { name: "Delete Interviewer?" });
    expect(
      await within(ask).findByText("3 items have a value for it. They go too, and this can't be undone."),
    ).toBeInTheDocument();
    m(Fields.deleteField).mockImplementation(() => ok(def({ uses: 3 })));
    fireEvent.click(within(ask).getByRole("button", { name: "Delete it and 3 values" }));
    await waitFor(() =>
      expect(m(Fields.deleteField).mock.calls[0][0]).toMatchObject({ path: { name: "pods", fid: 1 } }),
    );
  });

  it("lists a collection's fields and the namespace's that apply there", async () => {
    m(Fields.listFields).mockImplementation(() =>
      ok([
        def({ id: 1, can_change: false }),
        def({ id: 2, label: "Venue", collection: 4, collection_path: ["Talks"], can_change: false }),
      ]),
    );
    wrap(
      <FieldsDialog ns="pods" collection={4} collectionName="Talks" canDefine={false} open onOpenChange={() => {}} />,
    );
    const list = await screen.findByRole("list", { name: "Fields of Talks" });
    expect(within(list).getByText("Venue")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Defined on the namespace" })).toHaveTextContent("Interviewer");
    expect(screen.getByRole("button", { name: "Add a field" })).toHaveAttribute("aria-disabled", "true");
  });
});
