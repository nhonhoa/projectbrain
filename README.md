# pbrain

Project knowledge for AI coding agents.

Source code gets rewritten: by people, by agents, sometimes in another language. What a
project knows about itself should survive that: why a decision was made, which business rules
apply, where the code knowingly differs from the intent. pbrain keeps that knowledge as
markdown in git and gives agents a few guardrails to keep it true.

It follows the "LLM wiki" idea: raw sources stay untouched, the agent writes and maintains the
wiki, and a schema fixes its structure. In a software project the source code is one of the raw
sources.

- Python 3.11+, standard library only. No API key, no database, no external service. The review
  page is a small local server that pbrain starts by itself (see "Review page").
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
pip install git+https://github.com/nhonhoa/projectbrain   # or: uv tool install git+https://...
pbrain setup           # once per machine: start the review page now and at every login
cd your-project
pbrain init            # on a terminal: wizard (detects the project, Enter accepts the defaults)
                       # elsewhere (CI, pipes): report only, changes nothing
pbrain init --yes      # same wizard, no questions: take every default
pbrain init --all      # pbrain.toml, wiki skeleton, agent hooks, git pre-commit
pbrain init --rules CLAUDE.md
```

Each `init` flag installs one level and can be used alone:
`--config`, `--wiki`, `--hooks`, `--pre-commit`, `--rules FILE`.

Once it's installed, the page runs: after `pbrain setup` the review page is at `http://127.0.0.1:8765/` and
comes back at every login. `pbrain init` adds the project to the machine's project list
(`~/.pbrain/projects.json`) and the page shows it on the next reload, without a restart. Skipped
`setup`? Any pbrain command (`init`, the session-start hook, ...) starts the page when it is not
running; `PBRAIN_AUTOSERVE=0` turns that off. Details: [docs/review-service.md](docs/review-service.md).

To join a shared knowledge repo (see "Shared knowledge across projects"):

```
pbrain init --all --extends https://github.com/me/my-knowledge
```

`--extends` writes `extends` into `pbrain.toml` (creating it if needed), fetches the repo, and fills
in the wiki headings (`sections`), page limits and cases directory from the repo's `pbrain.toml`,
and uses its `SCHEMA.md` for the new wiki. Keys the project already sets are never overwritten, and
an existing `extends` line is left alone. It can be repeated. On a terminal, `init --config` or
`--all` asks for a repo when the project has none (not in hooks or CI; `--no-extends` skips the
question). It prints the line the owner of the shared repo adds to its `--roots-file` to include the
project on other machines; on this one the registry already lists it. pbrain never edits another
repository.

## Commands

```
pbrain reuse <keywords>          search the catalog and the code index by purpose
pbrain check [files]             everything below, for changed files; exit 1 on errors
pbrain wiki lint                 structure, links, drift against code
pbrain wiki touching <files>     wiki pages that cover these files
pbrain wiki confirm <page>       mark a page as re-verified today
pbrain setup [--uninstall]       start the review page now and at every login (once per machine)
pbrain serve [--status|--stop]   the review page server (`setup` runs it in the background)
pbrain projects [add|remove|prune] [ROOT]  the machine's project list shown on the page
pbrain review [ROOT ...]         open the review page (reuses the running one)
pbrain scan [ROOT ...] [--all]   report pending review items and what is new since the last scan
pbrain cases record <file.json>  run the current code and store its outputs
pbrain cases check               replay every case
pbrain sync                      fetch the shared knowledge repos
```

## Review page

A small web page on your machine (`http://127.0.0.1:8765/`) lists everything waiting for a human
decision, for every project registered on the machine, so you do not edit front matter by hand:

- pages whose `trust` is `requested`: asked for but never confirmed (approve, edit, deprecate),
- sections parked in `<wiki>/_inbox/*.md` (turn into a new page, append to a page, or discard),
- pages whose covered code drifted (confirm, show the diff, edit),
- every other lint finding (edit).

It is also where you read the wiki. **Read** lists every page, index first; links between pages
open in place and the browser's Back works. A `sources` or `covers` entry opens the file it names
when that file is tracked by git in one of the open projects (a GitHub blob URL maps to the project
whose origin is that repository), and references such as "item 12" in a page jump to that item.
The address bar always names what is open (`#<project>/<item>`), so you can paste it to someone.

Keys: `j`/`k` move, `a` approve or confirm, `e` edit, `d` discard or deprecate, `?` help. A
"changed files" button commits only the wiki directory. The page is local-only: it binds to
127.0.0.1, checks the `Host` and `Origin` headers, requires a per-run token on every write, and
never pushes.

Which projects it shows: every project in `~/.pbrain/projects.json` (`pbrain init` and the
session-start hook add theirs; `pbrain projects` lists and edits it), read again on each page load,
plus roots given to `pbrain serve ROOT ...` / `--roots-file`. A project whose `pbrain.toml` is gone
shows struck out until `pbrain projects prune`.

Who runs it: `pbrain setup` installs a login autostart (Windows: the per-user `Run` registry key;
macOS: a LaunchAgent; Linux: a systemd user unit or an XDG autostart entry) and starts a background
`pbrain serve --daemon`. Only one runs per user (an OS lock on `~/.pbrain/serve.lock`); its log is
`~/.pbrain/serve.log`; `pbrain serve --status` shows what serves the port. `pbrain review` opens the
running page, or starts a foreground one when none runs (`--port 0` for any free port, `--host`,
`--no-open`, `--no-registry`). See [docs/review-service.md](docs/review-service.md), which also
covers running it under a process manager such as WebHost.

## Scheduled scan

`pbrain scan` is the non-interactive twin of the review page, meant for a scheduler. For each
project it fetches the `extends` repos (`--no-sync` to skip), builds the review queue and compares
it with the previous scan, then prints per project the totals by kind, the new items and the ones
that are gone (`--json` for a machine-readable form). The result is stored in `--state`
(default `~/.cache/pbrain/scan-state.json`); `pbrain review` reads the same file, shows
"Last scan: ..." and marks new items. Exit code: 0 normally, 1 when a new lint error or drift
appeared, 2 for a usage error. Projects come from arguments, `--all` (every project in the
registry) or `--roots-file` (one directory per line; blank lines and `#` comments are ignored,
directories without `pbrain.toml` are skipped with a warning).

```
# Windows, every Monday 08:00
schtasks /Create /SC WEEKLY /D MON /ST 08:00 /TN "pbrain scan" /TR "pbrain scan --all"

# cron
0 8 * * 1  pbrain scan --all
```

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
skip_files = ["test_*.py", "tests_*.py", "*_test.py", "*_tests.py", "tests.py"]   # default

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

`covers` names functions, classes or module/class constants (`file.py::RULES`), not files, so a
page is flagged only when that definition changes.
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
