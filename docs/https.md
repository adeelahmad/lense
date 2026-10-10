# HTTPS with automatic certificates

Lens can serve itself at `https://lens.example.com` with a certificate it gets and renews on its own, from
[Let's Encrypt](https://letsencrypt.org) or [ZeroSSL](https://zerossl.com), with no reverse proxy to set up. It runs
[Caddy](https://caddyserver.com), which answers on the HTTPS port, gets a certificate for each domain over ACME,
renews it about a month before it runs out and passes visitors on to the web app. Passkeys work there, since browsers
allow them on https:// addresses.

Status: **built**, set up in **Settings › HTTPS**. Off by default.

For a machine the internet can't reach at all, [Remote access](remote-access.md) (a Cloudflare tunnel) is the
other way to an https:// address.

## Turning it on

1. Point the domain's DNS at this machine (an A or AAAA record), unless you use the DNS challenge.
2. Give Lens the HTTPS ports: **Open the HTTPS ports** in Settings › HTTPS has the machine's upgrade agent
   ([Updates](updates.md)) run install.sh again for the version running, with `LENS_HTTPS=1`, and Lens restarts. Or
   run the install line again with it yourself. Either way it publishes ports 443 and 80 to the worker, where Caddy
   runs, and remembers it in `.env`, so upgrades keep them.

   ```sh
   curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_HTTPS=1 sh
   ```

   Something else on the machine uses 443 or 80? `LENS_HTTPS_PORT` and `LENS_HTTP_PORT` choose others (Lens's
   addresses then carry the port, like `https://lens.example.com:8443`). `LENS_HTTPS_BIND` limits them to one
   address. Rootless Docker can't publish ports below 1024 unless `net.ipv4.ip_unprivileged_port_start` allows it.
3. In **Settings › HTTPS**: the domain, an email address for the certificate authority (optional; it warns you before
   a certificate expires), and turn it on.

Settings › HTTPS shows whether a process has the ports, the certificates Lens holds and when each runs out, and
Caddy's last lines, so a domain that can't be verified says why.

## Proving the domain is yours

| Challenge | What needs to reach this machine | Wildcards |
| --- | --- | --- |
| **HTTP** (default) | Port 80 or 443 from the internet: the certificate authority visits `http://<domain>/.well-known/acme-challenge/` or the HTTPS port itself (TLS-ALPN) | No |
| **TLS-ALPN only** | Port 443 | No |
| **DNS** | Nothing: Lens adds a TXT record through your DNS provider's API | Yes, `*.example.com` |

The DNS challenge suits a machine on a home network or behind a firewall, as long as the address people use reaches
it (on the LAN, say). Pick the provider and give what it asks for:

| Provider | Fields |
| --- | --- |
| Cloudflare | an API token with Zone › Zone › Read and Zone › DNS › Edit |
| Amazon Route 53 | access key ID and secret of an IAM user allowed to list the hosted zones and change their records; region and hosted zone ID optional |
| DigitalOcean, Duck DNS, Gandi, deSEC, Netlify, Vultr, Linode, IONOS, Bunny | an API token |
| Porkbun, Namecheap, OVHcloud, DNSimple, netcup, Azure, Google Cloud DNS, PowerDNS, RFC 2136 (BIND, Knot) | the fields Settings lists |

Any other [caddy-dns](https://github.com/caddy-dns) plugin works too: its name as the provider (like `hetzner`), and
its fields as JSON. `dns_package` names a plugin outside caddy-dns (`github.com/<owner>/<repo>`).

## Certificate authorities

| `ca` | |
| --- | --- |
| `both` (default) | Let's Encrypt, and ZeroSSL when Let's Encrypt can't issue (an outage, a rate limit) |
| `letsencrypt` | Let's Encrypt only. `staging: true` uses its staging service while you try things out (browsers don't trust those certificates) |
| `zerossl` | ZeroSSL only. Lens gets ZeroSSL's account credentials with the email address; or give the EAB key ID and HMAC key from your ZeroSSL account |
| `custom` | any ACME directory (`ca_url`), like a company's own step-ca; EAB credentials when it needs them |

## Settings

All in Settings › HTTPS (`PUT /api/v1/settings/https`; `POST /api/v1/settings/https/ports` opens the ports):

| Key | Default | |
| --- | --- | --- |
| `enabled` | `false` | |
| `domains` | `[]` | the names to serve, like `lens.example.com` |
| `email` | `""` | the ACME account's contact |
| `ca`, `ca_url`, `staging` | `both` | see above |
| `eab_key_id`, `eab_hmac_key` | | external account binding (the HMAC key is a secret) |
| `challenge` | `http` | `http`, `tls-alpn` or `dns` |
| `dns_provider`, `dns_package`, `dns_credentials` | | the DNS challenge's provider, plugin and fields (a JSON object; a secret) |
| `dns_resolvers`, `propagation` | | resolvers to check the TXT record with, and how long to wait for it (`2m`) |
| `redirect_http` | `true` | send http:// to https:// |
| `hsts` | `false` | tell browsers to use https:// only, for a year. Turn it on once HTTPS works: turning HTTPS off later leaves browsers unable to reach the http:// address |
| `origin` | | the web app, as the worker reaches it (`LENS_HTTPS_ORIGIN`, else `LENS_TUNNEL_ORIGIN`: `http://frontend:3000` in Docker) |
| `https_port`, `http_port` | `8443`, `8080` | where Caddy listens in the container (install.sh publishes 443 and 80 to them) |

## How it runs

- **Caddy** is fetched into `data_dir/bin` the first time it's needed: Caddy's release from GitHub, checked against
  its published checksums, or, for a DNS provider, a build with that provider's plugin from Caddy's download service
  (`caddyserver.com/api/download`). A `caddy` on `PATH`, or `LENS_CADDY`, is used instead when it has the plugin.
- **Where**: only a process with `LENS_HTTPS_LISTEN=1` runs it. install.sh's `docker-compose.https.yml` gives the
  worker the ports and sets it; in a [microsandbox](microsandbox.md) it's the sandbox. Of those, one process holds a
  lease and runs Caddy; another takes over within 30 seconds if it stops.
- **What it keeps**: the certificates and the ACME account in `data_dir/https/caddy`, in the archive volume, so a
  rebuilt container doesn't ask for them again (certificate authorities limit how often). The configuration Lens
  writes, `data_dir/https/caddy.json`, holds the DNS provider's credentials, so it's readable only by Lens (mode 600)
  and passed by path, not on a command line.
- **Visitors**: Caddy passes the browser's `Host` on with `X-Forwarded-For` and `X-Forwarded-Proto: https`. The web
  app trusts `X-Forwarded-For` from the worker (`LENS_WEB_PROXY_HOSTS`), so throttles and IP groups see the visitor.
  Passkeys and sign-in follow the https:// address, and email links use the first domain unless
  `notifications.app_url` says otherwise.
- **Changes**: a saved change restarts Caddy with the new configuration within a few seconds.

By hand, with Docker:

```sh
docker compose -f docker-compose.prod.yml -f docker-compose.https.yml up -d
```

## Refine later

- Closing the ports again from Settings (`LENS_HTTPS=0` on the install line does it now).
- The install wizard asking for the domain (left out on purpose: it asks only what's needed to start).
- Caddy in the image, for machines that can't download it.
