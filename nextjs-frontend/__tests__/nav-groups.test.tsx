import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Nav } from "@/components/app-shell/nav";
import { isActive, navFor } from "@/components/app-shell/nav-config";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("next/navigation", () => ({ usePathname: () => "/settings/llm" }));
jest.mock("@/components/notes/notes-tree", () => ({ NotesTree: () => null }));

const nav = (collapsed: boolean, counts = {}) =>
  render(
    <TooltipProvider>
      <Nav admin collapsed={collapsed} counts={counts} />
    </TooltipProvider>,
  );

test("names each group above its first item", () => {
  nav(false);
  const text = screen.getByRole("navigation", { name: "Primary" }).textContent ?? "";
  expect(text.indexOf("Explore")).toBeLessThan(text.indexOf("Timeline"));
  expect(text.indexOf("Timeline")).toBeLessThan(text.indexOf("Entities"));
  expect(text.indexOf("Notes")).toBeLessThan(text.indexOf("Explore"));
  expect(text.indexOf("Automate")).toBeLessThan(text.indexOf("Pipelines"));
  expect(text.indexOf("Manage")).toBeLessThan(text.indexOf("Costs"));
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
});

test("collapsed to icons, the groups are lines rather than names", () => {
  nav(true);
  expect(screen.queryByText("Explore")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Entities" })).toBeInTheDocument();
});

test("the timeline is under Explore for everyone", () => {
  for (const admin of [true, false]) {
    const t = navFor(admin).find((i) => i.href === "/timeline");
    expect(t?.group).toBe("Explore");
  }
  expect(isActive("/timeline", "/timeline")).toBe(true);
});

test("feeds are under Explore, after Podcasts, for everyone", () => {
  for (const admin of [true, false]) {
    const items = navFor(admin);
    const feeds = items.findIndex((i) => i.href === "/feeds");
    expect(items[feeds]?.group).toBe("Explore");
    expect(items[feeds - 1]?.href).toBe("/podcasts");
  }
  expect(isActive("/feeds/7", "/feeds")).toBe(true);
});

test("Approvals is in the nav only while something waits, and Automate keeps its name either way", () => {
  const { unmount } = nav(false);
  expect(screen.queryByRole("link", { name: /Approvals/ })).not.toBeInTheDocument();
  expect(screen.getByText("Automate")).toBeInTheDocument();
  unmount();
  nav(false, { approvals: 2 });
  expect(screen.getByRole("link", { name: "Approvals 2" })).toHaveAttribute("href", "/approvals");
  const text = screen.getByRole("navigation", { name: "Primary" }).textContent ?? "";
  expect(text.indexOf("Automate")).toBeLessThan(text.indexOf("Approvals"));
  expect(text.indexOf("Approvals")).toBeLessThan(text.indexOf("Pipelines"));
});
