import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { NamespaceSwitcher } from "@/components/app-shell/namespace-switcher";
import { SettingsShell } from "@/components/settings/settings-shell";
import { TooltipProvider } from "@/components/ui/tooltip";

const setNamespace = jest.fn();

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/speakers",
  useRouter: () => ({ push: jest.fn() }),
}));
jest.mock("@/components/speakers/speakers-page", () => ({ SpeakersPage: () => <p>The speakers</p> }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: true,
    namespaces: [{ name: "interviews", recordings: 2 }, { name: "podcasts" }],
    partialNamespaces: [],
    namespace: null,
    setNamespace,
    roleIn: () => "owner",
    isPartial: () => false,
  }),
}));

test("picking a namespace in the phone's drawer closes it", () => {
  const onPick = jest.fn();
  render(<NamespaceSwitcher className="w-full" onPick={onPick} />);
  fireEvent.click(screen.getByRole("button", { name: /^Namespace: all namespaces/ }));
  fireEvent.click(screen.getByRole("option", { name: /^podcasts/ }));
  expect(setNamespace).toHaveBeenCalledWith("podcasts");
  expect(onPick).toHaveBeenCalled();
});

test("on a phone the settings sections open as a list from the section's name, and close on a pick", () => {
  render(
    <TooltipProvider>
      <SettingsShell section="speakers" />
    </TooltipProvider>,
  );
  const toggle = screen.getByRole("button", { name: "Settings section: People" });
  const list = document.getElementById("settings-sections")!;
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(list).toHaveClass("hidden");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(list).not.toHaveClass("hidden");
  fireEvent.click(screen.getByRole("link", { name: "LLM provider" }));
  expect(toggle).toHaveAttribute("aria-expanded", "false");
});
