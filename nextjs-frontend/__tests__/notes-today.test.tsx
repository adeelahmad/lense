import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { NotesHome } from "@/components/notes/notes-home";
import { TodayNote, localDay } from "@/components/notes/today";

jest.mock("@/app/openapi-client", () => ({
  Notes: {
    todayPage: jest.fn(),
    listPages: jest.fn(() =>
      Promise.resolve({ data: { namespace: "home", pages: [] }, response: { ok: true, status: 200 } }),
    ),
    deletedPages: jest.fn(() => Promise.resolve({ data: [], response: { ok: true, status: 200 } })),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const replace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: jest.fn() }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
let roles: Record<string, string> = { home: "editor", pods: "viewer" };
let picked: string | null = "home";
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    loaded: true,
    namespace: picked,
    namespaces: Object.keys(roles).map((name) => ({ name })),
    can: (role: string, ns?: string) => (ns ? roles[ns] === "editor" || role === "viewer" : true),
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

beforeEach(() => {
  jest.clearAllMocks();
  roles = { home: "editor", pods: "viewer" };
  picked = "home";
});

describe("today's note", () => {
  it("is today here, not in UTC", () => {
    expect(localDay(new Date(2026, 9, 5, 23, 30))).toBe("2026-10-05");
  });

  it("opens today's note of the namespace in view", async () => {
    m(Notes.todayPage).mockImplementation(() => ok({ id: 12, title: "2026-10-15" }));
    show(<TodayNote />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/notes/12"));
    expect(m(Notes.todayPage).mock.calls[0][0].body).toEqual({ ns: "home", date: localDay() });
  });

  it("with every namespace in view, uses the first one you can write in", async () => {
    picked = null;
    roles = { pods: "viewer", home: "editor" };
    m(Notes.todayPage).mockImplementation(() => ok({ id: 3 }));
    show(<TodayNote />);
    await waitFor(() => expect(Notes.todayPage).toHaveBeenCalled());
    expect(m(Notes.todayPage).mock.calls[0][0].body.ns).toBe("home");
  });

  it("says what went wrong, and tries again", async () => {
    m(Notes.todayPage)
      .mockImplementationOnce(() =>
        Promise.resolve({
          error: { detail: "A namespace can have up to 20000 pages." },
          response: { ok: false, status: 400 },
        }),
      )
      .mockImplementationOnce(() => ok({ id: 4 }));
    show(<TodayNote />);
    expect(await screen.findByText("Couldn’t open today’s note")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/notes/4"));
  });

  it("is one click from the Notes page", async () => {
    show(<NotesHome />);
    expect(await screen.findByRole("link", { name: "Today" })).toHaveAttribute("href", "/notes/today");
  });
});
