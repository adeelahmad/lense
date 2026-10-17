import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { SharedFiles, sharedFileUrl } from "@/components/chat/shared-files";
import { codeOf, endedAs, ToolSteps } from "@/components/chat/steps";
import { applyEvent, newTurn, savedSteps, sharedFiles } from "@/components/chat/stream";

const download = jest.fn();
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/library/actions", () => ({
  downloadWithToken: (...a: unknown[]) => download(...a),
}));

const RUN = {
  tool: "run_python",
  args: { code: "print('made it')\nraise SystemExit('careful')" },
  summary: "Ran Python (2 lines), exit 1",
  output: { stdout: "made it\n", stderr: "careful\n", exit_code: 1, timed_out: false, cut: false },
};
const SHARE = {
  tool: "share_sandbox_file",
  args: { path: "chart.svg" },
  summary: "Shared chart.svg to download",
  file: { id: "ab12", name: "chart.svg", size: 2048 },
};

describe("code sandbox steps", () => {
  it("keeps a run's output and a shared file, live and saved", () => {
    let s = newTurn("Chart it");
    s = applyEvent(s, { event: "step", data: JSON.stringify(RUN) });
    s = applyEvent(s, { event: "step", data: JSON.stringify(SHARE) });
    s = applyEvent(s, {
      event: "step",
      data: '{"tool":"run_bash","args":{"command":"ls"},"output":"junk","file":{"id":3}}',
    });
    expect(s.steps[0].output).toEqual(RUN.output);
    expect(s.steps[1].file).toEqual(SHARE.file);
    expect(s.steps[2].output).toBeUndefined();
    expect(s.steps[2].file).toBeUndefined();
    expect(sharedFiles(s.steps)).toEqual([SHARE.file]);
    expect(savedSteps([RUN, SHARE, { tool: "search_transcripts" }])).toEqual([
      RUN,
      SHARE,
      { tool: "search_transcripts", args: {}, summary: "" },
    ]);
  });

  it("names the code a run ran and how it ended", () => {
    expect(codeOf(RUN)).toBe(RUN.args.code);
    expect(codeOf({ tool: "run_bash", args: { command: "ls -l" }, summary: "" })).toBe("ls -l");
    expect(codeOf(SHARE)).toBeNull();
    expect(endedAs(RUN.output)).toBe("exit 1");
    expect(endedAs({ ...RUN.output, timed_out: true })).toBe("timed out");
  });

  it("shows the code collapsed, then the code and its output", () => {
    render(<ToolSteps steps={[RUN]} />);
    expect(screen.queryByLabelText("Python code")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ran Python/ }));
    expect(screen.getByLabelText("Python code")).toHaveTextContent("print('made it')");
    expect(screen.getByText("Output · exit 1")).toBeInTheDocument();
    expect(screen.getByText("made it")).toBeInTheDocument();
    expect(screen.getByText("careful")).toHaveClass("text-red-dark");
  });

  it("downloads a file the sandbox shared from the answer", async () => {
    download.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("404: no such file"));
    render(<SharedFiles chatId={7} files={[SHARE.file]} />);
    const chip = screen.getByRole("button", { name: /chart\.svg/ });
    fireEvent.click(chip);
    await waitFor(() => expect(download).toHaveBeenCalledWith("/api/v1/chats/7/files/ab12", "t", "chart.svg"));
    fireEvent.click(chip);
    expect(await screen.findByRole("alert")).toHaveTextContent("404: no such file");
    expect(sharedFileUrl(7, "a/b")).toBe("/api/v1/chats/7/files/a%2Fb");
  });

  it("shows nothing without a conversation or files", () => {
    const { container } = render(<SharedFiles chatId={null} files={[SHARE.file]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
