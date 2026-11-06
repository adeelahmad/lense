### Fixed

- Syslog over TLS now sends a bounded TLS shutdown before closing its connection, avoiding a reset that could discard the final log message with TLS 1.3 collectors. Certificate verification is unchanged; a non-responsive collector can delay shutdown by at most half a second.
