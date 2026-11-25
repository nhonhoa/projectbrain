# hub-ui

A reusable Django app with the shared admin UI layer: a small vanilla-JS toolkit, one stylesheet
(on top of Bootstrap 5.3 and Bootstrap Icons), a few template components, and Vietnamese number
formatting (`1.234.567,89`) for templates. Vietnamese is the language of the UI strings.

## Install

```
pip install "hub-ui @ git+https://github.com/nhonhoa/projectbrain@hub-ui-v0.1.0#subdirectory=packages/hub-ui"
```

```python
INSTALLED_APPS = [
    # ...
    "django.contrib.staticfiles",
    "hub_ui",
]
```

## Base template

Bootstrap 5.3 and Bootstrap Icons must be loaded by your base template; `hub-ui.js` goes before page
content so inline page scripts can use it.

```django
{% load static %}
<link rel="stylesheet" href="{% static 'hub_ui/hub-ui-base.css' %}?v=1">   {# optional: the opinionated look #}
<link rel="stylesheet" href="{% static 'hub_ui/hub-ui.css' %}?v=1">        {# package components #}
<script src="{% static 'hub_ui/hub-ui.js' %}?v=1"></script>
...
{% include "hub_ui/ui_dialog.html" %}   {# once, in the base template #}
```

Two stylesheets, on purpose. `hub-ui-base.css` restyles plain Bootstrap (14px root font, blue navbar,
flat cards/buttons, hover-open dropdowns, `--hub-*` colour tokens): load it first for the full admin look
(this is what 0.2 gave you in one file). `hub-ui.css` holds only the components (`.hub-*`, `.sc-*`,
`.gt-*`, dialogs, pager) and falls back to built-in colours, so on its own it leaves the rest of a plain
Bootstrap page untouched. Override tokens (`--hub-primary`...) in your own CSS after the base file.

On pages that use type-ahead inputs (or `<select data-combobox>`, or the pagination page-size
picker) also load `<script src="{% static 'hub_ui/search-combobox.js' %}"></script>`.
Register your own data sources with `SearchCombobox.register(name, {...})`.

## What is inside

JS (`hub_ui/hub-ui.js`, all attached to `window`): `HubFmt` (number/date formatting),
`HubNumber` (number inputs), `HubState` (localStorage), `HubApi` (fetch with CSRF + errors),
`HubDom`, `HubFilterBar` (collapsible filter bar), `HubDate`, `HubPeriod` (month/range/day picker),
`HubTable` (viewport-fitting table, loading, empty state, sorting).

JS (`hub_ui/search-combobox.js`): `SearchCombobox` (`attach`, `picker`, `enhance`, `register`, `fromApi`).

CSS: `hub_ui/hub-ui-base.css` (look) and `hub_ui/hub-ui.css` (components).

Templates (`hub_ui/...`): `ui_dialog.html` (`UIDialog.alert/confirm/prompt/toast`),
`empty_state.html`, `pagination.html`, `period_filter.html`, `date_range_filter.html`.

Client-side pager for lists built in JS (same look as `pagination.html`; Vietnamese labels, page
sizes, `x-y / total`):

```js
const pager = HubPager.attach(document.getElementById("pager"), {
    total: rows.length, pageSize: 50, page: 1, sizes: [50, 100, 300],
    onChange({ page, pageSize }) { render(page, pageSize); },   // fires on user clicks / size change
});
pager.set(filtered.length);          // new total (keeps page, clamped); pager.set(total, 1) to jump
const { offset, pageSize } = pager.get();   // rows.slice(offset, offset + pageSize)
```

`<select data-combobox>` options filled by JS later are picked up automatically (placeholder, required,
disabled), or call `SearchCombobox.refresh(select)`. Free-text creation (TomSelect `create`):
`SearchCombobox.picker({..., create: true, onCreate: (text) => ({value: id, label: text})})`, or
`<select data-combobox data-create>`. `maxOptions` limits rendered suggestions.

`UIDialog.alert/confirm/prompt(message, {detail})`: `
` in `message` breaks lines, `detail` is an extra
small scrollable block; both are plain text (never parsed as HTML).

Number rounding (Python and JS alike): half away from zero on the decimal as written (`1.005` -> `1,01`,
`-2.5` -> `-3`), no `-0`, empty/non-numeric -> `""`.

Template tags (`{% load hub_ui %}`): filters `format_vnd`, `vn_number`; tag `query_replace`.

## Versioning

Semantic versions, one git tag per release: `hub-ui-vX.Y.Z`. Pin the tag in your requirements.
Never edit an installed copy of this package inside a consumer project: change it here, bump the
version, update `CHANGELOG.md`, tag, and upgrade the consumers.

## Tests

```
pip install -e . pytest
pytest -q
```

The number-formatting contract test runs the same recorded cases against the Python filters and
against `HubFmt` (needs `node`; skipped when missing).
