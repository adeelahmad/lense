Queued pipelines now wait for deferred components before later steps, not just before their first step, and resume
without repeating completed work. Live updates keep the Queued view in worker priority order before applying the
page limit, so new arrivals no longer displace runs that should execute sooner.
