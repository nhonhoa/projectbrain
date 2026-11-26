# Wiki conventions

The wiki keeps the project's **intent, business rules, decisions and lessons**. It does not restate
what the code does.

## Structure

- `index.md`: table of contents, one line per page: `- [Title](file.md) - one-sentence summary`,
  grouped by topic.
- `log.md`: change log, one line per ingest or edit: `## [YYYY-MM-DD] <ingest|edit|review> | <what>`.
- `_inbox/`: loose `## ` sections waiting to be filed into a page (no front matter). A whole new page
  waiting for approval goes directly in the wiki with `trust: requested`; lint warns about full pages
  in the inbox.
- `cases/`: behavior cases as JSON, see "Behavior cases" below.
- Every page is one `.md` file with a lowercase, hyphen-separated ASCII name, placed directly in
  the wiki directory.

## Front matter

```yaml
---
title: Invoice totals
type: concept        # concept | feature | rule | decision | contract | gotcha
status: active       # active | superseded | deprecated
trust: agreed        # agreed | requested | inferred
covers:              # code this page describes, by function: `file.py::name` or `file.py::Class.name`; may be empty
  - src/app/billing.py::Invoice.total
  - src/app/models.py::Customer
sources:             # raw sources ingested: a path from the repo root, a URL, or an id (commit hash, lesson id)
  - docs/specs/billing.md
verified: 2026-01-15 # last date the page was checked against the code
verified_hash: 0123456789abcdef # written by `pbrain wiki confirm`; do not edit by hand
superseded_by:       # only when status = superseded: file name of the replacing page
---
```

`covers` is declared per function so a page is flagged as drifted only when that function changes.
Declare a whole file (no `::`) only when the page is about the entire file, or the file is not
Python (template, script, short config). Lint takes the last commit up to the end of the `verified`
day as the baseline and compares its content with the current code. After re-checking a page
against the code, set `verified` to that day (`pbrain wiki confirm <page>`). Confirming also stores
`verified_hash`, a fingerprint of the covered code, so a page is not flagged while that code is
unchanged since the confirmation, committed or not.

A `sources` entry with no `/` and no file extension (a commit hash, a lesson id) is treated as an
external id and is not checked for existence. `http(s)://` URLs are skipped as well.

Trust levels:

- `agreed`: a person approved it beforehand or confirmed it afterwards (for example an approved
  spec).
- `requested`: a person asked for it, details not discussed.
- `inferred`: an agent decided it or read it back from the code. Anything visible only in the code
  belongs here.

A page mixing levels declares the lowest one and marks each lower-level claim `(inferred from
code)`.

## Page body

```markdown
## Intent
What we want, the business rule, acceptance criteria, and why.

## Reality
Point to the files, functions and tests that implement the intent. Do not re-describe the algorithm.

## Gaps
Where the code differs from the intent, and since when. If none, write "None recorded".

## Related
- depends on: [Other page](other-page.md)
```

Relation names used in "Related": `depends on`, `replaces`, `implemented by`, `conflicts with`,
`see also`. The headings are configurable in `pbrain.toml` (`[wiki] sections`).

## Writing rules

- Every business claim must trace to an entry in `sources` or a file in `covers`.
- When unsure, write it under "Gaps" or mark it "unverified"; do not guess. An "unverified" note
  must name the file and function to open to verify it.
- Decisions are never edited or deleted. A new decision is a new page; the old page becomes
  `status: superseded`.
- Keep pages short: under 80 lines. Split longer ones.

## Behavior cases

Fine-grained behavior (string normalization, rounding, empty values) is not written as prose in
wiki pages. It lives in `cases/<name>.json`: input/output pairs for pure functions, data only, so an
implementation in any language can replay them.

```json
{
 "format": "pbrain-cases/1",
 "target": "src/app/pricing.py",
 "python": {"module": "app.pricing", "env": {"APP_ENV": "test"}, "setup": "app.boot:init"},
 "cases": [
  {
   "id": "round_to_step-03",
   "fn": "round_to_step",
   "args": [29695],
   "kwargs": {"step": 10},
   "expected": 29700.0,
   "origin": "observed",
   "note": "The midpoint rounds up."
  }
 ]
}
```

The `python` block names the module to import. `env` values are set with `os.environ.setdefault`
before importing; `setup` (optional, `module:callable`) runs once before the target module is
imported. `"django_settings": "x.settings"` is accepted as a shortcut for a Django project.

- `origin: observed`: `expected` was recorded from the current code. Trust level `inferred`: it
  describes what the code does, not a requirement.
- `origin: decided`: `expected` is written by hand following a decision, and `decision` points to the
  wiki page recording it (path relative to the case file, for example `../page-name.md`). The tool
  never overwrites such a case.
- Values JSON cannot express are tagged: `{"$date": "2025-04-01"}`,
  `{"$datetime": "2025-04-01T08:30:00"}`, `{"$decimal": "1.50"}`. A function that raises has
  `expected` equal to `{"$error": "ExceptionClassName"}`. Tuples are stored as arrays; numbers
  compare by value (1 equals 1.0).
- Use only for pure functions. Anything tied to a database, UI or external service has no
  convention yet.

Commands (run from the project root):

```
pbrain cases record <cases-dir>/<name>.json            # fill expected for new cases
pbrain cases record <file> --refresh                    # rewrite observed cases, report changes
pbrain cases check                                      # replay every file in the cases directory
```

`check` treats a wrong `observed` case as an error (behavior changed unintentionally). A wrong
`decided` case means the code does not follow the decision yet: a warning unless `--strict` is
given. When behavior is changed on purpose, record the decision as a wiki page first, then switch the
related cases to `decided`; do not use `--refresh` to make an error go away.
