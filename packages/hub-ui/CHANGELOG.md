# Changelog

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
