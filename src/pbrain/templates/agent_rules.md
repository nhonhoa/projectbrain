## Project knowledge (pbrain)

Managed by `pbrain init`; edit outside the markers.

- **The wiki holds what the code does not show**: decisions and their reasons, business rules,
  known gaps between intent and code, gotchas. Index: `{wiki_dir}/index.md`; conventions:
  `{wiki_dir}/SCHEMA.md`. Read the relevant page before changing an area it covers. The code
  says what the system does; the wiki says why and what it should do. If they disagree, do not
  pick a side yourself: record the gap on the page and ask the user.
- **Reuse first**: before writing a function, class, component, template or helper, run
  `pbrain reuse <3-5 purpose keywords>` and use what it finds. Register a new shared component
  in the reuse catalog in the same change.
- **Before reporting done**: run `pbrain check`. Fix every ERROR. For each wiki DRIFT warning,
  re-read the page: still accurate, run `pbrain wiki confirm <page>`; otherwise update the page
  or record the gap.
- **Write knowledge as you go**: when the session produces a decision, a business rule or a
  gotcha, add or update a wiki page. Do not write pages that restate what the code does. Small
  fixes need no page; the commit message is enough.
- **Behavior cases** (`{cases_dir}/*.json`) pin down edge-case behavior of pure functions.
  A failing observed case means behavior changed by accident. To change behavior on purpose,
  record the decision as a wiki page first, then mark the case `decided`.
