/* ==========================================================================
   Hub UI — JS dùng chung cho giao diện quản trị (nạp bằng {% static 'hub_ui/hub-ui.js' %}).

   Module (đều gắn vào window):
     HubFmt        number / decimal / parseNumber / vnd / date / stripVN — MỘT chỗ định dạng kiểu VN ở client.
     HubNumber     ô nhập số có phân cách ngay khi gõ: input[data-hub-number] + get(el) / set(el, v).
     HubState      nhớ trạng thái nhỏ theo trang trong localStorage (an toàn khi bị chặn).
     HubApi        fetch JSON có CSRF, ném HubApi.Error({status, message}), toast lỗi chuẩn; deleteItem xóa dòng.
     HubDom        loadImage, toggleWhen (ẩn/hiện khối theo select), filterRows (lọc dòng không dấu), selectAll.
     HubFilterBar  thanh lọc thu gọn/mở rộng, chip tóm tắt, nhớ trạng thái, nút Xem có spinner.
     HubPeriod     chọn kỳ: month (◀ tháng/năm ▶), range (preset + từ/đến), day (◀ ngày ▶).
     HubTable      bảng: vừa viewport, loading overlay, empty-state, sort client theo th[data-sort].
   Tự khởi tạo theo data-attribute khi DOMContentLoaded:
     [data-hub-filterbar], [data-hub-period], [data-hub-table]
   Nút xóa bản ghi: <button data-hub-delete="/api/.../delete/" data-confirm="Xóa X?" data-success="Đã xóa X." data-row="row-id">
   ========================================================================== */
(function () {
    "use strict";

    // ------------------------------------------------------------------ HubFmt
    // Làm tròn: half away from zero trên số thập phân NHƯ ĐÃ VIẾT (1.005 → "1,01", 2.5 → "3", -2.5 → "-3"),
    // không qua nhị phân của float; kết quả về 0 thì không có dấu. Rỗng/null/không phải số → "".
    // Giống hệt hub_ui.formatting (Python). Số JS lớn hơn 2^53 đã mất độ chính xác trước khi vào đây —
    // muốn chính xác tuyệt đối hãy truyền chuỗi số (vd "12345678901234567890.125").
    const NUM_RE = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;
    const MAX_EXP = 400; // mũ vượt ngưỡng coi như không phải số (cùng ngưỡng với Python)

    /** Số/chuỗi → {neg, int, frac} (chuỗi chữ số, không mũ) hoặc null nếu rỗng/không phải số. */
    function decParts(v) {
        let s;
        if (v === null || v === undefined) return null;
        if (typeof v === "boolean") s = v ? "1" : "0";
        else if (typeof v === "number") { if (!Number.isFinite(v)) return null; s = String(v); }
        else if (typeof v === "string") s = v.trim();
        else return null;
        const m = NUM_RE.exec(s);
        if (!m || (m[2] === "" && !m[3])) return null;
        let int = m[2], frac = m[3] || "";
        const exp = m[4] ? parseInt(m[4], 10) : 0;
        if (Math.abs(exp) > MAX_EXP) return null;
        if (exp > 0) {
            frac = frac.padEnd(exp, "0");
            int += frac.slice(0, exp);
            frac = frac.slice(exp);
        } else if (exp < 0) {
            int = int.padStart(-exp, "0");
            frac = int.slice(int.length + exp) + frac;
            int = int.slice(0, int.length + exp);
        }
        return { neg: m[1] === "-", int, frac };
    }

    /** Làm tròn half away from zero về dec số lẻ; trả { neg, int, frac } với frac dài đúng dec. */
    function roundParts(p, dec) {
        let int = p.int, frac = p.frac;
        if (frac.length <= dec) {
            frac = frac.padEnd(dec, "0");
        } else {
            const up = frac.charCodeAt(dec) >= 53; // chữ số kế tiếp >= 5
            const digits = (int + frac.slice(0, dec)).split("");
            if (up) {
                let i = digits.length - 1;
                while (i >= 0 && digits[i] === "9") { digits[i] = "0"; i--; }
                if (i < 0) digits.unshift("1"); else digits[i] = String(Number(digits[i]) + 1);
            }
            const all = digits.join("");
            int = all.slice(0, all.length - dec);
            frac = all.slice(all.length - dec);
        }
        int = int.replace(/^0+(?=\d)/, "");
        if (int === "") int = "0";
        const zero = /^0*$/.test(int + frac);
        return { neg: p.neg && !zero, int, frac };
    }

    function joinVN(p, frac) {
        return (p.neg ? "-" : "") + p.int.replace(/\B(?=(?:\d{3})+(?!\d))/g, ".") + (frac ? "," + frac : "");
    }

    const HubFmt = {
        /** 1234567.891 → "1.234.567,89" (dec số lẻ, mặc định 0; luôn đủ dec số lẻ). Rỗng/không phải số → "". */
        number(v, dec = 0) {
            const p = decParts(v);
            if (!p) return "";
            const r = roundParts(p, dec);
            return joinVN(r, r.frac);
        },
        /** Tiền VND: làm tròn 0 lẻ, không ký hiệu. */
        vnd(v) { return HubFmt.number(v, 0); },
        /** Số có phân cách, GIỮ phần lẻ thực có (tối đa maxDec, không ép thêm số 0): 1234.5 → "1.234,5",
         *  1234 → "1.234". Cho số lượng / đơn giá và giá trị hiện trong ô HubNumber. Rỗng/không phải số → "". */
        decimal(v, maxDec = 2) {
            const p = decParts(v);
            if (!p) return "";
            const r = roundParts(p, maxDec);
            return joinVN(r, r.frac.replace(/0+$/, ""));
        },
        /** Ngược của number/decimal: "1.234.567,89" → 1234567.89. Rỗng / không phải số → null. */
        parseNumber(text) {
            const s = String(text ?? "").replace(/[^\d,-]/g, "").replace(",", ".");
            const n = s === "" ? NaN : Number(s);
            return Number.isNaN(n) ? null : n;
        },
        /** "2026-09-03" | Date → "03/09/2026". Chuỗi không parse được trả nguyên. */
        date(v) {
            if (!v) return "";
            const d = v instanceof Date ? v : new Date(String(v).length === 10 ? v + "T00:00:00" : v);
            if (Number.isNaN(d.getTime())) return String(v);
            const p = (x) => String(x).padStart(2, "0");
            return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()}`;
        },
        /** "2026-09-03" → "03/09/26" (ô ngày gọn). */
        date2(v) {
            const d = HubFmt.date(v);
            return d.length === 10 ? d.slice(0, 6) + d.slice(8) : d;
        },
        /** Date → "yyyy-mm-dd" (giá trị cho input[type=date]). */
        isoDate(d) {
            const p = (x) => String(x).padStart(2, "0");
            return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
        },
        /** Bỏ dấu tiếng Việt + lowercase để so khớp không dấu. */
        stripVN(s) {
            return String(s ?? "").toLowerCase().normalize("NFD")
                .replace(/[̀-ͯ]/g, "").replace(/đ/g, "d");
        },
        /** Escape cho HTML text lẫn giá trị thuộc tính (& < > " '). */
        escapeHtml(s) {
            return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
                .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
        },
    };

    // ---------------------------------------------------------------- HubState
    const HubState = {
        get(key, fallback = null) {
            try {
                const raw = window.localStorage.getItem("hub." + key);
                return raw === null ? fallback : JSON.parse(raw);
            } catch (e) { return fallback; }
        },
        set(key, value) {
            try { window.localStorage.setItem("hub." + key, JSON.stringify(value)); } catch (e) { /* bị chặn */ }
        },
        remove(key) {
            try { window.localStorage.removeItem("hub." + key); } catch (e) { /* bị chặn */ }
        },
    };

    // ------------------------------------------------------------------ HubTabs
    // Tab chọn theo #hash trên URL (gửi link là mở đúng tab), không có #hash thì lấy tab nhớ trong trình duyệt.
    //   const tabs = HubTabs.hash(["a", "b"], { store: "trang.tab", onChange: (k) => render() });
    //   tabs.current → tab đang chọn; tabs.select("b") → đổi tab (ghi #b lên URL + nhớ + gọi onChange).
    const HubTabs = {
        hash(keys, { store = null, fallback = keys[0], onChange = null } = {}) {
            const ok = (k) => keys.includes(k);
            const fromHash = () => { try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return ""; } };
            const write = (k) => {
                if (fromHash() !== k) history.replaceState(history.state, "", `${location.pathname}${location.search}#${encodeURIComponent(k)}`);
            };
            let cur = ok(fromHash()) ? fromHash() : (store ? HubState.get(store, fallback) : fallback);
            if (!ok(cur)) cur = fallback;
            write(cur);
            const ctl = {
                get current() { return cur; },
                select(k) {
                    if (!ok(k)) return;
                    cur = k;
                    if (store) HubState.set(store, k);
                    write(k);
                    if (onChange) onChange(k);
                },
            };
            window.addEventListener("hashchange", () => { const k = fromHash(); if (ok(k) && k !== cur) ctl.select(k); });
            return ctl;
        },
    };

    // ------------------------------------------------------------------ HubApi
    class HubApiError extends Error {
        constructor(message, status, data) { super(message); this.status = status; this.data = data; }
    }
    const HubApi = {
        Error: HubApiError,
        csrf() {
            // CSRF_USE_SESSIONS=True: token nằm trong session, KHÔNG có cookie csrftoken hợp lệ.
            // Ưu tiên token do server render vào trang; cookie chỉ là fallback cuối (có thể là
            // cookie cũ/của app khác cùng hostname -> "CSRF token incorrect").
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.content) return meta.content;
            const el = document.querySelector("[name=csrfmiddlewaretoken]");
            if (el && el.value) return el.value;
            const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
            return m ? decodeURIComponent(m[1]) : "";
        },
        /** GET url?params → JSON. params: object (bỏ giá trị null/undefined/""). */
        async get(url, params) {
            return HubApi.request(HubApi.withQuery(url, params), { method: "GET" });
        },
        /** POST JSON body → JSON. */
        async post(url, body, opts = {}) {
            return HubApi.request(url, {
                method: opts.method || "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body ?? {}),
            });
        },
        /** POST FormData (upload) → JSON. */
        async postForm(url, formData) {
            return HubApi.request(url, { method: "POST", body: formData });
        },
        withQuery(url, params) {
            if (!params) return url;
            const q = new URLSearchParams();
            Object.entries(params).forEach(([k, v]) => {
                if (v === null || v === undefined || v === "") return;
                if (Array.isArray(v)) v.forEach((x) => q.append(k, x)); else q.append(k, v);
            });
            const s = q.toString();
            return s ? url + (url.includes("?") ? "&" : "?") + s : url;
        },
        /** Như request nhưng KHÔNG ném khi HTTP lỗi: trả {ok, status, data} để trang tự xử lý từng mã.
         *  Chỉ ném HubApiError (status 0) khi mất kết nối. */
        async send(url, init = {}) {
            const headers = Object.assign({ "X-Requested-With": "XMLHttpRequest" }, init.headers || {});
            if ((init.method || "GET").toUpperCase() !== "GET") headers["X-CSRFToken"] = HubApi.csrf();
            let res;
            try {
                res = await fetch(url, Object.assign({ credentials: "same-origin" }, init, { headers }));
            } catch (e) {
                throw new HubApiError("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", 0, null);
            }
            const ct = res.headers.get("content-type") || "";
            const data = ct.includes("json") ? await res.json().catch(() => ({})) : await res.text().catch(() => "");
            return { ok: res.ok, status: res.status, data };
        },
        /** Gọi API, trả data đã parse; HTTP lỗi → ném HubApiError(message, status, data). */
        async request(url, init = {}) {
            const { ok, status, data } = await HubApi.send(url, init);
            if (!ok) {
                const msg = (data && typeof data === "object" && (data.error || data.detail || data.message))
                    || (status === 403 ? "Bạn không có quyền thực hiện thao tác này."
                        : status === 404 ? "Không tìm thấy dữ liệu yêu cầu."
                            : `Máy chủ trả lỗi ${status}.`);
                throw new HubApiError(String(msg), status, data);
            }
            return data;
        },
        /** Xóa một bản ghi bằng DELETE: hỏi xác nhận → gọi API → gỡ dòng #rowId → toast. Trả true nếu đã xóa.
         *  opts: {confirm, success, rowId, okText = "Xóa"}. Lỗi hiện qua showError. */
        async deleteItem(url, opts) {
            if (!await UIDialog.confirm(opts.confirm, { variant: "danger", okText: opts.okText || "Xóa" })) return false;
            try {
                await HubApi.request(url, { method: "DELETE" });
                const row = opts.rowId ? document.getElementById(opts.rowId) : null;
                if (row) row.remove();
                UIDialog.toast(opts.success, "success");
                return true;
            } catch (err) {
                HubApi.showError(err);
                return false;
            }
        },
        /** Hiện lỗi chuẩn: toast cho lỗi ngắn, dialog cho lỗi dài. Trả về message. */
        showError(err, prefix = "") {
            const msg = (prefix ? prefix + " " : "") + (err && err.message ? err.message : String(err));
            if (window.UIDialog) {
                if (msg.length > 120) UIDialog.alert(msg, { variant: "danger" }); else UIDialog.toast(msg, "danger");
            } else {
                console.error(msg);
            }
            return msg;
        },
    };

    // ------------------------------------------------------------------ HubDom
    const HubDom = {
        /** Nạp ảnh (URL / data URL) → Promise<HTMLImageElement>; lỗi → reject. */
        loadImage(src) {
            return new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => resolve(img);
                img.onerror = reject;
                img.src = src;
            });
        },
        /** Hiện khối #blockId khi giá trị của #selectId === value, ngược lại ẩn (display none). */
        toggleWhen(selectId, blockId, value) {
            document.getElementById(blockId).style.display =
                document.getElementById(selectId).value === value ? "" : "none";
        },
        /** Lọc các dòng theo ô tìm không dấu: dòng không chứa chuỗi trong #inputId thì hidden. */
        filterRows(inputId, rowsSelector) {
            const q = HubFmt.stripVN(document.getElementById(inputId).value || "");
            document.querySelectorAll(rowsSelector).forEach((tr) => {
                tr.hidden = !HubFmt.stripVN(tr.textContent).includes(q);
            });
        },
        /** Ô "chọn tất cả": đặt mọi checkbox rowSelector (không disabled) theo master rồi cập nhật thanh hàng loạt. */
        selectAll(master, rowSelector) {
            document.querySelectorAll(rowSelector + ":not(:disabled)").forEach((cb) => { cb.checked = master.checked; });
            HubDom.syncBulkBar(rowSelector);
        },
        /** Cập nhật thanh thao tác hàng loạt: #bulk-sign-btn hiện khi có dòng tick, #selected-count là số dòng. */
        syncBulkBar(rowSelector) {
            const n = document.querySelectorAll(rowSelector + ":checked").length;
            document.getElementById("bulk-sign-btn").hidden = n === 0;
            document.getElementById("selected-count").innerText = n;
        },
    };

    // ------------------------------------------------------------ HubFilterBar
    // Mọi ô lọc (.hub-field) nằm chung một vùng flex-wrap. Chỉ khi tràn quá MỘT dòng mới có nút thu gọn;
    // thu gọn = giữ nguyên dòng đầu (kỳ, ô tìm...), ẩn các dòng sau và tóm tắt chúng bằng chip.
    const HubFilterBar = {
        init(root) {
            if (!root || root.__hubInit) return;
            root.__hubInit = true;
            const key = root.dataset.key || location.pathname;
            const toggle = root.querySelector("[data-hub-filter-toggle]");

            // Mặc định: có kết quả → thu gọn; chưa có → mở. Trạng thái người dùng chọn tay được nhớ theo trang,
            // nhưng chỉ áp khi trang có kết quả (không kết quả thì luôn mở để nhập).
            const def = root.dataset.default === "collapsed";
            const remembered = HubState.get("filter." + key, null);
            let collapsed = def;
            if (def && remembered !== null) collapsed = remembered === "collapsed";
            root.classList.toggle("is-collapsed", collapsed);

            if (toggle) toggle.addEventListener("click", () => {
                const now = !root.classList.contains("is-collapsed");
                HubState.set("filter." + key, now ? "collapsed" : "expanded");
                HubFilterBar.collapse(root, now);
                if (!now) HubFilterBar.focusFirst(root);
            });
            const form = root.querySelector("form");
            if (form) {
                form.addEventListener("submit", () => HubFilterBar.busy(root, true));
                // Back/Forward (bfcache) khôi phục trang đang ở trạng thái loading: trả nút Xem về bình thường
                window.addEventListener("pageshow", (ev) => { if (ev.persisted) HubFilterBar.busy(root, false); });
                // Enter trong ô lọc = Xem; Esc = xóa ô đang gõ
                form.addEventListener("keydown", (ev) => {
                    if (ev.key === "Escape" && ev.target.matches("input[type=text], input[type=search]")) {
                        ev.target.value = ""; ev.target.dispatchEvent(new Event("input", { bubbles: true }));
                    }
                });
                form.addEventListener("change", () => HubFilterBar.updateChips(root));
                // Trang bật data-autosubmit: đổi select/ngày là Xem luôn
                if (root.dataset.autosubmit !== undefined) {
                    form.addEventListener("change", (ev) => {
                        if (ev.target.matches("select, input[type=date], .hub-period input[type=hidden]") && form.requestSubmit) form.requestSubmit();
                    });
                }
            }
            HubFilterBar.layout(root);
            // Đo lại khi bề rộng thanh lọc đổi mà không có sự kiện resize (vd thanh cuộn dọc biến mất sau
            // khi HubTable co bảng vừa viewport) — nếu không, đo lúc còn thanh cuộn sẽ tưởng tràn dòng.
            if (window.ResizeObserver) {
                // Bề rộng lúc đo lần đầu (ngay đây): lần gọi đầu của observer đến ở frame sau, có thể đã
                // sau khi thanh cuộn biến mất, nên so với giá trị này chứ không bỏ qua lần gọi đầu.
                let width = Math.round(root.clientWidth);
                new ResizeObserver(() => {
                    const w = Math.round(root.clientWidth);
                    if (w !== width) { width = w; HubFilterBar.layout(root); }
                }).observe(root);
            }
        },
        fields(root) {
            return Array.from(root.querySelectorAll(".hub-filterbar-fields .hub-field"));
        },
        /** Đo xem các ô có tràn quá 1 dòng không; gắn is-folded cho ô ở dòng 2+ khi đang thu gọn. */
        layout(root) {
            root = HubFilterBar.resolve(root);
            if (!root) return;
            const fields = HubFilterBar.fields(root);
            const toggle = root.querySelector("[data-hub-filter-toggle]");
            // Đo ở trạng thái MỞ (không chip, có nút thu gọn) để kết quả không phụ thuộc trạng thái hiện tại.
            const wasCollapsed = root.classList.contains("is-collapsed");
            root.classList.remove("is-collapsed");
            fields.forEach((f) => f.classList.remove("is-folded"));
            // Đo khi nút thu gọn đang ẨN: nút chiếm chỗ trên dòng, nếu bật lên rồi mới đo thì chính nó
            // đẩy ô cuối xuống dòng 2 (tưởng tràn). Không tràn khi ẩn nút thì giữ ẩn.
            if (toggle) toggle.hidden = true;
            const visible = fields.filter((f) => !f.hidden && f.offsetParent !== null);
            const firstTop = visible.length ? Math.min(...visible.map((f) => f.offsetTop)) : 0;
            const below = () => visible.filter((f) => f.offsetTop > firstTop + 4);
            let folded = below();
            root.__multiRow = folded.length > 0;
            if (toggle) toggle.hidden = !root.__multiRow;
            if (root.__multiRow && toggle) folded = below();   // nút vừa hiện có thể đẩy thêm ô xuống dòng 2
            if (wasCollapsed && root.__multiRow) {
                root.classList.add("is-collapsed");
                folded.forEach((f) => f.classList.add("is-folded"));
            }
            HubFilterBar.updateChips(root);
        },
        /** Bật/tắt trạng thái đang gửi của nút Xem (trang tải bằng JS gọi busy(root,false) khi xong). */
        busy(root, on = true) {
            root = HubFilterBar.resolve(root);
            if (!root) return;
            root.querySelectorAll(".btn[data-loading]").forEach((b) => {
                b.classList.toggle("is-loading", !!on); b.disabled = !!on;
            });
        },
        /** Thu gọn / mở rộng bằng code (chỉ có tác dụng khi ô lọc tràn quá 1 dòng). */
        collapse(root, on = true) {
            root = HubFilterBar.resolve(root);
            if (!root) return;
            root.classList.toggle("is-collapsed", !!on);
            HubFilterBar.layout(root);
            HubTable.fitAll();
        },
        /** Trang tải bằng JS gọi sau khi có kết quả: thu gọn theo mặc định trừ khi người dùng đã chọn mở. */
        onResults(root) {
            root = HubFilterBar.resolve(root);
            if (!root) return;
            const remembered = HubState.get("filter." + (root.dataset.key || location.pathname), null);
            HubFilterBar.collapse(root, remembered !== "expanded");
        },
        resolve(root) {
            if (typeof root === "string") return document.querySelector(root);
            return root || document.querySelector("[data-hub-filterbar]");
        },
        focusFirst(root) {
            const f = HubFilterBar.fields(root).find((x) => x.offsetTop > 0) || HubFilterBar.fields(root)[0];
            const el = f && f.querySelector("input:not([type=hidden]), select, button");
            if (el) el.focus();
        },
        /** Chip tóm tắt giá trị của các ô đang bị ẩn (dòng 2+) khi thu gọn. */
        updateChips(root) {
            const box = root.querySelector("[data-hub-chips]");
            const toggle = root.querySelector("[data-hub-filter-toggle]");
            if (!box) return;
            const chips = [];
            HubFilterBar.fields(root).filter((f) => f.classList.contains("is-folded")).forEach((field) => {
                const info = HubFilterBar.describe(field);
                if (info) chips.push({ field, ...info });
            });
            box.innerHTML = chips.map((c, i) =>
                `<button type="button" class="hub-chip" data-chip="${i}" title="Sửa ${HubFmt.escapeHtml(c.label)}">`
                + `${HubFmt.escapeHtml(c.label)}: <b>${HubFmt.escapeHtml(c.value)}</b></button>`).join("");
            box.querySelectorAll("[data-chip]").forEach((btn) => btn.addEventListener("click", () => {
                const f = chips[Number(btn.dataset.chip)].field;
                HubState.set("filter." + (root.dataset.key || location.pathname), "expanded");
                HubFilterBar.collapse(root, false);
                const el = f.querySelector("input:not([type=hidden]), select, button");
                if (el) el.focus();
            }));
            if (toggle) {
                const badge = toggle.querySelector(".badge");
                const n = root.classList.contains("is-collapsed") ? chips.length : 0;
                if (badge) { badge.textContent = n; badge.classList.toggle("d-none", !n); }
            }
        },
        /** {label, value} của một .hub-field hoặc null nếu trống. data-chip-value / data-chip-label ghi đè. */
        describe(field) {
            const labelEl = field.querySelector("label, .form-label");
            const label = field.dataset.chipLabel || (labelEl ? labelEl.textContent.trim().replace(/[:：]$/, "") : "");
            if (field.dataset.chipValue !== undefined) {
                return field.dataset.chipValue ? { label, value: field.dataset.chipValue } : null;
            }
            if (field.dataset.hubPeriod) {
                const v = HubPeriod.describe(field);
                return v ? { label, value: v } : null;
            }
            const tree = field.querySelector(".group-tree-btn");
            if (tree) {
                const t = tree.textContent.trim();
                return /^chọn/i.test(t) ? null : { label, value: t };
            }
            const checks = field.querySelectorAll("input[type=checkbox]:checked, input[type=radio]:checked");
            if (checks.length && !field.querySelector("select, input[type=text], input[type=date]")) {
                const names = Array.from(checks).map((c) => {
                    const l = field.querySelector(`label[for="${c.id}"]`);
                    return l ? l.textContent.trim() : c.value;
                });
                return { label, value: names.join(", ") };
            }
            const sel = field.querySelector("select");
            if (sel) {
                if (sel.multiple) {
                    const v = Array.from(sel.selectedOptions).map((o) => o.text.trim()).filter(Boolean);
                    return v.length ? { label, value: v.join(", ") } : null;
                }
                const opt = sel.selectedOptions[0];
                if (!opt || opt.value === "" || (opt.index === 0 && /^(--|tất cả|all)/i.test(opt.text.trim()))) return null;
                return { label, value: opt.text.trim() };
            }
            const inp = field.querySelector("input:not([type=hidden])");
            if (inp && inp.value) {
                return { label, value: inp.type === "date" ? HubFmt.date(inp.value) : inp.value };
            }
            return null;
        },
    };

    // ----------------------------------------------------------------- HubDate
    // Ô ngày gọn: <input type=hidden id=X value=ISO> + <input.hub-date data-hub-date-for=X> (dd/mm/yy)
    // + <button data-hub-date-pick=X> mở picker gốc. Hidden giữ ISO cho server/JS, tự bắn 'change' khi đổi.
    const HubDate = {
        parse(text) {
            const t = String(text || "").trim();
            if (!t) return "";
            let d, m, y;
            const sep = t.match(/^(\d{1,2})[\/.\-](\d{1,2})(?:[\/.\-](\d{2}|\d{4}))?$/);
            const digits = t.match(/^(\d{2})(\d{2})(\d{2}|\d{4})$/);
            if (sep) { d = +sep[1]; m = +sep[2]; y = sep[3]; }
            else if (digits) { d = +digits[1]; m = +digits[2]; y = digits[3]; }
            else return null;
            const now = new Date();
            y = y === undefined ? now.getFullYear() : (y.length === 2 ? 2000 + Number(y) : Number(y));
            const dt = new Date(y, m - 1, d);
            if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
            return HubFmt.isoDate(dt);
        },
        /** Đặt ISO vào hidden, cập nhật ô text đi kèm, bắn change (silent=true để không bắn). */
        set(hidden, iso, silent = false) {
            if (!hidden) return;
            hidden.value = iso || "";
            const text = document.querySelector(`[data-hub-date-for="${hidden.id}"]`);
            if (text) { text.value = HubFmt.date2(hidden.value); text.classList.remove("is-invalid"); }
            if (!silent) hidden.dispatchEvent(new Event("change", { bubbles: true }));
        },
        initText(text) {
            if (text.__hubInit) return;
            text.__hubInit = true;
            const hidden = document.getElementById(text.dataset.hubDateFor);
            if (!hidden) return;
            text.value = HubFmt.date2(hidden.value);
            const commit = () => {
                const iso = HubDate.parse(text.value);
                if (iso === null) { text.classList.add("is-invalid"); return; }
                text.classList.remove("is-invalid");
                if (iso !== hidden.value) HubDate.set(hidden, iso);
                else text.value = HubFmt.date2(hidden.value);
            };
            text.addEventListener("change", commit);
            text.addEventListener("keydown", (ev) => { if (ev.key === "Enter") commit(); });
        },
        initPick(btn) {
            if (btn.__hubInit) return;
            btn.__hubInit = true;
            btn.addEventListener("click", () => {
                const hidden = document.getElementById(btn.dataset.hubDatePick);
                if (!hidden) return;
                let native = HubDate._native;
                if (!native) {
                    native = document.createElement("input");
                    native.type = "date"; native.className = "hub-date-native"; native.tabIndex = -1;
                    document.body.appendChild(native);
                    HubDate._native = native;
                }
                const r = btn.getBoundingClientRect();
                native.style.left = (r.left + window.scrollX) + "px";
                native.style.top = (r.bottom + window.scrollY) + "px";
                native.value = hidden.value || "";
                native.onchange = () => { if (native.value) HubDate.set(hidden, native.value); };
                try { native.showPicker(); } catch (e) { native.focus(); native.click(); }
            });
        },
        boot() {
            document.querySelectorAll("[data-hub-date-for]").forEach((el) => HubDate.initText(el));
            document.querySelectorAll("[data-hub-date-pick]").forEach((el) => HubDate.initPick(el));
        },
    };

    // --------------------------------------------------------------- HubNumber
    // Ô nhập số hiện phân cách kiểu VN ngay khi gõ: <input type="text" inputmode="decimal" data-hub-number>
    // (data-hub-number="0" = số nguyên, "3" = tối đa 3 số lẻ; mặc định 2). Dấu "." luôn là phân cách
    // nghìn (gõ "12.345" = 12345), phần lẻ chỉ gõ bằng dấu ",". Bắt sự kiện ở document nên ô
    // sinh bằng JS cũng tự chạy, không cần init. el.value là chuỗi HIỂN THỊ "1.234.567,89" → đọc/ghi
    // GIÁ TRỊ SỐ qua HubNumber.get(el) / HubNumber.set(el, v), không Number(el.value) / el.value = số.
    const HubNumber = {
        maxDec(el) {
            const d = parseInt(el.dataset.hubNumber, 10);
            return Number.isNaN(d) ? 2 : d;
        },
        /** Giá trị số của ô; ô trống / không có ô → null. */
        get(el) { return el ? HubFmt.parseNumber(el.value) : null; },
        /** Gán số (hoặc chuỗi số kiểu máy "1234.5"; null/"" = xóa trống). Không bắn sự kiện input. */
        set(el, v) { if (el) el.value = HubFmt.decimal(v, HubNumber.maxDec(el)); },
        /** Chuẩn hóa chuỗi đang gõ: chỉ giữ chữ số + MỘT dấu phẩy thập phân, chèn dấu chấm hàng nghìn. */
        clean(text, maxDec = 2) {
            const s = String(text).replace(/[^\d,]/g, "");
            const cut = s.indexOf(",");
            const int = (cut < 0 ? s : s.slice(0, cut))
                .replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+$)/g, ".");
            if (cut < 0 || maxDec <= 0) return int;
            return `${int},${s.slice(cut + 1).replace(/,/g, "").slice(0, maxDec)}`;
        },
        onInput(el, ev) {
            let text = el.value;
            const caret = el.selectionStart ?? text.length;
            // Giữ con trỏ theo số ký tự có nghĩa (chữ số, dấu phẩy) bên trái nó.
            const left = text.slice(0, caret).replace(/[^\d,]/g, "").length;
            const out = HubNumber.clean(text, HubNumber.maxDec(el));
            el.value = out;
            let pos = 0;
            for (let seen = 0; pos < out.length && seen < left; pos++) {
                if (/[\d,]/.test(out[pos])) seen++;
            }
            el.setSelectionRange(pos, pos);
        },
    };
    // Pha capture: chuẩn hóa xong TRƯỚC khi handler oninput của trang đọc giá trị.
    document.addEventListener("input", (ev) => {
        if (ev.target.matches && ev.target.matches("[data-hub-number]")) HubNumber.onInput(ev.target, ev);
    }, true);
    // Rời ô: gọn lại phần gõ dở ("12," → "12", ",5" → "0,5").
    document.addEventListener("change", (ev) => {
        if (ev.target.matches && ev.target.matches("[data-hub-number]")) {
            HubNumber.set(ev.target, HubNumber.get(ev.target));
        }
    }, true);

    // Chọn số dòng/trang ở thanh phân trang (hub_ui/pagination.html, pg_sizes):
    // đổi param số dòng, bỏ param trang (về trang 1), giữ các tham số lọc khác.
    document.addEventListener("change", (ev) => {
        const sel = ev.target.closest && ev.target.closest("select[data-hub-page-size]");
        if (!sel) return;
        const url = new URL(window.location.href);
        url.searchParams.set(sel.name, sel.value);
        url.searchParams.delete(sel.dataset.hubPageSize || "page");
        window.location.href = url.toString();
    });

    // --------------------------------------------------------------- HubPeriod
    const HubPeriod = {
        /** Chuỗi tóm tắt kỳ đang chọn (cho chip): "Tháng 9/2026" | "01/09/26 → 30/09/26" | "04/09/26". */
        describe(root) {
            const mode = root.dataset.hubPeriod;
            if (mode === "month") {
                const m = root.querySelector("[data-period-month]"), y = root.querySelector("[data-period-year]");
                return m && y && m.value ? `Tháng ${m.value}/${y.value}` : "";
            }
            if (mode === "day") {
                const v = root.querySelector("[data-period-value], input[type=date]");
                return v && v.value ? HubFmt.date2(v.value) : "";
            }
            const f = root.querySelector("[data-period-from]"), t = root.querySelector("[data-period-to]");
            const a = f && f.value ? HubFmt.date2(f.value) : "", b = t && t.value ? HubFmt.date2(t.value) : "";
            return a || b ? `${a} → ${b}` : "";
        },
        /** Tính [from, to] cho preset. now = Date hiện tại. */
        preset(val, now = new Date()) {
            const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
            const day = (dd) => new Date(y, m, d + dd);
            const dow = now.getDay() || 7;   // Thứ 2 = 1 ... CN = 7
            const q = Math.floor(m / 3);
            const map = {
                today: [now, now],
                yesterday: [day(-1), day(-1)],
                last7: [day(-6), now],
                last30: [day(-29), now],
                this_week: [day(-dow + 1), now],
                last_week: [day(-dow - 6), day(-dow)],
                this_month: [new Date(y, m, 1), new Date(y, m + 1, 0)],
                last_month: [new Date(y, m - 1, 1), new Date(y, m, 0)],
                this_quarter: [new Date(y, q * 3, 1), new Date(y, q * 3 + 3, 0)],
                last_quarter: [new Date(y, q * 3 - 3, 1), new Date(y, q * 3, 0)],
                this_year: [new Date(y, 0, 1), new Date(y, 11, 31)],
                last_year: [new Date(y - 1, 0, 1), new Date(y - 1, 11, 31)],
            };
            if (map[val]) return map[val];
            const mm = /^month_(\d{4})_(\d{1,2})$/.exec(val);
            if (mm) return [new Date(+mm[1], +mm[2] - 1, 1), new Date(+mm[1], +mm[2], 0)];
            return null;
        },
        init(root) {
            if (!root || root.__hubInit) return;
            root.__hubInit = true;
            const mode = root.dataset.hubPeriod || "range";
            const fire = () => {
                const form = root.dataset.submit ? document.getElementById(root.dataset.submit) : root.closest("form");
                if (root.dataset.callback && typeof window[root.dataset.callback] === "function") {
                    window[root.dataset.callback]();
                } else if (form && root.dataset.submit !== undefined && form.requestSubmit) {
                    form.requestSubmit();
                } else if (form && form.hasAttribute("data-autosubmit") && form.requestSubmit) {
                    form.requestSubmit();
                }
                root.dispatchEvent(new CustomEvent("hub:period", { bubbles: true }));
            };
            if (mode === "month") HubPeriod.initMonth(root, fire);
            else if (mode === "day") HubPeriod.initDay(root, fire);
            else HubPeriod.initRange(root, fire);
        },
        fillMonthYear(root) {
            const now = new Date();
            const mSel = root.querySelector("[data-period-month]");
            const ySel = root.querySelector("[data-period-year]");
            if (mSel && !mSel.options.length) {
                const cur = Number(mSel.dataset.value) || now.getMonth() + 1;
                for (let i = 1; i <= 12; i++) {
                    const o = new Option("Tháng " + i, String(i), false, i === cur);
                    mSel.add(o);
                }
            }
            if (ySel && !ySel.options.length) {
                const cur = Number(ySel.dataset.value) || now.getFullYear();
                const from = Math.min(Number(ySel.dataset.from) || now.getFullYear() - 5, cur);
                const to = Math.max(Number(ySel.dataset.to) || now.getFullYear() + 1, cur);
                for (let yy = from; yy <= to; yy++) ySel.add(new Option(String(yy), String(yy), false, yy === cur));
            }
        },
        initMonth(root, fire) {
            HubPeriod.fillMonthYear(root);
            const mSel = root.querySelector("[data-period-month]");
            const ySel = root.querySelector("[data-period-year]");
            const shift = (delta) => {
                let m = Number(mSel.value) + delta, y = Number(ySel.value);
                if (m < 1) { m = 12; y -= 1; } else if (m > 12) { m = 1; y += 1; }
                if (!Array.from(ySel.options).some((o) => o.value === String(y))) ySel.add(new Option(String(y), String(y)));
                mSel.value = String(m); ySel.value = String(y);
                fire();
            };
            root.querySelector("[data-period-prev]")?.addEventListener("click", () => shift(-1));
            root.querySelector("[data-period-next]")?.addEventListener("click", () => shift(1));
            mSel?.addEventListener("change", fire);
            ySel?.addEventListener("change", fire);
        },
        initDay(root, fire) {
            const inp = root.querySelector("[data-period-value], input[type=date]");
            if (!inp) return;
            const shift = (delta) => {
                const d = inp.value ? new Date(inp.value + "T00:00:00") : new Date();
                d.setDate(d.getDate() + delta);
                HubDate.set(inp, HubFmt.isoDate(d), true);
                fire();
            };
            root.querySelector("[data-period-prev]")?.addEventListener("click", () => shift(-1));
            root.querySelector("[data-period-next]")?.addEventListener("click", () => shift(1));
            inp.addEventListener("change", fire);
        },
        initRange(root, fire) {
            const quick = root.querySelector("[data-period-quick]");
            const from = root.querySelector("[data-period-from]");
            const to = root.querySelector("[data-period-to]");
            if (quick) {
                const months = Number(root.dataset.months || 12);
                const now = new Date();
                for (let i = 1; i <= months; i++) {
                    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
                    const mm = String(d.getMonth() + 1).padStart(2, "0");
                    quick.add(new Option(`Tháng ${mm}/${d.getFullYear()}`, `month_${d.getFullYear()}_${mm}`));
                }
                quick.addEventListener("change", () => {
                    const r = HubPeriod.preset(quick.value);
                    if (!r) return;
                    if (from) HubDate.set(from, HubFmt.isoDate(r[0]), true);
                    if (to) HubDate.set(to, HubFmt.isoDate(r[1]), true);
                    fire();
                });
            }
            [from, to].forEach((el) => el && el.addEventListener("change", () => {
                if (quick) quick.value = "";
                if (from && to && from.value && to.value && from.value > to.value) {
                    // Từ > Đến: hoán đổi thay vì báo lỗi
                    const t = from.value; HubDate.set(from, to.value, true); HubDate.set(to, t, true);
                }
            }));
        },
    };

    // ---------------------------------------------------------------- HubTable
    const HubTable = {
        init(wrap) {
            if (!wrap || wrap.__hubInit) return;
            wrap.__hubInit = true;
            const table = wrap.querySelector("table");
            if (table) {
                table.classList.add("hub-table");
                if (table.querySelector("th[data-sort]")) HubTable.sortable(table);
            }
            if (wrap.dataset.fit !== "off") HubTable.fit(wrap);
        },
        /** Bảng cao vừa phần viewport còn lại (trừ phần dưới bảng: data-fit-bottom px, mặc định 16). */
        fit(wrap) {
            if (!wrap || wrap.dataset.fit === "off") return;
            const bottom = Number(wrap.dataset.fitBottom || 16);
            const top = wrap.getBoundingClientRect().top + window.scrollY;
            const h = window.innerHeight - top - bottom;
            wrap.style.maxHeight = Math.max(h, 240) + "px";
        },
        fitAll() { document.querySelectorAll("[data-hub-table]").forEach((w) => HubTable.fit(w)); },
        /** Spinner đang tải trên .hub-table-wrap, hoặc vùng bất kỳ có class hub-loading (kèm chữ "Đang tải..."). */
        loading(wrap, on = true) {
            if (typeof wrap === "string") wrap = document.getElementById(wrap) || document.querySelector(wrap);
            if (wrap) wrap.classList.toggle("is-loading", !!on);
        },
        /** HTML một dòng empty-state để đổ vào tbody. */
        emptyRow(colspan, title = "Không có dữ liệu", text = "", icon = "bi-inbox") {
            return `<tr class="hub-empty-row"><td colspan="${colspan}"><div class="hub-empty">`
                + `<i class="bi ${icon}"></i><h2>${HubFmt.escapeHtml(title)}</h2>`
                + (text ? `<p>${HubFmt.escapeHtml(text)}</p>` : "") + `</div></td></tr>`;
        },
        empty(tbody, colspan, title, text, icon) {
            if (typeof tbody === "string") tbody = document.getElementById(tbody) || document.querySelector(tbody);
            if (tbody) tbody.innerHTML = HubTable.emptyRow(colspan, title, text, icon);
        },
        /** Sort client theo th[data-sort="text|num|date"]; giá trị lấy từ td[data-value] nếu có. */
        sortable(table) {
            const ths = Array.from(table.querySelectorAll("thead th[data-sort]"));
            ths.forEach((th) => th.addEventListener("click", () => {
                const type = th.dataset.sort || "text";
                const dir = th.classList.contains("asc") ? "desc" : "asc";
                ths.forEach((t) => t.classList.remove("asc", "desc"));
                th.classList.add(dir);
                const col = Array.from(th.parentNode.children).indexOf(th);
                const tbody = table.tBodies[0];
                const rows = Array.from(tbody.rows).filter((r) => !r.classList.contains("hub-empty-row"));
                const val = (r) => {
                    const td = r.cells[col]; if (!td) return "";
                    const raw = td.dataset.value !== undefined ? td.dataset.value : td.textContent.trim();
                    if (type === "num") return Number(String(raw).replace(/\./g, "").replace(",", ".")) || 0;
                    if (type === "date") { const p = raw.split("/"); return p.length === 3 ? `${p[2]}-${p[1]}-${p[0]}` : raw; }
                    return HubFmt.stripVN(raw);
                };
                rows.sort((a, b) => {
                    const x = val(a), y = val(b);
                    const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
                    return dir === "asc" ? c : -c;
                });
                rows.forEach((r) => tbody.appendChild(r));
            }));
        },
    };

    // ------------------------------------------------------------ Khởi tạo
    function boot() {
        // Nút [data-hub-delete]: xóa bản ghi theo data-attribute (xem HubApi.deleteItem).
        document.addEventListener("click", (ev) => {
            const btn = ev.target.closest("[data-hub-delete]");
            if (!btn) return;
            ev.preventDefault();
            HubApi.deleteItem(btn.dataset.hubDelete, {
                confirm: btn.dataset.confirm, success: btn.dataset.success, rowId: btn.dataset.row,
            });
        });
        HubDate.boot();
        document.querySelectorAll("[data-hub-period]").forEach((el) => HubPeriod.init(el));
        document.querySelectorAll("[data-hub-filterbar]").forEach((el) => HubFilterBar.init(el));
        document.querySelectorAll("[data-hub-table]").forEach((el) => HubTable.init(el));
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            document.querySelectorAll("[data-hub-filterbar]").forEach((el) => HubFilterBar.layout(el));
            HubTable.fitAll();
        }, 120);
    });

    window.HubFmt = HubFmt;
    window.HubState = HubState;
    window.HubTabs = HubTabs;
    window.HubApi = HubApi;
    window.HubDom = HubDom;
    window.HubFilterBar = HubFilterBar;
    window.HubPeriod = HubPeriod;
    window.HubDate = HubDate;
    window.HubNumber = HubNumber;
    window.HubTable = HubTable;
})();
