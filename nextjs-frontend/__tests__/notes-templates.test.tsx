import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { TemplatePicker } from "@/components/notes/templates";

jest.mock("@/app/openapi-client", () => ({
  Notes: { listTemplates: jest.fn(), templatesNote: jest.fn(), copyPage: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});
beforeEach(() => jest.clearAllMocks());

describe("new note from a template", () => {
  it("finds a template as you type, and Enter starts a note from the first", async () => {
    m(Notes.listTemplates).mockImplementation(() =>
      ok({
        namespace: "home",
        root: 1,
        templates: [
          { id: 2, title: "Meeting", summary: "For weekly calls" },
          { id: 3, title: "Trip" },
        ],
      }),
    );
    m(Notes.copyPage).mockImplementation(() => ok({ id: 9, title: "Trip" }));
    const onClose = jest.fn();
    show(<TemplatePicker ns="home" onClose={onClose} />);
    const box = await screen.findByRole("combobox", { name: "Template" });
    expect(await screen.findByText("Meeting")).toBeInTheDocument();
    expect(screen.getByText("For weekly calls")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "tri" } });
    await waitFor(() => expect(screen.queryByText("Meeting")).not.toBeInTheDocument());
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/9?new=1"));
    expect(m(Notes.copyPage).mock.calls[0][0]).toMatchObject({ path: { pid: 3 }, body: { title: null, parent: null } });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("with no templates yet, makes the Templates note and opens it", async () => {
    m(Notes.listTemplates).mockImplementation(() => ok({ namespace: "home", root: null, templates: [] }));
    m(Notes.templatesNote).mockImplementation(() => ok({ id: 4, title: "Templates" }));
    show(<TemplatePicker ns="home" onClose={jest.fn()} />);
    expect(await screen.findByText(/A template is any note inside the Templates note/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make the Templates note" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/4"));
    expect(m(Notes.templatesNote).mock.calls[0][0].body).toEqual({ ns: "home" });
  });

  it("with an empty Templates note, opens it", async () => {
    m(Notes.listTemplates).mockImplementation(() => ok({ namespace: "home", root: 4, templates: [] }));
    show(<TemplatePicker ns="home" onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Open the Templates note" }));
    expect(push).toHaveBeenCalledWith("/notes/4");
    expect(Notes.templatesNote).not.toHaveBeenCalled();
  });
});
