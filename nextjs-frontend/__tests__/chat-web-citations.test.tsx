import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { Passage } from "@/app/openapi-client/types.gen";
import { CitationChip } from "@/components/chat/citation";
import { citeLabel, isWeb, passageHref, passageTime, siteOf } from "@/components/chat/cite";
import { SourcesList } from "@/components/chat/sources";
import { TooltipProvider } from "@/components/ui/tooltip";

const PAGE: Passage = {
  n: 2,
  source: "web",
  url: "https://www.example.org/news/solar",
  site: "example.org",
  title: "Solar output doubles",
  text: "Output doubled over the year.",
  recording_id: null,
  used: true,
};
const MAIL: Passage = {
  n: 3,
  source: "mail",
  url: "https://mail.google.com/mail/u/mara@harbour.example/#all/18f2a",
  site: "Mara's mail",
  title: "Harbour report",
  text: "The harbour reopens on Monday.",
  recording_id: null,
  used: true,
};
const CLIP: Passage = { n: 1, recording_id: 7, title: "Episode 12 — X", t0: 65000, text: "Welcome back.", used: true };

describe("web citations", () => {
  it("labels a page by its site and links to the page itself", () => {
    expect(isWeb(PAGE)).toBe(true);
    expect(isWeb(CLIP)).toBe(false);
    expect(siteOf(PAGE)).toBe("example.org");
    expect(siteOf({ url: "https://www.example.com/a", site: null })).toBe("example.com");
    expect(citeLabel(PAGE)).toBe("example.org · Solar output doubles");
    expect(passageTime(PAGE)).toBe("example.org");
    expect(passageHref(PAGE)).toBe("https://www.example.org/news/solar");
    expect(passageHref(CLIP)).toBe("/resources/7?t=65");
  });

  it("opens a cited page in a new tab, and an archive excerpt in Lens", () => {
    render(
      <TooltipProvider>
        <p>
          <CitationChip passage={CLIP} n={1} /> <CitationChip passage={PAGE} n={2} />
        </p>
      </TooltipProvider>,
    );
    const page = screen.getByRole("link", { name: /example\.org · Solar output doubles/ });
    expect(page).toHaveAttribute("href", "https://www.example.org/news/solar");
    expect(page).toHaveAttribute("target", "_blank");
    expect(page).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: /Ep\. 12/ })).not.toHaveAttribute("target");
  });

  it("lists a page among the sources with its site", () => {
    render(<SourcesList passages={[PAGE, CLIP]} hover={null} />);
    const card = screen.getByRole("link", { name: /Source 2: .*a web page/ });
    expect(card).toHaveAttribute("href", "https://www.example.org/news/solar");
    expect(card).toHaveAttribute("target", "_blank");
    expect(card).toHaveTextContent("example.org");
    expect(card).toHaveTextContent("Output doubled over the year.");
  });

  it("a message from a connected mailbox", () => {
    expect(isWeb(MAIL)).toBe(true);
    expect(citeLabel(MAIL)).toBe("Mara's mail · Harbour report");
    expect(passageHref(MAIL)).toBe("https://mail.google.com/mail/u/mara@harbour.example/#all/18f2a");
    render(
      <TooltipProvider>
        <CitationChip passage={MAIL} n={3} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("link", { name: /Mara's mail · Harbour report/ })).toHaveAttribute("target", "_blank");
  });
});
