/*
SearchCombobox — ô gõ-tìm có danh sách gợi ý (nạp MỘT lần mỗi trang, sau hub-ui.js).

Ô input gõ-tìm có dropdown gợi ý, hỗ trợ tiếng Việt KHÔNG DẤU (client-side strip dấu,
server-side đã dùng collation AI), điều hướng bàn phím (↑ ↓ Enter Esc).

Nguồn dữ liệu gồm hai lớp, dùng riêng hoặc ghép:
  1. Danh sách TRONG BỘ NHỚ (localItems | preloadItems): bấm/focus vào ô là xổ ra, gõ là lọc
     tại chỗ (không dấu) — nhanh, không gọi server. Dành cho danh sách ít.
  2. Tìm LIVE trên server (fetchItems): chạy khi gõ đủ minChars, kết quả BƠM THÊM vào sau các
     mục trong bộ nhớ (bỏ trùng theo value). Dành cho danh mục lớn.

Cách dùng gọn nhất — nguồn đã đăng ký theo tên (SearchCombobox.register) + khai ô cần điền:
  SearchCombobox.attach(inputEl, {
    source: "item",                                       // tên đã đăng ký bằng SearchCombobox.register
    fill: { code: "#itemCode", name: hiddenNameEl },  // {khóa trong item.data: ô đích}
  })
Nguồn dùng lại: SearchCombobox.register("ten", {preloadItems, fetchItems, ...}) một lần,
hoặc SearchCombobox.fromApi({url, params, listKey, toItem}) để dựng hàm lấy dữ liệu từ API JSON.

API đầy đủ:
  SearchCombobox.attach(inputEl, {
    source: "ten",           // (tùy chọn) nguồn đã đăng ký; các option dưới đây ghi đè lên nó
    localItems: () => [{value, label, sub?, data?}, ...],   // mảng có sẵn trên trang (gọi lại mỗi lần tìm)
    preloadItems: async () => [items],                       // nạp MỘT lần rồi giữ trong bộ nhớ
    fetchItems: async (q) => [items],                        // tìm live, bơm thêm vào danh sách
    minChars: 2,             // số ký tự tối thiểu để tìm live (không áp cho danh sách trong bộ nhớ)
    maxItems: 30,
    fill: {key: el|selector},// chọn → el.value = item.data[key]; gõ lại/xóa → el.value = ""
    inputValue: (item) => "",// (tùy chọn) chữ điền vào Ô khi chọn; mặc định item.label. Ô lọc gửi
                             // mã lên server dùng: inputValue: (it) => it.value
    onSelect: (item) => {},  // chọn một gợi ý (chạy SAU fill)
    onClear: () => {},       // ô bị xóa trống / gõ lại (mất lựa chọn cũ)
    onFreeText: (text) => {},// (tùy chọn) blur với text không khớp gợi ý nào
    multiSeparators: /[;,+]/,// (tùy chọn) ô NHIỀU giá trị: chỉ tìm/thay đoạn sau dấu phân
                             // cách cuối cùng, các đoạn đã gõ giữ nguyên. Tên không có
                             // trong danh mục vẫn gõ tay được (không có onClear/onFreeText).
    createItem: {            // (tùy chọn) dòng "➕ thêm mới" ở CUỐI danh sách gợi ý — chỉ hiện
                             // khi đang gõ và chưa có gợi ý nào trùng khít.
      label: (q) => `➕ Thêm "${q}"`,
      onCreate: (q, insert) => {},   // insert(label) điền kết quả vừa tạo vào ô (hiểu nhiều giá trị)
    },
  })
  SearchCombobox.picker({...option của attach, input?, value, onChange(value, item), placeholder,
                         className, title, labelOf(value), required}) → {el, input, value, set(value)}
      Ô chọn MỘT giá trị thay <select> dài (giữ giá trị như select, gõ-tìm như attach). Ô không
      required có nút (x) bên phải khi đang có giá trị: bấm là về rỗng + onChange(""). el = khung bọc.
  <select data-combobox> — tự thành picker (SearchCombobox.enhance), <select> gốc ẩn nhưng giữ giá trị.
  SearchCombobox.stripVN(text) — bỏ dấu tiếng Việt, lowercase (dùng chung khi cần).
*/
window.SearchCombobox = (function () {
    const stripVN = (s) => HubFmt.stripVN(s);

    // Nguồn dùng lại theo tên ("concept"): attach(input, {source: "ten"}) lấy sẵn cách nạp dữ liệu.
    const SOURCES = {};
    // Gọi fn đúng một lần rồi dùng lại kết quả; lỗi thì lần sau thử lại.
    function once(fn) {
        let pending = null;
        return () => pending || (pending = Promise.resolve().then(fn).catch((e) => { pending = null; throw e; }));
    }
    function register(name, def) {
        SOURCES[name] = Object.assign({}, def, def.preloadItems ? { preloadItems: once(def.preloadItems) } : null);
    }
    // Hàm lấy mục từ API JSON: GET url?{params}&q= → data[listKey].map(toItem).
    // queryParam: tên tham số tìm kiếm khi API không dùng "q"; bỏ listKey khi API trả thẳng một mảng.
    function fromApi(cfg) {
        return async (q) => {
            const data = await HubApi.get(cfg.url, Object.assign({}, cfg.params, { [cfg.queryParam || "q"]: q }));
            const list = cfg.listKey ? (data && data[cfg.listKey]) : data;
            return (Array.isArray(list) ? list : []).map(cfg.toItem);
        };
    }

    function attach(input, opts) {
        opts = opts || {};
        if (opts.source && !SOURCES[opts.source]) throw new Error(`SearchCombobox: chưa đăng ký nguồn "${opts.source}"`);
        const src = SOURCES[opts.source] || {};
        opts = Object.assign({}, src, opts);
        const minChars = opts.minChars ?? (opts.fetchItems ? 2 : 0);
        const maxItems = opts.maxItems || 30;
        // Danh sách trong bộ nhớ: mảng của trang (localItems) hoặc nạp một lần (preloadItems).
        const preload = opts.preloadItems
            && (opts.preloadItems === src.preloadItems ? opts.preloadItems : once(opts.preloadItems));
        const hasMemory = !!(opts.localItems || preload);
        let preloaded = [];
        function memoryNow() { return (opts.localItems ? opts.localItems() : preloaded) || []; }
        async function memoryItems() {
            // Nạp sẵn lỗi (mất mạng, thiếu quyền) thì coi như danh sách rỗng — tìm live vẫn chạy.
            if (preload) preloaded = await preload().catch(() => []);
            return memoryNow();
        }
        const fillTargets = Object.entries(opts.fill || {})
            .map(([key, el]) => [key, typeof el === "string" ? document.querySelector(el) : el])
            .filter(([, el]) => el);
        function fill(data) {
            fillTargets.forEach(([key, el]) => { el.value = data ? (data[key] ?? "") : ""; });
        }
        // Chữ điền vào ô khi chọn một mục: mặc định là nhãn; inputValue dành cho ô phải nhận giá
        // trị khác nhãn (vd ô lọc gửi MÃ lên server).
        const textOf = (it) => String(opts.inputValue ? opts.inputValue(it) : it.label);

        // Dropdown gắn vào body với position:fixed theo vị trí input → không bị
        // overflow của bảng/modal cắt mất (vd .table-responsive)
        const box = document.createElement("div");
        box.className = "list-group shadow";
        box.style.cssText = "position:fixed; z-index:3000; max-height:240px; overflow-y:auto; display:none; font-size:0.85rem;";

        function place() {
            const r = input.getBoundingClientRect();
            box.style.left = `${r.left}px`;
            box.style.width = `${Math.max(r.width, 260)}px`;
            // Ô sát đáy màn hình (vd chọn số dòng/trang ở thanh phân trang): thiếu chỗ bên dưới
            // mà bên trên rộng hơn thì mở danh sách lên trên ô.
            const h = box.offsetHeight, below = window.innerHeight - r.bottom;
            box.style.top = `${h > below && r.top > below ? Math.max(0, r.top - h) : r.bottom}px`;
        }
        window.addEventListener("scroll", () => { if (box.style.display !== "none") place(); }, true);
        window.addEventListener("resize", () => { if (box.style.display !== "none") place(); });

        let items = [];
        let active = -1;
        let timeout = null;
        let liveTimeout = null;
        let seq = 0;            // lượt tìm mới nhất — kết quả của lượt cũ về trễ thì bỏ
        let typed = false;      // người dùng có gõ từ lần chọn gần nhất không (blur chỉ xử lý khi có)
        let lastFreeText = null;
        // Nhãn đang được chọn (hoặc giá trị có sẵn khi gắn) — blur với text này KHÔNG phải gõ tự do
        let selectedLabel = input.value.trim();
        // Ô nhiều giá trị: mọi thao tác tìm/chọn chỉ đụng tới đoạn sau dấu phân cách cuối.
        const multi = opts.multiSeparators || null;
        const sep = multi ? new RegExp(multi.source) : null;   // bỏ cờ /g để .test() không giữ trạng thái
        function segmentStart() {
            if (!sep) return 0;
            for (let i = input.value.length - 1; i >= 0; i--) {
                if (sep.test(input.value[i])) return i + 1;
            }
            return 0;
        }
        function currentQuery() {
            return multi ? input.value.slice(segmentStart()).trim() : input.value.trim();
        }

        // Hộp gợi ý chỉ nằm trong body lúc đang mở: trang dựng lại DOM liên tục (tạo ô mới mỗi lần
        // render) không để lại hộp mồ côi.
        function hide() { box.style.display = "none"; box.remove(); active = -1; }

        // Dòng "➕ thêm mới" — bỏ qua khi chưa gõ gì hoặc đã có gợi ý trùng khít nhãn.
        function createRow(q, found) {
            const cfg = opts.createItem;
            if (!cfg || !q) return null;
            if (found.some(it => stripVN(it.label) === stripVN(q))) return null;
            return {
                __create: true,
                query: q,
                label: typeof cfg.label === "function" ? cfg.label(q) : (cfg.label || `➕ Thêm "${q}"`),
            };
        }

        function renderList() {
            if (!items.length) { hide(); return; }
            box.innerHTML = items.map((it, i) => `
                <button type="button" class="list-group-item list-group-item-action py-1 ${i === active ? 'active' : ''} ${it.__create ? 'fw-semibold text-primary border-top' : ''}" data-idx="${i}">
                    ${it.label}${it.sub ? `<div class="small ${i === active ? '' : 'text-muted'}">${it.sub}</div>` : ""}
                </button>`).join("");
            if (!box.isConnected) document.body.appendChild(box);
            box.style.display = "";
            place();                                  // sau khi hiện mới đo được chiều cao để chọn mở lên/xuống
            box.querySelectorAll("button").forEach(btn => {
                btn.addEventListener("mousedown", (e) => {
                    e.preventDefault();
                    select(Number(btn.dataset.idx));
                });
            });
        }

        // Điền một nhãn vào ô: ô nhiều giá trị chỉ thay đoạn đang gõ, ô thường thay cả ô.
        function applyValue(label) {
            if (multi) {
                const head = input.value.slice(0, segmentStart());
                input.value = (head ? head + " " : "") + label;
                input.dispatchEvent(new Event("change", { bubbles: true }));
                return;
            }
            input.value = label;
            selectedLabel = label;
            lastFreeText = null;
        }

        function select(idx) {
            const it = items[idx];
            if (!it) return;
            hide();
            // Dòng "➕ thêm mới": không phải một gợi ý, mà là lệnh tạo bản ghi mới
            if (it.__create) {
                opts.createItem.onCreate(it.query, applyValue);
                return;
            }
            applyValue(textOf(it));
            typed = false;
            if (!multi) fill(it.data || it);
            if (opts.onSelect) opts.onSelect(it);
        }

        function show(found, q, keepActive) {
            if (document.activeElement !== input) return;   // kết quả về sau khi đã rời ô
            items = found.slice(0, maxItems);
            const extra = createRow(q, items);
            if (extra) items = items.concat([extra]);
            if (!keepActive) active = -1;
            renderList();
        }

        // live=false: chỉ lọc danh sách trong bộ nhớ (focus/bấm vào ô, và ngay khi gõ);
        // live=true: sau đó hỏi thêm server rồi bơm kết quả vào cuối danh sách đang hiện.
        async function query(live) {
            // Chưa gõ gì (giá trị đang có là lựa chọn cũ) thì mở TOÀN BỘ danh sách như một ô
            // chọn, không lọc theo chính nhãn đang hiển thị.
            const q = (typed || multi) ? currentQuery() : "";
            const mine = ++seq;
            const goLive = live && opts.fetchItems && q.length >= minChars;
            if (!hasMemory && !goLive) { hide(); return; }
            try {
                let found = [];
                if (hasMemory) {
                    const all = await memoryItems();
                    if (mine !== seq) return;
                    const nq = stripVN(q);
                    found = nq
                        ? all.filter(it => stripVN(`${it.label} ${it.sub || ""}`).includes(nq))
                        : all;
                    show(found, q);
                }
                if (goLive) {
                    const remote = await opts.fetchItems(q) || [];
                    if (mine !== seq) return;
                    const seen = new Set(found.map(it => String(it.value)));
                    show(found.concat(remote.filter(it => !seen.has(String(it.value)))), q, hasMemory);
                }
            } catch (e) { if (mine === seq && !hasMemory) hide(); }
        }

        input.addEventListener("input", () => {
            selectedLabel = null;
            typed = true;
            if (!multi) { fill(null); if (opts.onClear) opts.onClear(); }
            clearTimeout(timeout);
            clearTimeout(liveTimeout);
            if (hasMemory) timeout = setTimeout(() => query(false), 120);
            if (opts.fetchItems) liveTimeout = setTimeout(() => query(true), 300);
        });
        input.addEventListener("focus", () => {
            if (hasMemory) { clearTimeout(timeout); query(false); }
        });
        // Ô đang focus sẵn (vd vừa chọn xong, vừa bấm Esc) thì bấm lại vẫn xổ danh sách.
        input.addEventListener("click", () => {
            if (hasMemory && box.style.display === "none") query(false);
        });
        input.addEventListener("keydown", (e) => {
            if (box.style.display === "none") return;
            if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); renderList(); }
            else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); renderList(); }
            else if (e.key === "Enter") { if (active >= 0) { e.preventDefault(); select(active); } }
            else if (e.key === "Escape") { hide(); }
        });
        input.addEventListener("blur", () => {
            setTimeout(() => {
                hide();
                if (multi) return;      // ô nhiều giá trị: gõ tay tự do, không "chốt" lựa chọn
                // Chỉ xét khi người dùng CÓ gõ: giá trị do code đặt sẵn (mở đơn cũ) không phải lựa
                // chọn mới, không được kích hoạt onSelect/onFreeText chỉ vì lướt qua ô.
                if (!typed) return;
                const text = input.value.trim();
                if (!text || text === selectedLabel) return;
                // Gõ đúng nhãn một mục trong bộ nhớ (không cần bấm chọn) → coi như đã chọn
                const nt = stripVN(text);
                const hit = memoryNow().find(it => stripVN(it.label) === nt || stripVN(textOf(it)) === nt);
                if (hit) { items = [hit]; select(0); return; }
                if (opts.onFreeText && text !== lastFreeText) {
                    lastFreeText = text;
                    opts.onFreeText(text);
                }
            }, 150);
        });
    }

    // Ô chọn MỘT giá trị thay <select> dài: gõ-tìm như attach nhưng giữ giá trị như <select> — chỉ đổi
    // khi chọn một mục hoặc xóa trống ô; gõ dở rồi rời ô thì trả lại nhãn đang chọn.
    // opts = option của attach (source / localItems / fetchItems...) cộng thêm:
    //   input (ô có sẵn; bỏ trống = tạo <input> mới), value, onChange(value, item), placeholder,
    //   className, title, labelOf(value) (nhãn cho giá trị không có trong localItems, vd mã đối tượng),
    //   required (xóa trống rồi rời ô thì trả lại giá trị cũ — cho ô bắt buộc chọn một mục).
    // Ô không bắt buộc có nút (x) bên phải khi đang có giá trị: bấm là xóa về rỗng và gọi onChange("").
    // Ô được bọc trong <span class="sc-wrap"> (chỗ đặt nút x); ô có sẵn trên trang được bọc tại chỗ.
    // Trả {el (khung bọc — để gắn vào DOM), input, value, set(value)}.
    function picker(opts) {
        const input = opts.input || document.createElement("input");
        if (!opts.input) {
            input.type = "text";
            input.className = `form-control form-control-sm ${opts.className || ""}`.trim();
        }
        const wrap = document.createElement("span");
        wrap.className = opts.required ? "sc-wrap" : "sc-wrap sc-clearable";
        if (input.parentNode) input.replaceWith(wrap);
        wrap.appendChild(input);
        const clearBtn = document.createElement("button");
        clearBtn.type = "button";
        clearBtn.className = "sc-clear";
        clearBtn.tabIndex = -1;
        clearBtn.title = "Xóa chọn";
        clearBtn.setAttribute("aria-label", "Xóa chọn");
        clearBtn.innerHTML = '<i class="bi bi-x-circle-fill"></i>';
        if (!opts.required) wrap.appendChild(clearBtn);
        // (x) chỉ hiện khi ô có gì để xóa và đang dùng được.
        const refresh = () => { clearBtn.hidden = !!(input.disabled || (!value && !input.value)); };
        input.autocomplete = "off";
        if (opts.placeholder) input.placeholder = opts.placeholder;
        if (opts.title) input.title = opts.title;
        let value = "";
        let label = "";
        const labelOf = (v) => {
            if (!v) return "";
            const hit = (opts.localItems ? opts.localItems() : []).find((it) => String(it.value) === v);
            return hit ? hit.label : (opts.labelOf ? opts.labelOf(v) : v);
        };
        const set = (v) => { value = v == null ? "" : String(v); label = labelOf(value); input.value = label; refresh(); };
        const change = (v, it) => {
            if (v === value) return;
            value = v;
            refresh();
            if (opts.onChange) opts.onChange(v, it);
        };
        set(opts.value);
        attach(input, Object.assign({}, opts, {
            onSelect: (it) => { label = String(it.label); change(String(it.value), it); },
            onClear: null,
            onFreeText: null,
        }));
        // Chạy sau xử lý blur của attach (150ms) — lúc đó gõ đúng nhãn đã được tự chọn.
        input.addEventListener("blur", () => setTimeout(() => {
            if (!input.value.trim() && !opts.required) { label = ""; change("", null); }
            else if (input.value !== label) input.value = label;
            refresh();
        }, 200));
        input.addEventListener("input", refresh);
        // mousedown không lấy focus khỏi ô: ô đang gõ thì vẫn gõ tiếp được sau khi xóa.
        clearBtn.addEventListener("mousedown", (e) => e.preventDefault());
        clearBtn.addEventListener("click", () => {
            input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));   // đóng gợi ý nếu đang mở
            label = "";
            input.value = "";
            change("", null);
            refresh();
        });
        return { el: wrap, input, get value() { return value; }, set };
    }

    // Biến một <select> có sẵn (render phía server) thành combobox chuẩn: hiện ô gõ-tìm, còn <select>
    // gốc ẩn đi nhưng VẪN giữ giá trị — form submit, JS đọc/gán .value, sự kiện change, chip và
    // data-autosubmit của thanh lọc chạy như cũ. Option value "" thành placeholder; select required
    // (hoặc không có option rỗng) thì ô bắt buộc chọn. Tự áp cho mọi <select data-combobox>.
    const nativeValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    const nativeIndex = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");
    function enhance(select) {
        if (select._combobox) return select._combobox;
        const opts = () => Array.from(select.options);
        const empty = opts().find((o) => o.value === "");
        const required = select.required || !empty;
        const input = document.createElement("input");
        input.type = "text";
        input.className = select.className.replace(/\bform-select\b/g, "form-control").replace(/\bform-select-(sm|lg)\b/g, "form-control-$1");
        if (select.id) {
            input.id = `${select.id}__cb`;
            document.querySelectorAll(`label[for="${select.id}"]`).forEach((l) => { l.htmlFor = input.id; });
        }
        const p = picker({
            input, required, title: select.title || null,
            placeholder: empty ? empty.text.trim().replace(/^-+\s*|\s*-+$/g, "") : "",
            localItems: () => opts().filter((o) => o.value !== "").map((o) => ({ value: o.value, label: o.text.trim() })),
            value: select.value,
            onChange: (v) => {
                nativeValue.set.call(select, v);
                select.dispatchEvent(new Event("change", { bubbles: true }));
            },
        });
        input.required = select.required;
        select.required = false;           // select ẩn không nhận được focus khi trình duyệt báo thiếu
        input.disabled = select.disabled;
        select.hidden = true;
        select.after(p.el);
        // JS của trang gán .value / đổi option / reset form → ô hiển thị theo.
        const sync = () => { input.disabled = select.disabled; p.set(nativeValue.get.call(select)); };
        [["value", nativeValue], ["selectedIndex", nativeIndex]].forEach(([prop, native]) => {
            Object.defineProperty(select, prop, {
                configurable: true,
                get() { return native.get.call(this); },
                set(v) { native.set.call(this, v); sync(); },
            });
        });
        select.addEventListener("change", sync);
        new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "selected"] });
        if (select.form) select.form.addEventListener("reset", () => setTimeout(sync));
        sync();                            // select bị khóa sẵn → ẩn nút (x)
        select._combobox = p;
        return p;
    }
    document.querySelectorAll("select[data-combobox]").forEach(enhance);

    return { attach, register, fromApi, stripVN, picker, enhance };
})();
