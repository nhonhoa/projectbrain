# pbrain

Project knowledge for AI coding agents.

Source code gets rewritten: by people, by agents, sometimes in another language. What a
project knows about itself should survive that: why a decision was made, which business rules
apply, where the code knowingly differs from the intent. pbrain keeps that knowledge as
markdown in git and gives agents a few guardrails to keep it true.

It follows the "LLM wiki" idea: raw sources stay untouched, the agent writes and maintains the
wiki, and a schema fixes its structure. In a software project the source code is one of the raw
sources.

- Python 3.11+, standard library only. No API key, no database, no service to run. (`pbrain review` is an optional local web page you start when you want it.)
- The tool is public; your knowledge stays in your own repositories.
- Works for a single project. Several projects can share one knowledge repo.

## What it does

| Part | Purpose |
|---|---|
| **Wiki** | One page per concept, rule, decision or gotcha, with intent, pointers to code, and known gaps. `pbrain wiki lint` finds broken links, orphan pages and pages whose covered functions changed since they were last verified. |
| **Behavior cases** | Input/output pairs for pure functions, stored as JSON. They pin down edge cases that prose cannot, and they can be replayed against an implementation in any language. |
| **Reuse first** | A catalog of shared components plus a full-text index of the code, so an agent searches by purpose before writing something new. A hook blocks edits that re-implement a catalogued component. |
| **Hooks** | Session start, before each edit, and when the agent reports done. `pbrain check` runs the same checks in pre-commit and CI. |
| **Shared knowledge** | `extends` points at one or more knowledge repos used by several projects. |

What the wiki is for: decisions and their reasons, business rules, gaps between documents and
code, gotchas. What it is not for: restating what the code does. Reading the code tells you
that.

## Install

```
pip install git+https://github.com/nhonhoa/projectbrain
cd your-project
pbrain init            # report only: shows what exists, changes nothing
pbrain init --all      # pbrain.toml, wiki skeleton, agent hooks, git pre-commit
pbrain init --rules CLAUDE.md
```

Each `init` flag installs one level and can be used alone:
`--config`, `--wiki`, `--hooks`, `--pre-commit`, `--rules FILE`.

## Commands

```
pbrain reuse <keywords>          search the catalog and the code index by purpose
pbrain check [files]             everything below, for changed files; exit 1 on errors
pbrain wiki lint                 structure, links, drift against code
pbrain wiki touching <files>     wiki pages that cover these files
pbrain wiki confirm <page>       mark a page as re-verified today
pbrain review [ROOT ...]         local page to clear pending decisions with one click
pbrain cases record <file.json>  run the current code and store its outputs
pbrain cases check               replay every case
pbrain sync                      fetch the shared knowledge repos
```

## Review page

`pbrain review` starts a small web page on your machine (default `http://127.0.0.1:8765/`) that
lists everything waiting for a human decision, so you do not edit front matter by hand:

- pages whose `trust` is `requested`: asked for but never confirmed (approve, edit, deprecate),
- sections parked in `<wiki>/_inbox/*.md` (turn into a new page, append to a page, or discard),
- pages whose covered code drifted (confirm, show the diff, edit),
- every other lint finding (edit).

Keys: `j`/`k` move, `a` approve or confirm, `e` edit, `d` discard or deprecate, `?` help. A
"changed files" button commits only the wiki directory. The page is local-only: it binds to
127.0.0.1, checks the `Host` and `Origin` headers, requires a per-run token on every write, and
never pushes. Several projects can be reviewed in one page: `pbrain review ../other-project`
(each extra root needs a `pbrain.toml`). Options: `--port` (0 = any free port), `--host`,
`--no-open`.

## Configuration

`pbrain.toml` at the project root. Every key is optional.

```toml
project = "my-app"
extends = ["https://github.com/me/my-knowledge"]

[wiki]
dir = "docs/wiki"
sections = ["## Intent", "## Reality", "## Gaps", "## Related"]   # translate if you like
max_lines = 80
max_age_days = 180

[cases]
dir = "docs/wiki/cases"

[reuse]
catalog = "tools/reuse_catalog.py"   # Python file exposing REUSE_CATALOG
roots = ["src"]                      # directories to index
summaries = ""                       # optional JSON of one-line symbol descriptions

[check]
ruff = true
```

## Wiki pages

```markdown
---
title: Invoice rounding
type: rule            # concept | feature | rule | decision | contract | gotcha
status: active        # active | superseded | deprecated
trust: agreed         # agreed | requested | inferred
covers:
  - src/app/billing.py::Invoice.total
sources:
  - docs/specs/billing.md
verified: 2026-01-15
---
```

`covers` names functions, not files, so a page is flagged only when that function changes.
`pbrain wiki confirm` records a fingerprint of the confirmed code (`verified_hash`), so a
confirmation holds even when the change is not committed yet.
`trust` records where a statement comes from: agreed with the user, requested without
discussion, or inferred from code. Anything read back from code is `inferred`, and is not a
requirement when the system is rewritten.

Decisions are never edited or deleted. A new decision is a new page, and the old one becomes
`superseded`.

## Behavior cases

```json
{
 "format": "pbrain-cases/1",
 "target": "src/app/money.py",
 "python": {"module": "app.money"},
 "cases": [
  {"id": "round_to_step-03", "fn": "round_to_step", "args": [29695],
   "expected": 29700.0, "origin": "observed", "note": "Midpoints round up."}
 ]
}
```

- `observed`: recorded from the current code. It describes behavior, not a requirement.
- `decided`: written by hand from a decision page that it links to. Never overwritten.

A failing observed case means behavior changed by accident. A failing decided case means the
code does not follow the decision yet.

## Rewriting a module

Give the agent three things: the old code, the decision pages, and the cases. Keep behavior
wherever no decision says otherwise, even where it looks like a bug; change behavior only where
a decision page exists; replace the old module only when every case passes.

## Shared knowledge across projects

A knowledge repo is an ordinary pbrain project: `pbrain.toml`, a wiki, optionally a reuse
catalog. Projects list it under `extends`. `pbrain reuse` shows the project's own results
first, then the shared ones. Keep it in a private repository if it describes private systems.

## Design rules

1. Knowledge outlives source code.
2. Knowledge is markdown in git. Every tool only reads, searches and checks it.
3. Automation is the default: hooks and pre-commit do the repetitive part.
4. Hooks fail open. A broken hook never blocks the work.
5. No LLM calls at lookup time, so results are stable and testable.
6. Just enough. Nothing is bundled unless it measurably helps.

## Development

```
pip install -e . pytest ruff
pytest
ruff check .
```

## License

MIT. See [LICENSE](LICENSE). Contributions are welcome under the same license.
