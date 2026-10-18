import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Files } from "@/app/openapi-client";
import type { ResourceFiles, TranslationPlan } from "@/app/openapi-client/types.gen";
import { PlayerProvider } from "@/components/player/media";
import { RecordingProvider, type RecordingCtx } from "@/components/recording/context";
import { FilesTab } from "@/components/recording/files-tab";
import {
  defaultTarget,
  languageChoices,
  planLine,
  targetProblem,
  tidyLanguage,
} from "@/components/recording/translate-model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Files: {
    listFiles: jest.fn(),
    listFileLines: jest.fn(),
    addFile: jest.fn(),
    updateFile: jest.fn(),
    deleteFile: jest.fn(),
    planTranslation: jest.fn(),
    translateTranscript: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
jest.mock("@/components/import/pending", () => ({ chooseFiles: () => Promise.resolve([]) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const LIST: ResourceFiles = { primary: null, files: [], max_mb: 50, can_change: true };
const PLAN: TranslationPlan = {
  recordings: 1,
  hours: 0.1,
  seconds: 30,
  llm: { calls: 1, input_tokens: 600, output_tokens: 500, cost: 0.004, model: "gpt" },
  would_replace: 0,
  needs_confirmation: false,
  lines: 12,
  basis: "12 lines, 1,000 characters, about four to a token, in 1 call at gpt's price in Settings",
  estimate: true,
  problem: null,
  job: null,
};

function show({ canEdit = true, language = "en" }: { canEdit?: boolean; language?: string | null } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ctx = {
    id: 7,
    ns: "pods",
    canEdit,
    fileFocus: null,
    rec: { id: 7, language },
    model: { segments: [{ t0: 0, t1: 1000, text: "Hello." }] },
  } as unknown as RecordingCtx;
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlayerProvider hasMedia durationMs={60000} onSeek={jest.fn()}>
          <RecordingProvider value={ctx}>
            <FilesTab />
          </RecordingProvider>
        </PlayerProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function browserReads(langs: string[]) {
  Object.defineProperty(window.navigator, "languages", { value: langs, configurable: true });
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Files.listFiles).mockImplementation(() => ok(LIST));
  m(Files.planTranslation).mockImplementation(() => ok(PLAN));
  browserReads(["fr-FR", "en"]);
});

describe("the language a translation is made in", () => {
  it("is the first the browser reads that the transcript isn't in", () => {
    expect(defaultTarget(["en-US", "fr"], "en")).toBe("fr");
    expect(defaultTarget(["pt_br", "en"], "en-GB")).toBe("pt-BR");
    expect(defaultTarget(["de"], null)).toBe("de");
    expect(defaultTarget(["en-US", "en"], "en")).toBe(""); // only the transcript's own: the person picks
    expect(tidyLanguage(" ZH-tw ")).toBe("zh-TW");
  });
  it("offers the browser's languages first, never the transcript's", () => {
    const codes = languageChoices(["sv"], "en").map((l) => l.code);
    expect(codes[0]).toBe("sv");
    expect(codes).toContain("fr");
    expect(codes).not.toContain("en");
    expect(new Set(codes).size).toBe(codes.length);
  });
  it("says why a language won't do", () => {
    expect(targetProblem("", "en")).toBe("Choose the language to translate into.");
    expect(targetProblem("French", "en")).toBe("Use a language code such as en or pt-BR.");
    expect(targetProblem("EN", "en")).toBe("The transcript is already in English.");
    expect(targetProblem("fr", "en")).toBeNull();
  });
  it("labels what it would take an estimate", () => {
    expect(planLine(PLAN)).toBe("12 lines in 1 call, ~1k tokens, less than $0.01 (an estimate)");
    expect(planLine({ ...PLAN, llm: { ...PLAN.llm, cost: null } })).toBe(
      "12 lines in 1 call, ~1k tokens, no price for the model in Settings (an estimate)",
    );
    expect(planLine({ ...PLAN, llm: { ...PLAN.llm, cost: 0.125 } })).toContain("about $0.125");
  });
});

describe("Translate… in the Files tab", () => {
  it("translates into the browser's language with Enter, after showing the estimate", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Translate…" }));
    const dialog = await screen.findByRole("dialog", { name: "Translate the transcript" });
    const field = within(dialog).getByLabelText(/Into/);
    expect(field).toHaveValue("fr-FR");
    expect(field).toHaveFocus();
    expect(
      await within(dialog).findByText("12 lines in 1 call, ~1k tokens, less than $0.01 (an estimate)"),
    ).toBeInTheDocument();
    expect(m(Files.translateTranscript)).not.toHaveBeenCalled(); // nothing runs until asked
    m(Files.translateTranscript).mockImplementation(() => ok({ ok: true, job: 41 }));
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(m(Files.translateTranscript)).toHaveBeenCalledTimes(1));
    expect(m(Files.translateTranscript).mock.calls[0][0]).toMatchObject({
      path: { rid: 7 },
      body: { language: "fr-FR" },
    });
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Translating into French (France)" })),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("explains why it can't, and waits", async () => {
    m(Files.planTranslation).mockImplementation(() =>
      ok({ ...PLAN, problem: "No language model is configured (Settings → LLM provider), so Lens can't translate." }),
    );
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Translate…" }));
    const dialog = await screen.findByRole("dialog", { name: "Translate the transcript" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No language model is configured");
    expect(within(dialog).getByRole("button", { name: "Translate" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(within(dialog).getByLabelText(/Into/), { key: "Enter" });
    expect(m(Files.translateTranscript)).not.toHaveBeenCalled();
  });

  it("is for editors", async () => {
    show({ canEdit: false });
    expect(await screen.findByRole("button", { name: "Translate…" })).toHaveAttribute("aria-disabled", "true");
  });
});
