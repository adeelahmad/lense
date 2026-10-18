- **A pipeline's Read step picks its own converter.** One pipeline can make its documents and photographed pages into
  PDFs with anytopdf while the rest keep the server's choice in Settings → Documents: set **Make PDFs with** on its Read
  step, or `converter` (`auto`, `anytopdf` or `lens`) in the step over the API and MCP. An image read again by Lens
  itself no longer keeps pointing at a PDF an earlier anytopdf run made of it
  ([processing](docs/processing.md#choosing-the-converter-for-a-read-step)).
