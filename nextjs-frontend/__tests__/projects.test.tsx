import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Projects } from "@/app/openapi-client";
import { isActive } from "@/components/app-shell/nav-config";
import { ProjectScreen } from "@/components/projects/project-screen";
import { ProjectsScreen } from "@/components/projects/projects-screen";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Projects: {
    listProjects: jest.fn(),
    createProject: jest.fn(),
    getProject: jest.fn(),
    updateProject: jest.fn(),
    startThread: jest.fn(),
    updateThread: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    namespace: "home",
    namespaces: [
      { id: 1, name: "home" },
      { id: 2, name: "work" },
    ],
    can: () => true,
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );

const project = {
  id: 4,
  name: "Treehouse with Sam",
  goal: "Build a treehouse in the oak by summer",
  instructions: "",
  namespaces: ["home"],
  coordinator: 10,
  archived: false,
  threads: 2,
  open: 1,
  thread_list: [
    { id: 12, title: "Materials list", done: true, answering: false, last: { role: "assistant", text: "Pine boards" } },
    { id: 11, title: "Find a builder", done: false, answering: true, last: { role: "user", text: "Who builds them?" } },
  ],
};

beforeEach(() => jest.clearAllMocks());

describe("projects", () => {
  it("are under Chat in the navigation", () => {
    expect(isActive("/projects", "/chat")).toBe(true);
    expect(isActive("/projects/4", "/chat")).toBe(true);
    expect(isActive("/projects", "/library")).toBe(false);
  });

  it("starts one in the current namespace with just a name, then opens it", async () => {
    m(Projects.listProjects).mockImplementation(() => ok([]));
    m(Projects.createProject).mockImplementation(() => ok({ ...project, thread_list: [] }));
    show(<ProjectsScreen />);
    fireEvent.click((await screen.findAllByRole("button", { name: "New project" }))[0]);
    const name = await screen.findByLabelText("Name");
    expect(name).toHaveFocus();
    expect(screen.getByRole("checkbox", { name: "home" })).toBeChecked();
    fireEvent.change(name, { target: { value: "Treehouse with Sam" } });
    fireEvent.keyDown(name, { key: "Enter" });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/4"));
    expect(m(Projects.createProject).mock.calls[0][0].body).toEqual({
      name: "Treehouse with Sam",
      goal: "",
      namespaces: ["home"],
    });
  });

  it("lists them with how many threads are open", async () => {
    m(Projects.listProjects).mockImplementation(() => ok([project]));
    show(<ProjectsScreen />);
    expect(await screen.findByText("Treehouse with Sam")).toBeInTheDocument();
    expect(screen.getByText("1 open of 2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Treehouse with Sam/ })).toHaveAttribute("href", "/projects/4");
  });
});

describe("a project", () => {
  it("shows its goal, coordinator and threads, open ones first, with the ones being answered marked", async () => {
    m(Projects.getProject).mockImplementation(() => ok(project));
    show(<ProjectScreen id={4} />);
    expect(await screen.findByText("Build a treehouse in the oak by summer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Coordinator/ })).toHaveAttribute("href", "/chat/10");
    const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.match(/^\/chat\/1[12]$/));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/chat/11", "/chat/12"]);
    expect(screen.getByText("Working")).toBeInTheDocument();
  });

  it("starts a thread with a brief on Enter and keeps the page", async () => {
    m(Projects.getProject).mockImplementation(() => ok(project));
    m(Projects.startThread).mockImplementation(() => ok({ id: 13 }));
    show(<ProjectScreen id={4} />);
    const brief = await screen.findByLabelText("New thread");
    fireEvent.change(brief, { target: { value: "Price the timber" } });
    fireEvent.keyDown(brief, { key: "Enter" });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(m(Projects.startThread).mock.calls[0][0]).toMatchObject({
      path: { pid: 4 },
      body: { brief: "Price the timber" },
    });
    expect(push).not.toHaveBeenCalled();
    toast.mock.calls[0][0].action.onClick();
    expect(push).toHaveBeenCalledWith("/chat/13");
  });

  it("opens an empty thread when there's no brief", async () => {
    m(Projects.getProject).mockImplementation(() => ok(project));
    m(Projects.startThread).mockImplementation(() => ok({ id: 14 }));
    show(<ProjectScreen id={4} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start thread" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat/14"));
    expect(m(Projects.startThread).mock.calls[0][0].body).toEqual({});
  });

  it("marks a thread done with one click", async () => {
    m(Projects.getProject).mockImplementation(() => ok(project));
    m(Projects.updateThread).mockImplementation(() => ok({ ok: true }));
    show(<ProjectScreen id={4} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Mark “Find a builder” done" }));
    await waitFor(() => expect(Projects.updateThread).toHaveBeenCalled());
    expect(m(Projects.updateThread).mock.calls[0][0]).toMatchObject({
      path: { pid: 4, cid: 11 },
      body: { done: true },
    });
  });
});
