- **Import webhooks can hand back PDFs.** A light server without LibreOffice or Chromium, such as a Raspberry Pi, can
  now take Word, PowerPoint and Excel files, emails and saved web pages through an import webhook whose owner turned on
  `hand_back`: the file is kept and waits, the answer says `needs_pdf`, and the conversion node that sent it pushes the
  PDF back with `?for=<recording>`. That PDF is what the document's pages are drawn and read from, and its pipeline
  runs then. It's off on every hook, and nothing changes on a server that converts those files itself
  ([PDF hand-back](docs/api.md#pdf-hand-back)).
