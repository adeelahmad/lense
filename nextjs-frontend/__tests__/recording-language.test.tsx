import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Resources } from "@/app/openapi-client";
import { LanguageEditor } from "@/components/recording/language";
import { languageEdit, languageLine } from "@/components/recording/language-model";
import { tidyLanguage } from "@/components/recording/translate-model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Resources: { updateRecording: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const m = (f: unknown) => f as jest.Mock;
const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });

function show(props: { language: string | null; set?: boolean; editable?: boolean }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <LanguageEditor rid={7} language={props.language} set={!!props.set} editable={props.editable ?? true} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Resources.updateRecording).mockImplementation(({ body }) => ok({ id: 7, ...body, language_set: !!body.language }));
});

describe("an asset's language", () => {
  it("reads by name with its tag, regions and scripts written the usual way", () => {
    expect(languageLine("en-gb")).toBe("British English (en-GB)");
    expect(languageLine("pt_BR")).toBe("Brazilian Portuguese (pt-BR)");
    expect(languageLine("en")).toBe("English (en)");
    expect(tidyLanguage("zh-hant-tw")).toBe("zh-Hant-TW");
    expect(languageLine("nospeech")).toBeNull();
    expect(languageLine(null)).toBeNull();
  });

  it("sends a tidy tag, null to forget it, and nothing when it's the same", () => {
    expect(languageEdit("en_gb", "en")).toEqual({ language: "en-GB" });
    expect(languageEdit(" pt-pt ", null)).toEqual({ language: "pt-PT" });
    expect(languageEdit("en-GB", "en-gb")).toBe("same");
    expect(languageEdit("", "en")).toEqual({ language: null });
    expect(languageEdit("", null)).toBe("same");
    expect(languageEdit("British", "en")).toEqual({ problem: "Use a language tag such as en, en-GB or pt-BR." });
  });

  it("says whether it was detected or set by a person, and only editors change it", () => {
    show({ language: "en", editable: false });
    expect(screen.getByText("English (en) · detected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change the language" })).not.toBeInTheDocument();
  });

  it("lets an editor correct it with Enter, and refuses what isn't a tag", async () => {
    show({ language: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Change the language" }));
    const field = screen.getByRole("combobox", { name: "Language" });
    expect(field).toHaveFocus();
    expect(field).toHaveValue("en");
    fireEvent.change(field, { target: { value: "British" } });
    fireEvent.submit(field.closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("Use a language tag such as en, en-GB or pt-BR.");
    expect(Resources.updateRecording).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "en_gb" } });
    fireEvent.submit(field.closest("form")!);
    await waitFor(() =>
      expect(Resources.updateRecording).toHaveBeenCalledWith(
        expect.objectContaining({ path: { rid: 7 }, body: { language: "en-GB" } }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("combobox", { name: "Language" })).not.toBeInTheDocument());
  });
});
