- **Choose the content type as it comes in.** Import has a **What it is** pick beside each file, pasted text and web
  page, offering the content types of its base type (Recognised automatically until you pick one). `POST /uploads`,
  `POST /import`, `POST /import/web` and `/import/web/links` take a `content_type`, and the assistant's `import_files`
  and `import_urls` take one when you say what the files or pages are: the asset is that content type from the start,
  so its pipeline runs the first time, not after a change and a rerun. One of another base type (a podcast for a
  photo) is refused before anything is saved, naming the ones that fit. Without one, nothing changes: it's recognised
  from the file ([Content types](docs/processing.md#content-types)).
