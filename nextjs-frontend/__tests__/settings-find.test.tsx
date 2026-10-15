import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { findSettings, hitHref, SETTINGS_GROUPS } from "@/components/settings/find";
import { SECTIONS } from "@/components/settings/model";
import { SettingsShell } from "@/components/settings/settings-shell";
import { TooltipProvider } from "@/components/ui/tooltip";

const push = jest.fn();
let admin = true;

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/speakers",
  useRouter: () => ({ push }),
}));
jest.mock("@/components/speakers/speakers-page", () => ({ SpeakersPage: () => <p>The speakers</p> }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin,
    namespaces: [],
    partialNamespaces: [],
    namespace: null,
    roleIn: () => "owner",
    can: () => true,
  }),
}));

const shell = () =>
  render(
    <TooltipProvider>
      <SettingsShell section="speakers" />
    </TooltipProvider>,
  );

beforeEach(() => {
  admin = true;
  push.mockClear();
});

describe("settings groups", () => {
  it("puts every section in exactly one group", () => {
    const grouped = SETTINGS_GROUPS.flatMap((g) => g.sections);
    expect([...grouped].sort()).toEqual(SECTIONS.map((s) => s.id).sort());
    expect(new Set(grouped).size).toBe(grouped.length);
  });

  it("shows the sections under their groups, the model first", () => {
    shell();
    expect(screen.getByText("AI and search")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    const links = screen.getAllByRole("link").map((a) => a.textContent);
    expect(links.indexOf("LLM provider")).toBe(links.indexOf("Speakers") + 1);
    expect(links).toHaveLength(SECTIONS.length + 1);
  });
});

describe("finding a setting", () => {
  it("finds a section by its name first", () => {
    const hits = findSettings("telegram", true);
    expect(hits[0]).toMatchObject({ section: "telegram", byField: false });
    expect(hitHref(hits[0])).toBe("/settings/telegram");
  });

  it("finds a field by its label, and opens the section on it", () => {
    const hits = findSettings("timeout seconds", true);
    const llm = hits.find((h) => h.section === "llm")!;
    expect(llm.byField).toBe(true);
    expect(llm.fields.map((f) => f.key)).toContain("timeout");
  });

  it("finds a field by one of its choices, ignoring case and accents", () => {
    const hits = findSettings("DEEPGRAM", true);
    expect(hits.some((h) => h.section === "transcription" && h.fields.some((f) => f.key === "engine"))).toBe(true);
  });

  it("finds only Speakers for people who aren't admins", () => {
    expect(findSettings("speak", false).map((h) => h.section)).toEqual(["speakers"]);
    expect(findSettings("telegram", false)).toEqual([]);
  });

  it("filters the list as you type, and Enter opens the first", () => {
    shell();
    const box = screen.getByRole("searchbox", { name: "Find a setting" });
    fireEvent.change(box, { target: { value: "slack" } });
    expect(screen.getByRole("link", { name: "Slack" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Transcription" })).not.toBeInTheDocument();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/settings/slack");
    expect(box).toHaveValue("");
  });

  it("says so when nothing matches", () => {
    shell();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a setting" }), { target: { value: "zzzz" } });
    expect(screen.getByText("No setting matches that.")).toBeInTheDocument();
  });

  it("focuses the Find box on /", async () => {
    shell();
    fireEvent.keyDown(document.body, { key: "/" });
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Find a setting" })).toHaveFocus());
  });

  it("has no Find box for people who aren't admins", () => {
    admin = false;
    shell();
    expect(screen.queryByRole("searchbox", { name: "Find a setting" })).not.toBeInTheDocument();
  });
});

describe("⌘K", () => {
  it("finds settings beside pages, a field opening its section on it", async () => {
    const { CommandPalette } = await import("@/components/app-shell/command-palette");
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    render(
      <QueryClientProvider client={new QueryClient()}>
        <CommandPalette />
      </QueryClientProvider>,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "timeout seconds" } });
    const llm = screen.getAllByRole("option").find((o) => o.textContent?.includes("Settings › LLM provider"))!;
    fireEvent.click(llm);
    expect(push).toHaveBeenCalledWith("/settings/llm?field=llm.timeout");
  });
});
