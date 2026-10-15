import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Nav } from "@/components/app-shell/nav";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("next/navigation", () => ({ usePathname: () => "/settings/llm" }));
jest.mock("@/components/notes/notes-tree", () => ({ NotesTree: () => null }));

const nav = (collapsed: boolean) =>
  render(
    <TooltipProvider>
      <Nav admin collapsed={collapsed} counts={{}} />
    </TooltipProvider>,
  );

test("names each group above its first item", () => {
  nav(false);
  const text = screen.getByRole("navigation", { name: "Primary" }).textContent ?? "";
  expect(text.indexOf("Explore")).toBeLessThan(text.indexOf("Entities"));
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
