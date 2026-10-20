import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { EmptyChat, nothingInScope } from "@/components/chat/empty";
import { TooltipProvider } from "@/components/ui/tooltip";

const props = {
  scope: {},
  suggestions: ["What topics came up most this month?"],
  onAsk: jest.fn(),
  onChangeScope: jest.fn(),
  noProvider: false,
  admin: false,
};

const show = (p: Partial<React.ComponentProps<typeof EmptyChat>>) =>
  render(
    <TooltipProvider>
      <EmptyChat {...props} {...p} />
    </TooltipProvider>,
  );

describe("empty chat names the next step", () => {
  it("knows when nothing in scope can answer, and waits for the list", () => {
    const rows = [{ namespace: "podcasts" }];
    expect(nothingInScope(undefined, {})).toBe(false);
    expect(nothingInScope([], {})).toBe(true);
    expect(nothingInScope(rows, {})).toBe(false);
    expect(nothingInScope(rows, { namespaces: ["podcasts"] })).toBe(false);
    expect(nothingInScope(rows, { namespaces: ["family"] })).toBe(true);
  });

  it("with nothing imported, offers Import instead of questions nothing can answer", () => {
    show({ nothing: true, canImport: true });
    expect(screen.getByRole("heading", { name: "Nothing to answer from yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Import assets/ })).toHaveAttribute("href", "/import");
    expect(screen.queryByRole("list", { name: "Suggested questions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change scope" })).not.toBeInTheDocument();
  });

  it("with an empty namespace in scope, offers Import and a wider scope", () => {
    const onChangeScope = jest.fn();
    show({ nothing: true, canImport: true, scope: { namespaces: ["family"] }, onChangeScope });
    expect(screen.getByRole("heading", { name: /Nothing in .*family.* yet/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change scope" }));
    expect(onChangeScope).toHaveBeenCalled();
  });

  it("someone who can't import is told who can", () => {
    show({ nothing: true, canImport: false });
    expect(screen.queryByRole("link", { name: /Import assets/ })).not.toBeInTheDocument();
    expect(screen.getByText(/ask one to add you/)).toBeInTheDocument();
  });

  it("with assets, suggests questions as before", () => {
    show({ nothing: false });
    expect(screen.getByRole("heading", { name: "Ask about your archive" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "What topics came up most this month?" })).toBeInTheDocument();
  });
});
