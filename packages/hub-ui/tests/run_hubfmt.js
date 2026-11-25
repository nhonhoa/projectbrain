// Runs hub-ui.js under node with a tiny stub of window/document and evaluates HubFmt on
// a list of cases. Usage: node run_hubfmt.js <path to hub-ui.js> < cases.json  (JSON on stdout)
const fs = require("fs");
const vm = require("vm");

const noop = () => {};
const stubEl = () => ({ addEventListener: noop, classList: { add: noop, remove: noop, toggle: noop } });
const document = {
    readyState: "complete",
    addEventListener: noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement: stubEl,
    body: stubEl(),
    documentElement: stubEl(),
    cookie: "",
};
const storage = { getItem: () => null, setItem: noop, removeItem: noop };
const window = { document, localStorage: storage, addEventListener: noop, matchMedia: () => ({ matches: false }) };
window.window = window;
const sandbox = Object.assign(window, { document, localStorage: storage, console });
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((c) => ({
    number: sandbox.HubFmt.number(c.value, c.decimals),
    vnd: sandbox.HubFmt.vnd(c.value),
}));
process.stdout.write(JSON.stringify(out));
