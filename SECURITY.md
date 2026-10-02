# Security policy

## Supported versions

Fixes go into the next release; only the [latest release](https://github.com/adeelahmad/lense/releases/latest) and
`main` get them. Update to the latest release before reporting, if you can.

## Reporting a vulnerability

Report it privately through GitHub: **Security > Report a vulnerability** on this repository
([direct link](https://github.com/adeelahmad/lense/security/advisories/new)). Please don't open a public issue, pull
request or discussion about it.

Include what you can of:

- the version or commit, and how Lens runs (Docker Compose, Cloudron, Synology, QNAP, Proxmox, native);
- what an attacker needs (no account, a viewer or editor role in one namespace, an admin) and what they get;
- steps or a proof of concept, and the requests involved.

You'll get an answer within a week. Once a fix is ready we'll agree a disclosure date with you, publish an advisory
and credit you unless you'd rather not be named.

## What counts

Lens keeps people's recordings, transcripts and documents, so these matter most:

- reading or changing anything in a namespace you have no role in (the API should answer 404 there), or doing more
  than your role allows;
- getting a working media link (audio, video, frames, documents) you weren't given; only the server's own signed
  links should work;
- authentication and sessions: login, the first-admin setup code, tokens and API keys;
- stored credentials for sources and model servers leaking through the API, logs or exports;
- making the server fetch internal addresses through a URL it is given, such as an import or a model server (SSRF);
- injection into the database queries, the shell or the processing tools.

Out of scope: findings that need an admin to configure Lens insecurely on purpose, missing hardening headers with no
attack, denial of service by sheer volume, and vulnerabilities in dependencies that Lens doesn't reach (tell us
anyway if you're unsure).

When you test, use your own instance and data, and don't touch other people's.
