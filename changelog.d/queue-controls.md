- **Clear the job queue, reorder it and retry failed runs in bulk.** Activity has **Clear queue** (it asks, then can
  be undone) and **Retry failed**, and under Queued the runs show in the order workers take them and can be dragged
  into another order (or moved with Alt+↑ and Alt+↓, or Move to top and Move to bottom). The API has the same:
  `POST /jobs/cancel` cancels every queued and held run you may process (or one namespace's, or chosen ids),
  `POST /jobs/{jid}/move` moves a queued run, and `POST /jobs/retry` retries every failed run, the dead letter queue.
  Agents use the MCP tools `runs`, `clear_queue`, `move_run` and `retry_runs`
  ([Background work](docs/processing.md#background-work), [the API](docs/api.md#jobs)). A retried run now joins the
  back of the queue.
