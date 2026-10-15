# Automations

Automations are the workflow studio: Lens follows what happens in every source as a stream of time-coded activities,
and an automation watches that stream and the clock, tests its conditions, and acts (directly for routine things,
through the agent, or by asking you first). The timeline is the same stream, filtered and reviewed.

Status: in progress. The studio is built in modules; this page labels each part built or planned, and only what is
merged is called built.

| Part | Status |
| --- | --- |
| Match trees: one condition language for triggers, an automation's If and workflow nodes | built |
| Activities, the kinds catalog, the event log, following the stream, the timeline API | planned |
| Automations, triggers, joins, schedules, actions, approvals, variables, the timeline page, the studio | planned |

## Contract

Every part of the studio builds on the shapes below. A change to one of them comes in a small pull request of its
own that changes the code, this section and the tests together.

### Match trees

Built (`app/domain/match.py`). One condition language for a trigger's `where`, an automation's `if`, and the
condition, filter and switch nodes of a workflow:

```
M := {"all": [M, …]} | {"any": [M, …]} | {"not": M} | {"path": "a.b.0", "op": OP, "value": V}
```

- A test (`{path, op, value}`) reads the part of the value at a dotted path, list items by number
  (`object.from.0`); no path tests the value itself. The tests are the workflow engine's: `exists`, `empty`,
  `equals`, `not_equals`, `contains` (text in any case, or an item of a list), `gt` and `lt` (numbers).
- `all` holds when every condition in it does (an empty `all` always holds), `any` when one does (it needs at least
  one), `not` when its condition doesn't.
- Values are literals, never templates, so matching stays cheap and safe.
- A tree nests at most 6 deep and holds at most 50 tests. A problem is named with where it is: `the condition,
  all.1.not: compare with a number`.
- `match.describe(tree)` reads a tree in words: `object.from contains “@stmarys.org” and (object.subject contains
  “trip” or object.subject contains “outing”)`.

In a workflow, a condition, filter or switch case takes either one test, as before, or a tree in `match`; with a
tree, `path` (when given) is the part of the value the tree tests. Graphs saved before keep running unchanged.

```json
{"id": "school", "type": "condition",
 "config": {"path": "object", "match": {"all": [{"path": "from.0", "op": "contains", "value": "@stmarys.org"},
                                                {"not": {"path": "attachments", "op": "gt", "value": 5}}]}}}
```

## Refine later

- More operators: `in`, `matches` (bounded regular expressions), `between`, times against now, `changed`.
- The canvas shows and edits match trees on condition, filter and switch nodes (today it edits one test; a tree
  saved through the API is kept).
