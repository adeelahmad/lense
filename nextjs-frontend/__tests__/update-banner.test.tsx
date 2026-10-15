import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { UpdateBanner } from "@/components/app-shell/update-banner";

jest.mock("@/components/settings/updates-panel", () => ({
  useUpdates: () => ({ data: { available: true, current: "0.9.0", latest: { tag: "v0.10.0" }, agent: false } }),
}));

const room = () => document.documentElement.style.getPropertyValue("--lens-banner");

describe("the update banner", () => {
  beforeEach(() => localStorage.clear());

  it("tells pages that fill the screen how much room it takes, and gives it back when dismissed", () => {
    render(<UpdateBanner admin />);
    expect(screen.getByText("Lens v0.10.0 is out (this is 0.9.0).")).toBeInTheDocument();
    expect(room()).toMatch(/^\d+px$/);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByText(/is out/)).not.toBeInTheDocument();
    expect(room()).toBe("");
  });

  it("takes no room for people who aren't admins", () => {
    render(<UpdateBanner admin={false} />);
    expect(screen.queryByText(/is out/)).not.toBeInTheDocument();
    expect(room()).toBe("");
  });
});
