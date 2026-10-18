- **Send logs straight to Grafana Loki or another HTTP endpoint.** Settings → Logs can now POST every line, in batches,
  to Loki's push API (Grafana Cloud and Alloy too), or as JSON for Vector, Fluent Bit and Logstash, with optional
  headers for a token, stored encrypted and dropped when the address moves to another server. Off by default, sent
  from a background queue so a slow endpoint never holds Lens up, with a test line and sent and dropped counts in
  Settings. Agents use `PUT /api/v1/settings/logs` and `POST /api/v1/settings/logs/test?to=http`
  ([Logging](docs/logging.md#sending-to-loki-or-another-http-endpoint)).
