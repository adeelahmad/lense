# Remote access (Cloudflare Tunnel)

Lens can make itself reachable from anywhere at an `https://` address, with nothing opened on your router: it runs
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), which makes an
outbound connection to Cloudflare, and Cloudflare serves the web app over https. Passkeys work there too, since
browsers allow them on https:// addresses.

It's off until an admin turns it on in **Settings › Remote access**. Everything is set there; nothing goes in `.env`.

## Three ways

| Tunnel | What you need | Address |
| --- | --- | --- |
| **Quick address** | Nothing | A random `https://<words>.trycloudflare.com`, new each time the tunnel starts. For trying things out. |
| **Your domain** | A domain on Cloudflare and an API token | Any hostname on it, like `lens.example.com` or `archive.lens.example.com`. Lens makes the tunnel, points it at the web app and adds the DNS record. |
| **Tunnel token** | A tunnel made in the Cloudflare dashboard (Zero Trust › Networks › Tunnels) | The public hostname you gave it there. Give the same hostname in Lens, so it knows its address. |

For **your domain**, make an API token at Cloudflare › My Profile › API Tokens with these permissions, for the zone
the hostname is on:

- Account › Cloudflare Tunnel › Edit
- Zone › DNS › Edit
- Zone › Zone › Read

Lens names the tunnel `lens-<hostname with dashes>`, routes the hostname to the web app (anything else gets a 404),
and adds a proxied CNAME record for it. It won't replace a record that isn't a tunnel's: if the hostname already has
an A record, say, it tells you, and you remove it or pick another name. The tunnel's own token is kept sealed in the
database; the API token is used again only when the hostname or the web app's address changes.

For **a tunnel token**, add a public hostname to the tunnel in the dashboard with the service
`http://frontend:3000` (the address Settings › Remote access shows), and paste the token from the tunnel's install
command (the long string after `--token`).

## What Lens does

- One server process runs cloudflared at a time (it holds a lease, renewed every few seconds; another takes over
  when it stops). Settings › Remote access shows the address, whether Cloudflare has the connection, and the last
  lines cloudflared printed.
- cloudflared is used from `PATH`, or downloaded from Cloudflare's GitHub releases into `<data_dir>/bin` the first
  time (Linux and macOS; amd64, arm64 and 32-bit ARM, so a Raspberry Pi works).
- The token goes to cloudflared in its environment, not on its command line.
- When cloudflared stops, Lens starts it again after 5 s, 15 s, 1 min, then every 5 min; changing a setting starts
  it again at once.
- The tunnel's address counts as one Lens is served at: passkeys and sign-in redirects use `https://<that host>`,
  and email links use the fixed hostname unless Settings › Notifications sets another address.

## Where it sends visitors

cloudflared reaches the web app at, in order: the **web app address** in Settings › Remote access, the
`LENS_TUNNEL_ORIGIN` environment variable (`http://frontend:3000` in the Docker Compose files), or `FRONTEND_URL`.

## Other ways to reach Lens

A reverse proxy you already run (Caddy, Traefik, nginx), Tailscale or WireGuard all work as well: add the address to
Settings › Access › Allowed hosts, and its network to Trusted proxies if it sets `X-Forwarded-For`.
