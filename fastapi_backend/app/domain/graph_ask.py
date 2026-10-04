"""Questions about the graph in plain language: the language model writes read-only Cypher (cypher.py) from the graph's
schema and the names the question mentions, Lens runs it over the asker's own projection, and the answer comes back
with the query that produced it, so people can see (and edit) how it was found. A query that fails goes back to the
model once with the error.
"""

from __future__ import annotations

import json
import re

from . import cypher, graph_model, llm

# questions and the Cypher that answers them: shown to agents (GET /graph/schema) and to the model writing queries
EXAMPLES = [
    {"ask": "Who is mentioned most?", "cypher": "MATCH (e:Person) RETURN e.name, e.mentions ORDER BY e.mentions DESC LIMIT 10"},
    {
        "ask": "Which recordings mention Acme?",
        "cypher": "MATCH (r:Recording)-[m:MENTIONS]->(e:Entity) WHERE toLower(e.name) = 'acme' RETURN r.name, r.date, m.count ORDER BY r.date",
    },
    {
        "ask": "Who talks about Acme, and how often?",
        "cypher": "MATCH (s:Speaker)-[x:SAID]->(e:Entity {name: 'Acme'}) RETURN s.name, x.count ORDER BY x.count DESC",
    },
    {
        "ask": "What is discussed together with Acme?",
        "cypher": "MATCH (e:Entity {name: 'Acme'})-[w:MENTIONED_WITH]-(o:Entity) RETURN o.name, o.type, w.count ORDER BY w.count DESC LIMIT 20",
    },
    {
        "ask": "How is Alice connected to Acme?",
        "cypher": "MATCH p = shortestPath((s:Speaker {name: 'Alice'})-[*..6]-(e:Entity {name: 'Acme'})) RETURN [n IN nodes(p) | n.name] AS chain",
    },
    {
        "ask": "Everything in the Interviews collection",
        "cypher": "MATCH (c:Collection {name: 'Interviews'})-[:CONTAINS*1..8]->(r:Recording) RETURN r.name, r.date ORDER BY r.date DESC",
    },
]

SCHEMA = {
    "type": "object",
    "properties": {
        "cypher": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["cypher"],
}
SYSTEM = """You turn questions about a personal archive's knowledge graph into one read-only Cypher query.

The graph:
{schema}

Rules:
- Only MATCH, OPTIONAL MATCH, WHERE, WITH, UNWIND, RETURN, ORDER BY, SKIP, LIMIT, UNION. Never CREATE, MERGE, SET, DELETE, REMOVE or CALL.
- Every node has the properties id, name and namespace. Match names case-insensitively, e.g. toLower(e.name) = 'acme', or CONTAINS for part of a name. Prefer the exact names listed under "Names in the question".
- When the question asks which or who, return the nodes themselves (RETURN r, e) as well as the values people want to read, so they can be shown on the canvas.
- Use shortestPath((a)-[*..6]-(b)) for how things are connected; return the path.
- Add LIMIT 50 unless the question asks for a count or for everything.
- Reply with JSON: {{"cypher": "...", "explanation": "one short sentence on what the query looks for"}}.

Examples:
{examples}"""


def describe(g):
    """The schema as the model reads it: labels with properties, relationships with what they join."""
    s = graph_model.schema(g)
    lines = []
    for lb in s["labels"]:
        if lb["count"]:
            lines.append(f"(:{lb['label']}) {lb['count']} nodes; properties: {', '.join(lb['properties'])}")
    if s["entity_types"]:
        lines.append(f"Entity type labels (each Entity also has one): {', '.join(s['entity_types'])}")
    for r in s["relationships"]:
        if r["from_to"]:
            props = f" {{{', '.join(r['properties'])}}}" if r["properties"] else ""
            lines.append(f"[:{r['type']}{props}] {', '.join(r['from_to'])}")
    return "\n".join(lines)


def names_in(g, question, limit=12):
    """Nodes whose names the question mentions (whole words), longest names first, to ground the query."""
    q = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()) + " "
    hits = []
    for n in g.nodes.values():
        name = str(n.props.get("name") or "")
        key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if len(key) >= 3 and f" {key} " in q:
            hits.append((len(key), n))
    hits.sort(key=lambda x: -x[0])
    return [f"{n.props['name']} (:{n.labels[0]}{':' + n.labels[1] if len(n.labels) > 1 else ''}, id {n.id})" for _, n in hits[:limit]]


def ask(cfg, g, question, examples, model=None, max_rows=200):
    """{question, cypher, explanation, attempts, result} or LLMError / CypherError when no query worked."""
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("ask a question")
    system = SYSTEM.format(
        schema=describe(g),
        examples="\n".join(f"Q: {e['ask']}\nCypher: {e['cypher']}" for e in examples),
    )
    named = names_in(g, question)
    prompt = f"Question: {question}"
    if named:
        prompt += "\nNames in the question: " + "; ".join(named)
    attempts, last = [], None
    for _ in range(2):
        out = llm.json_out(cfg, system, prompt, SCHEMA, model)
        query = str(out.get("cypher") or "").strip().rstrip(";")
        attempts.append(query)
        try:
            result = cypher.run(g, query, max_rows=max_rows)
            return {
                "question": question,
                "cypher": query,
                "explanation": str(out.get("explanation") or "")[:500] or None,
                "attempts": len(attempts),
                "result": result,
            }
        except cypher.CypherError as e:
            last = e
            prompt += f"\n\nThis query failed:\n{query}\nError: {e}\nWrite a corrected query."
    raise cypher.CypherError(f"{last} (query: {json.dumps(attempts[-1])})")
