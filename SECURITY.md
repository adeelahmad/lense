# Security policy

## Supported versions

Fixes go into the next release; only the [latest release](https://github.com/adeelahmad/lense/releases/latest) and
`main` get them. Update to the latest release before reporting, if you can.

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: on the repository, open **Security >
[Report a vulnerability](https://github.com/adeelahmad/lense/security/advisories/new)**. Only the maintainer,
[@adeelahmad](https://github.com/adeelahmad), sees it. Don't put details in an issue, pull request or discussion,
which everyone can read.

Include what you can of:

- the version or commit, and how Lens runs (Docker Compose, Cloudron, Synology, QNAP, Proxmox, native);
- what an attacker needs (no account, a viewer or editor role in one namespace, an admin) and what they get;
- steps or a proof of concept, and the requests involved.

There is no bug bounty. Reports are answered as soon as the maintainer can; fixes go into the
next release, and you're credited in its notes unless you'd rather not be.

## What counts

Lens keeps people's recordings, transcripts and documents, so these matter most:

- reading or changing anything in a namespace you have no role in (the API should answer 404 there), or doing more
  than your role allows;
- getting a working media link (audio, video, frames, documents) you weren't given; only the server's own signed
  links should work;
- authentication and sessions: login, the first-admin setup code, tokens and API keys;
- stored credentials for sources and model servers leaking through the API, logs or exports;
- making the server fetch internal addresses through a URL someone other than the admin can set, such as a web import,
  an extension's web tool or a notification target (SSRF). URLs only an admin sets, such as a model server or a
  source, are trusted, except for the guards Lens documents on them (iCal feeds, the telemetry endpoint);
- injection into the database queries, the shell or the processing tools.

Out of scope: findings that need an admin to configure Lens insecurely on purpose, missing hardening headers with no
attack, denial of service by sheer volume, and vulnerabilities in dependencies that Lens doesn't reach (tell us
anyway if you're unsure).

When you test, use your own instance and data, and don't touch other people's.

## Threat model

[threat-model.md](threat-model.md) is the detailed contract behind this page: what Lens assumes, what it guarantees,
what it leaves to whoever runs it, and how a report is routed. Its machine-readable companions are
[threat-model.yaml](threat-model.yaml) and [threat-model.json](threat-model.json).
