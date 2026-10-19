- **A bulk import is one run you can undo whole.** A folder scan, a zip or tar archive's files, a list of links or a
  bookmarks file, and chosen files of a source each make one import run, and every asset it makes carries the run's
  id (`import_run`). `GET /api/v1/import/runs` lists recent runs with how many assets each made and how many are left;
  `POST /api/v1/import/runs/{id}/undo` deletes them all through the normal delete, needing owner rights on each
  (nothing goes when any isn't yours to delete), audited per asset and for the run. The toast after a link or source
  import has Undo, the Import screen lists recent runs with Undo on each, and the assistant has `import_runs` and
  `undo_import` (always after your yes). Assets imported before keep no run, and nothing changes for them.
  ([Import runs](docs/api.md#import-runs))
