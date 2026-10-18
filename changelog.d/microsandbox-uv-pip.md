**Reinstalling in a microsandbox no longer stops at "Getting uv".** A sandbox whose volume an earlier install had
built in asked the backend's virtualenv, which has no pip, for uv and failed with "No module named pip". It now asks
the image's own Python. Rerun the install line to pick it up.
