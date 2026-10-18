import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { SummaryMark } from "@/components/chat/summary-mark";

describe("the mark under the last message a summary covers", () => {
  it("says how many messages the summary stands for, and shows it on request", () => {
    render(
      <SummaryMark summary={{ text: "Alice wants the capsid samples by Friday.", upto: 12, at: null, covers: 14 }} />,
    );
    expect(screen.getByRole("note", { name: "Summarised up to here" })).toHaveTextContent(
      "the assistant reads a summary of the 14 messages above instead of them",
    );
    expect(screen.queryByText("Alice wants the capsid samples by Friday.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show summary" }));
    expect(screen.getByText("Alice wants the capsid samples by Friday.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide summary" }));
    expect(screen.queryByText("Alice wants the capsid samples by Friday.")).not.toBeInTheDocument();
  });

  it("says the messages above are no longer read after /clear", () => {
    render(<SummaryMark summary={{ text: "", upto: 4, at: null, covers: 1 }} />);
    expect(screen.getByRole("note", { name: "Cleared here" })).toHaveTextContent("no longer reads the 1 message above");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
