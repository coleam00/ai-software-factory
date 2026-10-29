---
name: factory-manager
description: Safely tend Archon attention items from explicit project policy.
---

# Factory manager

Run in periodic mode when invoked by `factory-manager`, or interactively when a person asks.

For queue discovery, launch, inspection, and lifecycle operations, load and follow the
`archon-cli` skill shipped with the matching Archon revision. This skill adds only the
factory's policy-bound attention behavior; it does not replace Archon's queue operating
instructions.

1. Run `archon workflow attention --json` and parse the current attention items.
2. Read all files in `.archon/sdlc/policy/`, including `maintainers.json` only for identity context.
3. For each answerable attention wait, answer only when one policy line clearly and directly selects one offered choice. Re-read the item immediately before writing, then use `archon workflow answer <run-id> --occurrence <occurrence-id> --choose <choice-id> --text <policy-based reason> --json`. For message forms, omit `--choose`; for multi-select forms, repeat it. Never answer gates through this rule, infer product scope, approve protected paths, resolve conflicting lines, or treat silence as permission.
4. Leave every ambiguous or uncovered item unanswered and put it in the report with its choices and why policy does not settle it.
5. Inspect run/event timestamps available from the CLI. Report a running run as stuck only after more than two hours with no new event. Never cancel, kill, retry, or mutate a run.
6. For every recurring decision shape observed, suggest one concise policy line that would settle it next time. Suggestions are report text only; never edit policy.
7. Report answered items with the exact policy line used, unanswered items, stuck runs, and policy suggestions. In interactive mode, show the same report before taking any additional action.

If the attention command or policy cannot be read reliably, answer nothing and report the error.
