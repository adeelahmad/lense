- **Client certificates for log servers.** Settings → Logs takes this machine's certificate and key (PEM, stored
  encrypted) for syslog over TLS and https log endpoints that ask for one (mutual TLS), and a private CA's certificate
  to check the log server against ([Logging](docs/logging.md#client-certificates-and-a-private-ca)).
