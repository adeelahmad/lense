# Secrets

Lens keeps the secrets it signs in with, such as model API keys, chat-app tokens and mail passwords, in Settings,
encrypted with the server's key ([Encryption](encryption.md)). That is the default, and it needs nothing set up.

A secret can be kept in a password manager instead. The field in Settings then holds a **reference** to the item,
such as `vaultwarden://OpenAI/password`, and Lens reads the value from the manager. A key changed in the manager
reaches Lens without anyone saving a setting. Lens stores only the reference. People and agents see the reference
and whether it could be read, but never the value.

| | |
|---|---|
| Built | References in Settings' secret fields; Vaultwarden (and Bitwarden) as the first password manager; Settings › Secrets in the web app, and a picker for the vault's items beside every secret field; the API, the assistant's tools and MCP tools for them |
| Planned | References in other stores of secrets (sources, MCP servers, sign-in providers); more password managers |

## References

A reference names the manager, the item and the field to read:

| Reference | Reads |
|---|---|
| `vaultwarden://OpenAI` | the item's default field: a login's password, a note's text, a card's number |
| `vaultwarden://OpenAI/password` | a login's `username`, `password`, `totp` (the key as it's kept), `uri` or `notes` |
| `vaultwarden://Visa/code` | a card's `number`, `code`, `cardholderName`, `brand`, `expMonth` or `expYear` |
| `vaultwarden://Slack bot/app token` | one of the item's own custom fields, by its name |

Item names match without regard to case, and field names exactly. A `/` or `%` in a name is written `%2F` or `%25`. An identity has
no default field, so its references name one, like `vaultwarden://Me/email`.

Any secret field in Settings (`llm.api_key`, `slack.bot_token`, `mail.password` and the rest) can hold a reference,
except the Secrets section's own sign-in. The environment still wins: a field set in `.env` ignores what Settings
holds.

A reference is checked when it's saved. If the manager isn't set up, or it has no such item or field, the save fails
and says why, so nothing is saved that can't be read.

## Vaultwarden

Lens reads from a [Vaultwarden](https://github.com/dani-garcia/vaultwarden) server, or from Bitwarden, as one account
of the vault:

1. Make an account for Lens on the vault. Its items are all that Lens can read, so keep your own account out of it.
2. Make a folder called `Lens` in it. Put the items Lens may read there, or share them into it. Lens reads that folder
   only.
3. In that account, open **Settings › Security › Keys › View API key**. Copy the `client_id` and `client_secret`.
4. In Lens, open **Settings › Secrets** (under Security and access). Set the vault's address, the account's email, the
   API key and the account's master password, save, then click **Test**. It signs in and counts the items in the
   folder. Agents do the same with `PUT /api/v1/settings/secrets` and `POST /api/v1/secrets/test`, or the admin
   assistant's `change_settings` (which asks first).

Then, in any secret field in Settings, **From Vaultwarden** lists the folder's items by name with their fields:
pick one, or type part of a name and press Enter for the first. The field fills with its reference, shown in plain
text since it isn't a secret, and saves like any change. A saved reference shows as **From Vaultwarden ·
vaultwarden://Item/field**, with when it was last read or why it couldn't be. Settings › Secrets lists every field
that refers to the vault, each linked to its place in Settings.

The vault encrypts its items end to end, so the server holds them only encrypted. The master password is what opens
them, so Lens needs it as well as the API key. Both are sealed with the server's key, like any other secret in
Settings.

The client is [python-vaultwarden](https://github.com/numberly/python-vaultwarden) (Apache-2.0). Lens fetches it as a
component (about 10 MB) the first time a Vaultwarden address is set, so the default install carries nothing for it
([Components](components.md)).

| Setting | Default | |
|---|---|---|
| `secrets.vaultwarden_url` | none | the vault's address, like `https://vault.example.com` |
| `secrets.vaultwarden_email` | none | the email of Lens's account on the vault |
| `secrets.vaultwarden_client_id` | none | that account's API key client id (`user.…`) |
| `secrets.vaultwarden_client_secret` | none | its client secret, kept encrypted |
| `secrets.vaultwarden_password` | none | its master password, kept encrypted |
| `secrets.vaultwarden_folder` | `Lens` | the one folder Lens reads; items elsewhere can't be referred to |
| `secrets.cache_minutes` | 10 | how long a value read is used before it's read again, 1–1440 minutes |

## How values are read

Lens reads a reference's value when it first builds its settings. It keeps the value in memory, never on disk, for
`secrets.cache_minutes`. After that, it reads the value again in the background. The old value keeps serving until the
new one arrives, and the settings are rebuilt when the value changed.

* If the vault can't be reached, or it refuses Lens's sign-in, each value read before stays in use. A reference that
  was never read stays empty and is tried again a minute later. Lens logs why.
* If an item is deleted, renamed, or moved out of the folder, Lens stops using its value at the next read. The field
  then shows as not read, with the reason.
* Saving the Secrets section drops every value and sign-in the saving process holds, so it reads them again. The
  other processes sign in again with a new API key or password the next time they read.

## Agents and the API

Agents can refer to secrets and check them, but they can't read them. These tools are for admins:

| Where | What |
|---|---|
| `GET /api/v1/secrets` | the password managers and whether each is set up; every secret field, whether it's set, and for a reference whether it was read, when, or why not |
| `GET /api/v1/secrets/items?q=` | the vault's items in Lens's folder: name, type, the fields that hold something, and the reference to each; never their values |
| `POST /api/v1/secrets/test` | sign in with the saved settings and count the items Lens may read |
| `PUT /api/v1/settings/<section>` | set a secret field to a value or a reference |
| MCP `list_secrets`, `list_vault_items` | the same as the first two ([MCP](mcp.md#tools)) |
| MCP `set_secret_reference` | point a secret field at a reference; checked against the vault before it's saved. Needs a write-scope token |
| Assistant `list_secrets`, `vault_items` | the same, in chat ([Assistant](assistant.md#setting-up-and-running-the-server-by-chat)) |

The assistant's `change_settings` can set the Secrets section too, and that change always waits for the admin's
approval. Its approval card shows a secret field's new reference, but never a typed secret.

## Security

* Lens's vault account, its API key and its master password open everything in the account. That is why Lens gets an
  account of its own, which holds only the items Lens needs, and reads one folder of it.
* The vault's decrypted keys, and the values read, are held in the memory of each Lens process that uses them
  (the API server and the workers). They are never written to disk or to the database.
* Settings, the API and the agents' tools show references, never values. A field filled from the vault is used where
  its typed value would be: someone who can change where Lens sends that secret (an admin) can still send it
  elsewhere, as they could with a typed one.
* The [threat model](https://github.com/adeelahmad/lense/blob/main/threat-model.md): P-SECRET-REFERENCES.

## Refine later

* References in the other stores of secrets: sources' credentials, MCP servers' environments, sign-in providers.
* A value read from the vault isn't checked the way a typed one is (the telemetry headers' format, for one). It's
  checked where it's used.
* A custom CA for a self-hosted vault with a private certificate.
* An environment variable for the master password, to keep it out of the database.
* More password managers (1Password, HashiCorp Vault, Infisical), each as a provider in `secret_providers.py`.
