- **Clear the job queue, reorder it and retry failed runs in bulk.** `POST /jobs/cancel` cancels every queued and held
  run you may process (or one namespace's, or chosen ids), `POST /jobs/{jid}/move` moves a queued run to the top or
  bottom or next to another, and `POST /jobs/retry` retries every failed run, the dead letter queue, at the back of
  the queue. Agents do the same with the MCP tools `runs`, `clear_queue`, `move_run` and `retry_runs`
  ([Background work](docs/processing.md#background-work), [the API](docs/api.md#jobs)). A retried run now joins the
  back of the queue.
