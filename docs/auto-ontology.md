# NVIDIA Auto-Ontology

Lens can connect an existing [NVIDIA Auto-Ontology](https://github.com/NVIDIA/auto-ontology) deployment to its
assistant. This optional integration searches the deployment's semantic catalog, checks whether a question is covered,
and asks questions that return an answer, generated SQL and result rows.

It connects to Auto-Ontology's **public web gateway REST API**, using the existing MCP extension controls in Lens.
It does not replace Lens's SurrealDB graph, extract an ontology from Lens's archive, install Auto-Ontology, or send the
archive to it. No extra package or service is added to the default Lens install.

## Connect

1. In your Auto-Ontology deployment, connect the relational data sources and compile the semantic layer. Follow
   [its setup guide](https://github.com/NVIDIA/auto-ontology#quickstart). Keep its backend and ingestion ports private.
2. Create an API token in Auto-Ontology's user menu, **API Tokens**, for an account with only the data access you need.
3. In Lens, as an admin, open **Extensions → Add MCP server → Ready-made → Set up NVIDIA Auto-Ontology**.
4. Enter `AUTO_ONTOLOGY_URL`, the public web **origin** (for example `https://ontology.example.com` or, on a trusted
   local network, `http://auto-ontology-frontend:3000`), and `AUTO_ONTOLOGY_API_TOKEN`. Do not add `/mcp`, `/api`, a
   query string, or a private backend/ingestion port. Use HTTPS outside a trusted local network: HTTP exposes the token
   to anyone who can observe that network.
5. Choose **Test**, then **Add** (or Enter). The test makes one bounded glossary read; it verifies gateway access,
   not semantic readiness. Ask the assistant to run `auto_ontology__check_readiness` before asking about the data.

The extension starts private. Its address, token, visibility, enabled state and per-tool switches are editable from
its page. Tokens are sealed at rest and masked in the API and manifests, using Lens's existing credential storage.
An agent can set it up with the `add_mcp_server` tool and `preset: auto_ontology`, supplying the same two values, or
through the [extensions API](assistant.md#adding-an-mcp-server).

Auto-Ontology's own MCP server uses interactive OAuth, which this bridge does not implement. Its MCP address cannot
be substituted for the public web origin, and a static API token cannot be used as its OAuth credential.

## Tools

| Tool | What it does |
|---|---|
| `auto_ontology__check_readiness` | Reads compilation status and source counts, without a model call. `ready: null` means the token cannot inspect every readiness check, not that the deployment is broken. |
| `auto_ontology__search_terms` | Searches business terms, with `query`, `limit` (1–100, default 20), and `offset` (0–10,000). |
| `auto_ontology__get_term` | Reads one exact term ID from search results, including relationships and mapped tables. |
| `auto_ontology__check_answerable` | Asks upstream which concepts cover `question`; coverage is guidance, not proof. May call its models. |
| `auto_ontology__ask_question` | Asks `question` and returns the upstream answer, SQL, source, and rows. `max_rows` is 1–100 (default 25); `row_count` and `truncated` describe the returned set. May call its models. |

For example: “Use Auto-Ontology to check whether the catalog covers quarterly revenue, then show revenue by region.
Include the SQL and say if any rows were left out.” The answer identifies the external deployment as its source.
Lens does not independently certify the generated SQL or execute it against its own database.

Questions are stateless: Lens does not send an upstream `conversation_id`, earlier chat messages, or evidence from
the archive automatically. Predictive queries are explicitly disabled (`prediction: false`), so the bridge does not
trigger Auto-Ontology's optional KumoRFM table uploads. There are no ingestion, compilation, mutation or arbitrary SQL
execution tools in this integration. The deployment's own models still process the question and its connected data.

## Access, safety and cost

**The configured API token owner supplies the upstream permissions.** Sharing the extension lets every Lens user who
can use it query the data that token can read. Lens namespace permissions do not become Auto-Ontology permissions.
Keep it private unless every recipient is authorized to read that external data; use separate least-privileged
tokens/extensions when different groups need different access. Revoking the token in Auto-Ontology stops its use.

The bridge reuses Lens's extension access checks, tool switches and third-party-content approval protections. Results
are untrusted external data. Only five fixed read operations are exposed. Tokens are bound to the extension, gateway
origin and adapter; redirects are refused rather than forwarding a credential. Raw upstream errors are not echoed.

Replies are capped at 2 MB. Questions have a 15-minute total processing limit checked between received chunks, plus
a 30-second network read timeout. A slow, failed or incomplete answer is reported without retrying it: the upstream
model or query may already have run. A malformed or oversized reply is not presented as a successful partial answer.

Calls appear in the activity ledger. Answerability and question calls are marked **unpriced**, since upstream does
not report reliable token usage or cost. Calls use Lens's model budget hook, but unpriced remote calls cannot be
reliably capped by a USD budget. Configure model limits and budgets in Auto-Ontology and its provider too.

This bridge targets the public contracts documented in upstream's
[API specification](https://github.com/NVIDIA/auto-ontology/blob/main/docs/openapi/auto-ontology-api.json) and
[conversation API](https://github.com/NVIDIA/auto-ontology/blob/main/docs/conversation-api.md). Contract tests use
local responses, not a live deployment or paid models. A deployment smoke test remains necessary when upgrading
Auto-Ontology: test credentials, readiness, term search and one known-answer question before sharing the extension.
