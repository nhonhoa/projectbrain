// Runs hub-ui.js (HubPager) and the UIDialog script of ui_dialog.html under node with a tiny DOM stub
// and prints JSON results. Usage: node run_pager_dialog.js <hub-ui.js> <ui_dialog.html>
// Not covered here (needs a real browser): HubPager click handling / markup in the DOM, the dialog look.
const fs = require("fs");
const vm = require("vm");

const noop = () => {};
function makeEl() {
    const el = {
        children: {}, listeners: {}, style: {}, textContent: "", innerHTML: "", className: "", value: "",
        classList: {
            set: new Set(),
            add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
            toggle(c, on) { if (on === undefined ? !this.set.has(c) : on) this.set.add(c); else this.set.delete(c); },
        },
        addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
        setAttribute: noop, appendChild: noop, focus: noop,
        // every selector maps to one persistent child, which is enough to inspect what was written
        querySelector(sel) { return (el.children[sel] = el.children[sel] || makeEl()); },
    };
    return el;
}
const body = makeEl();
const document = {
    readyState: "complete", addEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
    getElementById: () => null, createElement: () => makeEl(), body, documentElement: makeEl(), cookie: "",
};
const storage = { getItem: () => null, setItem: noop, removeItem: noop };
const window = { document, localStorage: storage, addEventListener: noop, matchMedia: () => ({ matches: false }) };
window.window = window;
const sandbox = Object.assign(window, { document, localStorage: storage, console, setTimeout: noop });
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const html = fs.readFileSync(process.argv[3], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script, sandbox);

const out = { ranges: [], lists: [], html: {}, dialog: {} };
for (const [total, size, page] of [[0, 50, 1], [1, 50, 1], [50, 50, 1], [51, 50, 1], [51, 50, 2], [51, 50, 99], [120, 50, 3],
    [120, 50, -4], [120, 50, "x"], [7, 0, 1]]) {
    out.ranges.push({ args: [total, size, page], r: sandbox.HubPager.range(total, size, page) });
}
for (let pages = 1; pages <= 30; pages++) {
    for (let page = 1; page <= pages; page++) out.lists.push({ page, pages, list: sandbox.HubPager.pageList(page, pages) });
}
const H = sandbox.HubPager;
out.html.single = H.html(H.range(10, 50, 1), []);
out.html.singleWithSizes = H.html(H.range(10, 50, 1), [50, 100]);
out.html.multi = H.html(H.range(120, 50, 2), [50, 100, 300]);

(async () => {
    const dlg = document.createElement("div");
    // UIDialog builds its own overlay through createElement; capture it to reach the children
    let overlay = null;
    document.createElement = () => (overlay = makeEl());
    const message = '<img src=x onerror=alert(1)> & "q"\nline two';
    const p = sandbox.UIDialog.alert(message, { detail: "<b>why</b>\nmore" });
    const msg = overlay.children["#ui-dialog-message"];
    const detail = overlay.children["#ui-dialog-detail"];
    out.dialog = {
        message: msg.textContent, messageInnerHtmlTouched: msg.innerHTML !== "",
        detail: detail.textContent, detailHidden: detail.classList.set.has("d-none"),
        whiteSpaceInTemplate: /id="ui-dialog-message" style="white-space:pre-(wrap|line)/.test(html),
    };
    overlay.children["#ui-dialog-ok"].listeners.click[0]();
    await p;
    const p2 = sandbox.UIDialog.confirm("plain");
    out.dialog.detailHiddenWhenAbsent = detail.classList.set.has("d-none");
    overlay.children["#ui-dialog-cancel"].listeners.click[0]();
    out.dialog.confirmResult = await p2;
    process.stdout.write(JSON.stringify(out));
})();
