# Changelog

## 0.4.0

Additive, plus one display fix found on the new showcase page.

Added
- Showcase page `hub_ui/showcase.html` with every control in a fixed block (`#sc-*`) on static fake data:
  base CSS, number formatting, `data-hub-number`, `SearchCombobox`, `HubTabs`, `HubFilterBar`,
  `HubDate`/`HubPeriod` + `period_filter.html`/`date_range_filter.html`, `HubTable` + `empty_state.html`,
  `HubPager` + `pagination.html`, `UIDialog`. `?w=768` narrows the frame.
- `hub_ui.urls` (`include("hub_ui.urls")`) serving `showcase/`; 404 unless `DEBUG` or a staff user.
- `hub_ui.tools.showcase_snap`: headless Chrome/Edge screenshots (1280 and 768 wide), standard library only.
- Test guard: every template in `hub_ui/templates/hub_ui/` must be included in the showcase and every public
  JS module must appear in its script, so a new control cannot ship without being on the page.

Fixed
- `hub-ui-base.css` (since 0.3.0): the white text rule now only targets `.navbar .navbar-brand` and
  `.navbar .nav-link`. Before, `.nav-tabs`/`.nav-pills` labels inside pages were white on white.

## 0.3.0

Upgrade: to keep the 0.2 look, add `hub_ui/hub-ui-base.css` BEFORE `hub_ui/hub-ui.css` in your base
template. Without the base file you get stock Bootstrap plus the package components only.

Added
- `hub-ui-base.css`: the opinionated look (tokens, `html`/`body`, navbar, `.nav-link`, `.btn-primary`,
  `.card`, dropdowns incl. hover-open and submenus, menu quick search, print font size).
- `HubPager` (hub-ui.js): client-side pager for lists rendered in JS, same markup/classes as
  `pagination.html`. `HubPager.attach(container, {total, pageSize, page, sizes, onChange})` returns
  `{set(total, page?), get()}`; pure helpers `HubPager.range()`, `pageList()`, `html()`.
- `SearchCombobox.refresh(selectEl)`; `enhance()` now also re-reads placeholder / required / disabled
  whenever the select's options change (MutationObserver) or its form resets.
- `SearchCombobox` free-text create on `picker()` and `enhance()`: `create: true | {label}` plus optional
  `onCreate(text)` (returns a value, `{value, label}`, or a Promise; `null`/`false` cancels). On a
  `<select data-combobox data-create>` the new option is added and selected.
- `maxOptions` (alias of `maxItems`) to limit rendered suggestions; `data-max-options` on selects.
- `picker().configure({required, placeholder})`.
- `UIDialog.alert/confirm/prompt` accept `detail`: a longer, small, scrollable plain-text block.
- Tests: CSS selector coverage against a frozen copy of the 0.2.0 stylesheet, node tests for
  `HubPager` and `UIDialog` text handling.

Changed
- `hub-ui.css` now contains ONLY package components (`.hub-*`, `.sc-*`, `.gt-*`, dialog overlays, pager
  support). Every `--hub-*` variable has a fallback, so it works without the base file. `.num` keeps
  `!important` deliberately (it must beat Bootstrap `.text-start` / table alignment).
- Printing: the `.navbar` hide rule and the 12px print font size moved to the base file.
- `UIDialog` message is assigned with `textContent` (was escaped `innerHTML`); line breaks were and are kept.

## 0.2.0

Changed (behavior) - number formatting is now one rule, identical in Python (`format_vnd`,
`vn_number`, `format_number_vn`) and JS (`HubFmt.number/vnd/decimal`). Re-check these outputs:

- Rounding is half away from zero on the decimal as written (floats via their shortest repr, never
  the binary value): `2.5` -> `3` (Python was `2`), `1234.5` -> `1.235` (Python was `1.234`),
  `0.5` -> `1` (Python was `0`), `-2.5` -> `-3`, `1.005` (2 dec) -> `1,01` (Python was `1,00`),
  `2.675` (2 dec) -> `2,68` (Python was `2,67`), `0.125` (2 dec) -> `0,13` (Python was `0,12`).
  JS results for these were already as above.
- A result that rounds to zero has no sign: `-0.4` -> `0` and `-0.004` (2 dec) -> `0,00`
  (was `-0` / `-0,00`); `-0.5` -> `-1` (Python was `-0`).
- Empty input gives `""` on both sides: Python `None` was `"0"` / `"0,00"` and is now `""`
  (`format_vnd(None)`, `vn_number` on a missing value); JS already gave `""`.
- Non-numeric input gives `""` on both sides (was the input echoed back: `"abc"` -> `"abc"`,
  also for `HubFmt.decimal`). `nan`/`inf` and Vietnamese-format strings (`"1.234,5"`) are non-numeric.
- Big numbers: pass strings (or `Decimal` / `int` in Python) for exact results; a JS number above 2^53
  has already lost precision before it is formatted.
- Tests: the contract cases have no `agree` flag any more; every case must match on both sides.

## 0.1.0

- First release, extracted from an existing Django project without behavior changes.
- Static: `hub_ui/hub-ui.js`, `hub_ui/hub-ui.css`, `hub_ui/search-combobox.js`.
- Templates: `ui_dialog.html`, `empty_state.html`, `pagination.html`, `period_filter.html`,
  `date_range_filter.html`.
- Template tags: `format_vnd`, `vn_number`, `query_replace`.
- Tests: tag/template tests and a Python-vs-`HubFmt` number-formatting contract test.
