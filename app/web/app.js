// /admin/app.js

const $ = (sel) => document.querySelector(sel);

const LS_KEY = "DSM_API_KEY";
const LS_DECODE = "DSM_AUTO_DECODE";
const LS_SHOW_SEARCH_COLS = "DSM_SHOW_SEARCH_COLS";
const LS_LOCAL_TIME = "DSM_LOCAL_TIME";
const LS_RELATIVE_TIME = "DSM_RELATIVE_TIME";
const LS_LAST_TABLE = "DSM_LAST_TABLE";
const LS_PRESETS = "DSM_FILTER_PRESETS_V1"; // общий json для query/history, по таблицам

const TS_KEYS = new Set(["created_at", "updated_at", "archived_at"]);
const ALWAYS = ["entity_id", "created_at", "updated_at", "archived_at"];
const MAX_CELL = 140;

const OPS = ["=", "!=", "<", "<=", ">", ">=", "in"];

const state = {
  apiKey: localStorage.getItem(LS_KEY) || "",
  autoDecode: (localStorage.getItem(LS_DECODE) || "0") === "1",
  showSearchCols: (localStorage.getItem(LS_SHOW_SEARCH_COLS) || "0") === "1",
  localTime: (localStorage.getItem(LS_LOCAL_TIME) ?? "1") === "1",
  relativeTime: (localStorage.getItem(LS_RELATIVE_TIME) ?? "0") === "1",

  tables: {},
  tableName: "",
  pickerOpen: false,

  filtersOpen: false,
  historyFiltersOpen: false,
  getExtraFiltersOpen: false,
  advancedOpen: false,
  historyAdvancedOpen: false,

  lastMode: null,       // "query" | "get" | "history"
  lastOffset: 0,
  lastLimit: 50,

  listEndpoint: "/read/query",

  // Filter builder models (Query/History)
  // groups: [ [ {field,op,value}, ... ], ... ]
  queryGroups: [[]],
  historyGroups: [[]],

  queryDrafts: [{ id: 1, field: "", value: "" }],
  historyDrafts: [{ id: 1, field: "", value: "" }],
  draftSeq: 2,
};

let modalCurrentText = "";

// history modal append state
let historyModal = {
  offset: 0,
  limit: 100,
  sort: "desc",
  from_date: undefined,
  to_date: undefined,
  total: null,
  cols: null,
  rows: [],
  baseEntityFilters: {},
};

function setStatus(msg) { const el = $("#statusLine"); if (el) el.textContent = msg || "—"; }
function isTouchDevice() { return ("ontouchstart" in window) || (navigator.maxTouchPoints > 0); }

function setAuthed(isAuthed) {
  $("#grid")?.classList.toggle("hidden", !isAuthed);
  $("#loginView")?.classList.toggle("hidden", isAuthed);
  $("#authedView")?.classList.toggle("hidden", !isAuthed);
  $("#settingsCard")?.classList.toggle("hidden", isAuthed);
  $("#closeSettingsBtn")?.classList.toggle("hidden", !isAuthed);
}

// ---------- API ----------
async function apiFetch(path, { method = "GET", body = null } = {}) {
  if (!state.apiKey) throw new Error("Нужен ключ");

  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-API-KEY": state.apiKey,
    },
  };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(path, opts);
  const text = await res.text();

  let data;
  try { data = text ? JSON.parse(text) : null; }
  catch { data = { raw: text }; }

  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) ? (data.detail || data.error) : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return data;
}

// ---------- Modal (XSS-safe) ----------
function openModal(titleText) {
  modalCurrentText = "";
  $("#modalTitle").textContent = titleText || "";
  $("#modalBody").innerHTML = "";
  $("#modalBack").classList.remove("hidden");
}
function closeModal() {
  $("#modalBack").classList.add("hidden");
  $("#modalTitle").textContent = "";
  $("#modalBody").innerHTML = "";
  modalCurrentText = "";
}
function modalSetPre(text) {
  modalCurrentText = text || "";
  const pre = document.createElement("div");
  pre.className = "pre";
  pre.textContent = modalCurrentText;
  $("#modalBody").appendChild(pre);
}
function modalAppendNode(node) { $("#modalBody").appendChild(node); }

// ---------- Decode ----------
function decodeUnicodeEscapes(s) {
  try {
    s = s.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
    s = s.replace(/\\x([0-9a-fA-F]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
  } catch {}
  return s;
}
function decodePercent(s) {
  if (!/%[0-9a-fA-F]{2}/.test(s)) return s;
  try { return decodeURIComponent(s); } catch { return s; }
}
function fixMojibake(s) {
  if (!/[ÐÑÃ]/.test(s)) return s;
  try {
    const bytes = new Uint8Array([...s].map(ch => ch.charCodeAt(0) & 0xff));
    const dec = new TextDecoder("utf-8", { fatal: false });
    const out = dec.decode(bytes);
    if (/[А-Яа-яЁё]/.test(out) || /[A-Za-z]/.test(out)) return out;
  } catch {}
  return s;
}
function maybeDecodeString(s) {
  if (!state.autoDecode || typeof s !== "string") return s;
  let out = s;
  out = decodeUnicodeEscapes(out);
  out = decodePercent(out);
  out = fixMojibake(out);
  return out;
}
function deepDecode(value) {
  if (!state.autoDecode) return value;
  if (typeof value === "string") return maybeDecodeString(value);
  if (Array.isArray(value)) return value.map(deepDecode);
  if (value && typeof value === "object") {
    const o = {};
    for (const k of Object.keys(value)) o[k] = deepDecode(value[k]);
    return o;
  }
  return value;
}

// ---------- Timestamp formatting ----------
function pad2(x) { return String(x).padStart(2, "0"); }
function formatLocalDate(dt) {
  return `${dt.getFullYear()}-${pad2(dt.getMonth()+1)}-${pad2(dt.getDate())} ${pad2(dt.getHours())}:${pad2(dt.getMinutes())}:${pad2(dt.getSeconds())}`;
}
function formatUTCDate(dt) {
  return `${dt.getUTCFullYear()}-${pad2(dt.getUTCMonth()+1)}-${pad2(dt.getUTCDate())} ${pad2(dt.getUTCHours())}:${pad2(dt.getUTCMinutes())}:${pad2(dt.getUTCSeconds())}`;
}
function formatRelative(msDelta) {
  const future = msDelta > 0;
  let s = Math.abs(msDelta) / 1000;
  const days = Math.floor(s / 86400); s -= days * 86400;
  const hours = Math.floor(s / 3600); s -= hours * 3600;
  const mins = Math.floor(s / 60);
  const parts = [];
  if (days) parts.push(`${days}д`);
  if (hours) parts.push(`${hours}ч`);
  if (!days && !hours) parts.push(`${mins}м`);
  const body = parts.join(" ");
  return future ? `через ${body}` : `${body} назад`;
}
function hasTZInfo(iso) { return /(?:Z|[+\-]\d{2}:\d{2})$/.test(iso); }
function normalizeISOToUTC(iso) {
  if (typeof iso !== "string") return iso;
  let s = iso.trim().replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(s) && !hasTZInfo(s)) s += "Z";
  return s;
}
function tryFormatTimestamp(value) {
  let ms = null;
  if (typeof value === "string") {
    const raw = maybeDecodeString(value);
    const s = normalizeISOToUTC(raw);
    const t = Date.parse(s);
    if (!Number.isNaN(t)) ms = t;
  } else if (typeof value === "number") {
    if (Number.isFinite(value)) {
      if (value >= 1_000_000_000_000) ms = value;
      else if (value >= 1_000_000_000) ms = value * 1000;
    }
  }
  if (ms === null) return null;
  if (state.relativeTime) return formatRelative(ms - Date.now());
  const dt = new Date(ms);
  return state.localTime ? formatLocalDate(dt) : formatUTCDate(dt);
}

function syncDraftsFromGroups(mode) {
  const groups = getGroups(mode);
  const g0 = Array.isArray(groups?.[0]) ? groups[0] : [];

  const nextDrafts = [];
  for (const c of g0) {
    const field = (c?.field || "").trim();
    if (!field) continue;

    const op = (c?.op || "=").toLowerCase();
    if (op === "=") {
      if (c.value === undefined || c.value === null) {
        nextDrafts.push({ id: state.draftSeq++, field, value: String(c.value ?? "") });
      } else {
        nextDrafts.push({ id: state.draftSeq++, field, value: String(c.value) });
      }
    } else if (op === "in") {
      const arr = Array.isArray(c.value) ? c.value : [c.value];
      for (const v of arr) {
        nextDrafts.push({ id: state.draftSeq++, field, value: String(v ?? "") });
      }
    }
  }

  if (!nextDrafts.length) nextDrafts.push({ id: state.draftSeq++, field: "", value: "" });
  setDrafts(mode, nextDrafts);
}

// ---------- Schema helpers ----------
function getSchema() { return state.tables[state.tableName] || {}; }

function entityFields(schema) {
  return (schema.entity_fields || []).map(f => ({
    name: f.name,
    type: (f.type || "").toString().toUpperCase().trim(),
  }));
}

function getDrafts(mode) {
  return mode === "query" ? state.queryDrafts : state.historyDrafts;
}
function setDrafts(mode, drafts) {
  if (mode === "query") state.queryDrafts = drafts;
  else state.historyDrafts = drafts;
}

// Quick всегда пишет только в groups[0], advanced (groups[1..]) не трогаем
function rebuildGroup0FromDrafts(mode) {
  const drafts = getDrafts(mode);

  // соберём значения по полям
  const byField = new Map(); // field -> array(values)
  for (const d of drafts) {
    const f = (d.field || "").trim();
    const vRaw = (d.value ?? "");
    const v = String(vRaw).trim();
    if (!f || v === "") continue;

    if (!byField.has(f)) byField.set(f, []);
    byField.get(f).push(v);
  }

  const groups = getGroups(mode);
  if (!groups.length) groups.push([]);

  // ✅ сохраняем advanced-условия из group0 (те, что НЕ "=" и НЕ "in")
  const prev = Array.isArray(groups[0]) ? groups[0] : [];
  const preserved = prev.filter(c => {
    const op = (c?.op || "=").toLowerCase();
    return op !== "=" && op !== "in";
  });

  const quickConds = [];
  for (const [field, vals] of byField.entries()) {
    // unique values
    const uniq = [];
    for (const x of vals) {
      if (!uniq.some(u => String(u) === String(x))) uniq.push(x);
    }
    if (uniq.length === 1) quickConds.push({ field, op: "=", value: uniq[0] });
    else quickConds.push({ field, op: "in", value: uniq });
  }

  groups[0] = [...preserved, ...quickConds];
  setGroups(mode, groups);
}

function valuesEqualLoosely(a, b) {
  return String(a ?? "") === String(b ?? "");
}

function addOrMergeConditionAsIn(mode, field, rawValue) {
  const schema = getSchema();
  const meta = getFieldMeta(schema, mode);
  const t = (meta.get(field) || "").toUpperCase();

  // храним в groups сырое (строка) — типизацию делаем на отправке, но "in" может быть массивом строк
  const v = String(rawValue ?? "").trim();
  if (!field || !v) return;

  const groups = getGroups(mode);
  if (!groups.length) groups.push([]);
  if (!groups[0]) groups[0] = [];

  // ищем существующее условие по этому полю только в первой группе (quick = group0 AND)
  const g0 = groups[0];
  const idx = g0.findIndex(c => (c?.field || "") === field);

  if (idx === -1) {
    g0.push({ field, op: "=", value: v });
    setGroups(mode, groups);
    return;
  }

  const existing = g0[idx];

  // если уже "=" -> превращаем в in [old, new]
  if ((existing.op || "=") === "=") {
    const oldV = String(existing.value ?? "").trim();
    if (valuesEqualLoosely(oldV, v)) return; // не дублируем
    g0[idx] = { field, op: "in", value: [oldV, v] };
    setGroups(mode, groups);
    return;
  }

  // если уже "in" -> добавляем в массив
  if ((existing.op || "") === "in") {
    let arr = existing.value;
    if (!Array.isArray(arr)) arr = [String(arr ?? "").trim()].filter(Boolean);
    if (!arr.some(x => valuesEqualLoosely(x, v))) arr.push(v);
    g0[idx] = { field, op: "in", value: arr };
    setGroups(mode, groups);
    return;
  }

  // если было != или сравнения — просто добавим ещё одно условие (AND)
  g0.push({ field, op: "=", value: v });
  setGroups(mode, groups);
}
function baseFilterFields(schema) {
  // fields renamed -> search_fields
  return [...(schema.entity_fields || []), ...(schema.search_fields || []), ...(schema.data_fields || [])];
}
function changeCountName(schema) {
  if (schema.field_change_count === true && schema.field_change_count_name) return schema.field_change_count_name;
  return null;
}
function usedCountName(schema) {
  if (schema.field_used_count === true && schema.field_used_count_name) return schema.field_used_count_name;
  return null;
}

function filterFieldsForMode(schema, mode) {
  const out = [];

  // Query/History: entity_fields should be available in filters
  if (mode === "query" || mode === "history") {
    out.push(...(schema.entity_fields || []));
  }

  // search + data
  out.push(...(schema.search_fields || []), ...(schema.data_fields || []));

  // dynamic counters
  const cc = changeCountName(schema);
  const uc = usedCountName(schema);
  if (cc && (mode === "query" || mode === "get")) out.push({ name: cc, type: "INTEGER" });
  if (uc) out.push({ name: uc, type: "INTEGER" });

  // de-dup by name (keep first type)
  const seen = new Set();
  const dedup = [];
  for (const f of out) {
    const n = f?.name;
    if (!n || seen.has(n)) continue;
    seen.add(n);
    dedup.push(f);
  }
  return dedup;
}

function writeMode(schema) {
  const wm = (schema.write_mode || "").toLowerCase().trim();
  if (wm === "scd" || wm === "current" || wm === "append") return wm;

  // fallback old
  const cur = schema.current !== false;
  const his = schema.history !== false;
  if (cur && his) return "scd";
  if (cur && !his) return "current";
  if (!cur && his) return "append";
  return "current";
}

function applyTableCapabilities(schema) {
  const wm = writeMode(schema);
  const showQueryGet = (wm === "scd" || wm === "current");
  const showHistory = (wm === "scd" || wm === "append");

  // Containers should exist in HTML (blkQuery/blkGet/blkHistory).
  // If you didn't add them yet, it still works with heads.
  const bq = $("#blkQuery"), bg = $("#blkGet"), bh = $("#blkHistory");
  if (bq && bg && bh) {
    bq.classList.toggle("hidden", !showQueryGet);
    bg.classList.toggle("hidden", !showQueryGet);
    bh.classList.toggle("hidden", !showHistory);
  } else {
    $("#queryHead")?.classList.toggle("hidden", !showQueryGet);
    $("#getHead")?.classList.toggle("hidden", !showQueryGet);
    $("#historyHead")?.classList.toggle("hidden", !showHistory);
  }

  $("#querySection").classList.add("hidden"); $("#toggleQueryBtn").textContent = "▸";
  $("#getSection").classList.add("hidden"); $("#toggleGetBtn").textContent = "▸";
  $("#historySection").classList.add("hidden"); $("#toggleHistoryBtn").textContent = "▸";

  if (wm === "append") {
    $("#historySection").classList.remove("hidden");
    $("#toggleHistoryBtn").textContent = "▾";
    state.listEndpoint = "/read/history";
  } else {
    state.listEndpoint = "/read/query";
  }
}

// ---------- types ----------
function getFieldMeta(schema, mode) {
  const m = new Map();
  const all = filterFieldsForMode(schema, mode);
  for (const f of all) {
    const name = f?.name;
    if (!name) continue;
    const t = (f.type || f.db_type || f.sql_type || f.data_type || f.dtype || "")
      .toString().toUpperCase().trim();
    m.set(name, t);
  }
  return m;
}
function isIntType(t) { return t === "INTEGER" || t === "BIGINT"; }
function isFloatType(t) { return t === "FLOAT" || t === "DOUBLE" || t === "NUMERIC"; }

// ---------- Filter Builder UI (Query/History) ----------
function ensureGroups(mode) {
  if (mode === "query") {
    if (!Array.isArray(state.queryGroups) || !state.queryGroups.length) state.queryGroups = [[]];
  } else {
    if (!Array.isArray(state.historyGroups) || !state.historyGroups.length) state.historyGroups = [[]];
  }
}

function getGroups(mode) {
  ensureGroups(mode);
  return mode === "query" ? state.queryGroups : state.historyGroups;
}
function setGroups(mode, groups) {
  if (mode === "query") state.queryGroups = groups;
  else state.historyGroups = groups;
}

function createEl(tag, cls, txt) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (txt !== undefined) el.textContent = txt;
  return el;
}

function buildFilterUI(mode, containerId) {
  const schema = getSchema();
  const fields = filterFieldsForMode(schema, mode);
  const box = document.getElementById(containerId);
  box.textContent = "";

  injectFilterStyles();

  // ensure at least 1 row
  let drafts = getDrafts(mode);
  if (!Array.isArray(drafts) || !drafts.length) {
    drafts = [{ id: state.draftSeq++, field: "", value: "" }];
    setDrafts(mode, drafts);
  }

  const quick = document.createElement("div");
  quick.className = "fqQuick";

  const rowsWrap = document.createElement("div");
  rowsWrap.style.display = "flex";
  rowsWrap.style.flexDirection = "column";
  rowsWrap.style.gap = "8px";

  const render = () => {
    rowsWrap.textContent = "";
    drafts = getDrafts(mode);

    drafts.forEach((d, idx) => {
      const isLast = idx === drafts.length - 1;

      const row = document.createElement("div");
      row.className = "fqRow";

      // field select
      const fieldSel = document.createElement("select");
      fieldSel.className = "fqField";
      const opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = "Поле…";
      fieldSel.appendChild(opt0);
      fields.forEach(f => {
        const o = document.createElement("option");
        o.value = f.name;
        o.textContent = f.name;
        fieldSel.appendChild(o);
      });
      fieldSel.value = d.field || "";

      // value input
      const valInp = document.createElement("input");
      valInp.className = "fqVal";
      valInp.placeholder = "Значение";
      valInp.value = d.value ?? "";

      // right button: last => "+", others => "×"
      const btn = document.createElement("button");
      btn.className = "small fqBtn";
      btn.textContent = isLast ? "+" : "×";

      if (isLast) {
        btn.onclick = () => {
          const next = [...drafts, { id: state.draftSeq++, field: "", value: "" }];
          setDrafts(mode, next);
          render();
          // без автофокуса (чтобы не раздражало на мобиле)
        };
      } else {
        btn.onclick = () => {
          const next = drafts.filter(x => x.id !== d.id);
          if (!next.length) next.push({ id: state.draftSeq++, field: "", value: "" });
          setDrafts(mode, next);
          rebuildGroup0FromDrafts(mode);
          render();
        };
      }

      // auto-apply debounce (НЕ очищаем value)
      let timer = null;
      const scheduleApply = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          rebuildGroup0FromDrafts(mode);
        }, 200);
      };

      fieldSel.onchange = () => {
        const next = drafts.map(x => x.id === d.id ? ({ ...x, field: fieldSel.value }) : x);
        setDrafts(mode, next);
        drafts = next;
        scheduleApply();
      };
      valInp.oninput = () => {
        const next = drafts.map(x => x.id === d.id ? ({ ...x, value: valInp.value }) : x);
        setDrafts(mode, next);
        drafts = next;
        scheduleApply();
      };
      valInp.onkeydown = (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          rebuildGroup0FromDrafts(mode);
        }
      };

      row.appendChild(fieldSel);
      row.appendChild(valInp);
      row.appendChild(btn);
      rowsWrap.appendChild(row);
    });
  };

  quick.appendChild(rowsWrap);
  box.appendChild(quick);

  // ADVANCED + PRESETS
  const advWrap = document.createElement("div");
  advWrap.className = "fqAdvWrap";

  const advTop = document.createElement("div");
  advTop.className = "fqAdvTop";

  const advBtn = document.createElement("button");
  advBtn.className = "small";
  advBtn.textContent = "Расширенные ▸";

  const presetsBtn = document.createElement("button");
  presetsBtn.className = "small";
  presetsBtn.textContent = "Сохранённые";

  advTop.appendChild(advBtn);
  advTop.appendChild(presetsBtn);
  advWrap.appendChild(advTop);

  const advBody = document.createElement("div");
  advBody.className = "fqAdvBody hidden";
  advWrap.appendChild(advBody);

  advBtn.onclick = () => {
    const open = advBody.classList.contains("hidden");
    advBody.classList.toggle("hidden", !open);
    advBtn.textContent = open ? "Расширенные ▾" : "Расширенные ▸";
    if (open) {
      advBody.textContent = "";
      buildAdvancedBuilder(mode, advBody);
    }
  };

  presetsBtn.onclick = () => openPresetsModal(mode);

  box.appendChild(advWrap);

  // initial render + initial apply
  render();
  rebuildGroup0FromDrafts(mode);
}

function injectFilterStyles() {
  if (document.getElementById("fqStyle")) return;
  const st = document.createElement("style");
  st.id = "fqStyle";
  st.textContent = `
    /* quick */
    .fqQuick{display:flex; flex-direction:column; gap:10px; margin-top:6px;}
    .fqRow{
      display:grid;
      grid-template-columns: 1.1fr 1.6fr auto;
      gap:8px;
      align-items:center;
    }
    .fqBtn{width:auto; padding:8px 10px; border-radius:10px;}

    .fqChips{display:flex; flex-wrap:wrap; gap:8px;}
    .fqChip{
      display:flex; gap:8px; align-items:center;
      border:1px solid var(--line,#1f2630);
      background:#0f1318;
      border-radius:999px;
      padding:6px 8px;
    }
    .fqChipTxt{font-size:12px; opacity:.95;}
    .fqX{width:auto; padding:2px 8px; border-radius:999px;}

    .fqAdvWrap{margin-top:10px;}
    .fqAdvTop{display:flex; gap:8px; flex-wrap:wrap;}
    .fqAdvBody{margin-top:10px;}

    /* ✅ select styling (как input) */
    .fqQuick select, .fqQuick input,
    .fbRow select, .fbRow input {
      width: 100%;
      border: 1px solid var(--line,#1f2630);
      background: #0f1318;
      color: inherit;
      border-radius: 12px;
      padding: 10px 12px;
      outline: none;
    }
    .fqQuick select, .fbRow select {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-image:
        linear-gradient(45deg, transparent 50%, var(--muted,#7f8a99) 50%),
        linear-gradient(135deg, var(--muted,#7f8a99) 50%, transparent 50%);
      background-position:
        calc(100% - 18px) 52%,
        calc(100% - 12px) 52%;
      background-size: 6px 6px, 6px 6px;
      background-repeat: no-repeat;
      padding-right: 34px;
    }

    /* ✅ mobile: поле слева, значение справа (не вертикально) */
    @media (max-width:720px){
      .fqRow{
        grid-template-columns: 1fr 1fr auto;
      }
      .fqBtn{width:auto;}
    }
    
    /* preset settings */
    .psetBox{display:flex; flex-direction:column; gap:10px;}
    .psetRow{
      display:flex;
      gap:10px;
      align-items:flex-start;
      padding:10px;
      border:1px solid var(--line,#1f2630);
      border-radius:12px;
      background:#0f1318;
      cursor:pointer;
    }
    .psetRow:hover{filter:brightness(1.05);}
    .psetChk{
      width:18px; height:18px;
      border:1px solid var(--line,#1f2630);
      border-radius:6px;
      background:#0b0e13;
      display:flex;
      align-items:center;
      justify-content:center;
      margin-top:2px;
      flex:0 0 auto;
    }
    .psetChk.on{
      background:linear-gradient(180deg,#1a2431,#121a24);
      border-color:#2b3a52;
    }
    .psetChk.on:after{
      content:"";
      width:10px; height:10px;
      border-radius:3px;
      background:#e6eefc;
      opacity:.95;
    }
    .psetText{display:flex; flex-direction:column; gap:2px;}
    .psetTitle{font-weight:800;}
    .psetDesc{color:var(--muted,#7f8a99); font-size:12px; line-height:1.25;}
    
    /* ✅ global select/input styling (включая sortSelect) */
    select, input {
      border: 1px solid var(--line,#1f2630);
      background: #0f1318;
      color: inherit;
      border-radius: 12px;
      padding: 10px 12px;
      outline: none;
    }
    
    /* ✅ select стрелка */
    select {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-image:
        linear-gradient(45deg, transparent 50%, var(--muted,#7f8a99) 50%),
        linear-gradient(135deg, var(--muted,#7f8a99) 50%, transparent 50%);
      background-position:
        calc(100% - 18px) 52%,
        calc(100% - 12px) 52%;
      background-size: 6px 6px, 6px 6px;
      background-repeat: no-repeat;
      padding-right: 34px;
    }
  `;
  document.head.appendChild(st);
}

function buildAdvancedBuilder(mode, containerEl) {
  const schema = getSchema();
  const fields = filterFieldsForMode(schema, mode);
  const groups = getGroups(mode);

  // стили для advanced (один раз)
  if (!document.getElementById("fbStyle2")) {
    const st = document.createElement("style");
    st.id = "fbStyle2";
    st.textContent = `
      .fbTop{display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px;}
      .fbBtn{width:auto; padding:8px 10px; border-radius:10px; font-size:12px;}
      .fbGroup{border:1px solid var(--line,#1f2630); border-radius:14px; padding:10px; margin-top:10px; background:#0f1318;}
      .fbGroupHead{display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;}
      .fbRows{display:flex; flex-direction:column; gap:8px;}
      .fbRow{display:grid; grid-template-columns: 1.2fr .7fr 1.3fr auto; gap:8px; align-items:center;}
      @media (max-width:720px){ .fbRow{grid-template-columns: 1fr; } .fbRow .fbBtn{width:100%;}}
      .fbOr{display:flex; justify-content:center; margin:10px 0; color:var(--muted,#7f8a99); font-size:12px;}
    `;
    document.head.appendChild(st);
  }

  containerEl.textContent = "";

  const top = document.createElement("div");
  top.className = "fbTop";

  const addOr = document.createElement("button");
  addOr.className = "small fbBtn";
  addOr.textContent = "+ OR";
  const clearAll = document.createElement("button");
  clearAll.className = "small fbBtn";
  clearAll.textContent = "Очистить";

  top.appendChild(addOr);
  top.appendChild(clearAll);
  containerEl.appendChild(top);

  const fieldOptions = fields.map(f => ({ name: f.name, type: (f.type || "").toString().toUpperCase().trim() }));

  const rerender = () => buildAdvancedBuilder(mode, containerEl);

  addOr.onclick = () => {
    const g = getGroups(mode);
    g.push([]);
    setGroups(mode, g);
    rerender();
  };
  clearAll.onclick = () => {
    setGroups(mode, [[]]);
    rerender();
  };

  groups.forEach((grp, gi) => {
    if (gi > 0) {
      const or = document.createElement("div");
      or.className = "fbOr";
      or.textContent = "OR";
      containerEl.appendChild(or);
    }

    const gWrap = document.createElement("div");
    gWrap.className = "fbGroup";

    const gHead = document.createElement("div");
    gHead.className = "fbGroupHead";

    const gTitle = document.createElement("div");
    gTitle.style.fontWeight = "800";
    gTitle.textContent = `Группа ${gi + 1}`;

    const gBtns = document.createElement("div");
    gBtns.style.display = "flex";
    gBtns.style.gap = "8px";
    gBtns.style.flexWrap = "wrap";

    const addRowBtn = document.createElement("button");
    addRowBtn.className = "small fbBtn primary";
    addRowBtn.textContent = "+ условие";

    const delGroupBtn = document.createElement("button");
    delGroupBtn.className = "small fbBtn";
    delGroupBtn.textContent = "Удалить группу";
    delGroupBtn.disabled = groups.length === 1;

    gBtns.appendChild(addRowBtn);
    gBtns.appendChild(delGroupBtn);

    gHead.appendChild(gTitle);
    gHead.appendChild(gBtns);
    gWrap.appendChild(gHead);

    const rows = document.createElement("div");
    rows.className = "fbRows";

    addRowBtn.onclick = () => {
      const g = getGroups(mode);
      g[gi].push({ field: "", op: "=", value: "" });
      setGroups(mode, g);
      rerender();
    };
    delGroupBtn.onclick = () => {
      const g = getGroups(mode);
      g.splice(gi, 1);
      if (!g.length) g.push([]);
      setGroups(mode, g);
      rerender();
    };

    if (!grp.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "Нет условий";
      rows.appendChild(empty);
    } else {
      grp.forEach((cond, ci) => {
        const row = document.createElement("div");
        row.className = "fbRow";

        const fieldSel = document.createElement("select");
        const opt0 = document.createElement("option");
        opt0.value = "";
        opt0.textContent = "Поле…";
        fieldSel.appendChild(opt0);
        fieldOptions.forEach(o => {
          const op = document.createElement("option");
          op.value = o.name;
          op.textContent = o.name;
          fieldSel.appendChild(op);
        });
        fieldSel.value = cond.field || "";

        const opSel = document.createElement("select");
        OPS.forEach(opv => {
          const op = document.createElement("option");
          op.value = opv;
          op.textContent = opv;
          opSel.appendChild(op);
        });
        opSel.value = cond.op || "=";

        const valInp = document.createElement("input");
        valInp.placeholder = (opSel.value === "in") ? 'in: 1,2,3 или ["a","b"]' : "значение";
        valInp.value = cond.value ?? "";

        const rmBtn = document.createElement("button");
        rmBtn.className = "small fbBtn";
        rmBtn.textContent = "×";
        rmBtn.style.width = "auto";

        fieldSel.onchange = () => {
          const g = getGroups(mode);
          g[gi][ci].field = fieldSel.value;
          setGroups(mode, g);
        };
        opSel.onchange = () => {
          const g = getGroups(mode);
          g[gi][ci].op = opSel.value;
          setGroups(mode, g);
          valInp.placeholder = (opSel.value === "in") ? 'in: 1,2,3 или ["a","b"]' : "значение";
        };
        valInp.oninput = () => {
          const g = getGroups(mode);
          g[gi][ci].value = valInp.value;
          setGroups(mode, g);
        };
        rmBtn.onclick = () => {
          const g = getGroups(mode);
          g[gi].splice(ci, 1);
          setGroups(mode, g);
          rerender();
        };

        row.appendChild(fieldSel);
        row.appendChild(opSel);
        row.appendChild(valInp);
        row.appendChild(rmBtn);
        rows.appendChild(row);
      });
    }

    gWrap.appendChild(rows);
    containerEl.appendChild(gWrap);
  });
}

function openPresetsModal(mode) {
  openModal(mode === "query" ? "Сохранённые фильтры (Query)" : "Сохранённые фильтры (History)");

  const wrap = document.createElement("div");
  wrap.style.display = "flex";
  wrap.style.flexDirection = "column";
  wrap.style.gap = "10px";

  const list = document.createElement("div");

  function renderList() {
    list.textContent = "";
    const arr = presetsList(state.tableName, mode);
    if (!arr.length) {
      const m = document.createElement("div");
      m.className = "muted";
      m.textContent = "Нет сохранённых";
      list.appendChild(m);
      return;
    }

    arr.forEach((it) => {
      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.gap = "8px";
      row.style.alignItems = "center";
      row.style.flexWrap = "wrap";
      row.style.justifyContent = "space-between";
      row.style.border = "1px solid var(--line,#1f2630)";
      row.style.borderRadius = "12px";
      row.style.padding = "10px";
      row.style.background = "#0f1318";

      const name = document.createElement("div");
      name.style.fontWeight = "800";
      name.textContent = it.name;

      const btns = document.createElement("div");
      btns.style.display = "flex";
      btns.style.gap = "8px";
      btns.style.flexWrap = "wrap";

      const loadBtn = document.createElement("button");
      loadBtn.className = "small primary";
      loadBtn.textContent = "Загрузить";
      loadBtn.onclick = () => {
        setGroups(mode, Array.isArray(it.groups) && it.groups.length ? it.groups : [[]]);

        setGroups(mode, Array.isArray(it.groups) && it.groups.length ? it.groups : [[]]);

        // всегда пересобираем quick из groups
        syncDraftsFromGroups(mode);

        const cid = (mode === "query") ? "filtersBoxQuery" : "filtersBoxHistory";
        buildFilterUI(mode, cid);

        // ✅ auto run
        setTimeout(async () => {
          try {
            if (it.autoRun) {
              if (mode === "query") {
                $("#offsetInput").value = "0";
                state.lastOffset = 0;
                await runQuery();
              } else {
                $("#historyOffsetInput").value = "0";
                state.lastOffset = 0;
                await doHistory();
              }
            }
            setStatus("OK");
          } catch (e) {
            setStatus(e.message);
          }
        }, 0);

        closeModal();
      };

      const settingsBtn = document.createElement("button");
      settingsBtn.className = "small";
      settingsBtn.textContent = "⚙";
      settingsBtn.onclick = () => openPresetSettings(mode, it.name);

      const delBtn = document.createElement("button");
      delBtn.className = "small";
      delBtn.textContent = "Удалить";
      delBtn.onclick = () => {
        presetsRemove(state.tableName, mode, it.name);
        renderList();
      };

      btns.appendChild(loadBtn);
      btns.appendChild(settingsBtn);
      btns.appendChild(delBtn);

      row.appendChild(name);
      row.appendChild(btns);
      list.appendChild(row);
    });
  }

  // SAVE new preset (без чекбокса)
  const saveRow = document.createElement("div");
  saveRow.style.display = "flex";
  saveRow.style.gap = "8px";
  saveRow.style.flexWrap = "wrap";
  saveRow.style.alignItems = "center";

  const nameInp = document.createElement("input");
  nameInp.placeholder = "Название";
  nameInp.style.flex = "1";

  const saveBtn = document.createElement("button");
  saveBtn.className = "small primary";
  saveBtn.textContent = "Сохранить";
  saveBtn.onclick = () => {
    const nm = nameInp.value.trim();
    const g = getGroups(mode);
    // по умолчанию НЕ сохраняем drafts
    presetsUpsert(state.tableName, mode, nm, g, { autoRun: false });
    nameInp.value = "";
    renderList();
  };

  saveRow.appendChild(nameInp);
  saveRow.appendChild(saveBtn);

  wrap.appendChild(saveRow);
  wrap.appendChild(list);

  modalAppendNode(wrap);
  renderList();
}

function openPresetSettings(mode, presetName) {
  const arr = presetsList(state.tableName, mode);
  const it = arr.find(x => (x?.name || "") === presetName);
  if (!it) return;

  openModal(`Настройки: ${presetName}`);

  const box = document.createElement("div");
  box.className = "psetBox";

  // helper to build a toggle row
  const addToggle = ({ key, title, desc, value }) => {
    const row = document.createElement("div");
    row.className = "psetRow";

    const chk = document.createElement("div");
    chk.className = "psetChk" + (value ? " on" : "");

    const text = document.createElement("div");
    text.className = "psetText";

    const t = document.createElement("div");
    t.className = "psetTitle";
    t.textContent = title;

    const d = document.createElement("div");
    d.className = "psetDesc";
    d.textContent = desc;

    text.appendChild(t);
    text.appendChild(d);

    row.appendChild(chk);
    row.appendChild(text);

    row.onclick = () => {
      value = !value;
      chk.className = "psetChk" + (value ? " on" : "");
      row.dataset.value = value ? "1" : "0";
    };

    row.dataset.key = key;
    row.dataset.value = value ? "1" : "0";

    box.appendChild(row);
  };

  // ✅ первая настройка (на будущее можно добавлять addToggle)
  addToggle({
    key: "autoRun",
    title: "Автозапуск после загрузки",
    desc: mode === "query"
      ? "После загрузки пресета автоматически выполнить Query."
      : "После загрузки пресета автоматически выполнить History.",
    value: !!it.autoRun,
  });

  const save = document.createElement("button");
  save.className = "small primary";
  save.textContent = "Сохранить";

  save.onclick = () => {
    // собрать значения настроек
    const rows = box.querySelectorAll(".psetRow");
    const settings = {};
    rows.forEach(r => settings[r.dataset.key] = r.dataset.value === "1");

    const groups = Array.isArray(it.groups) && it.groups.length ? it.groups : [[]];
    const opts = {
      autoRun: !!settings.autoRun,
    };

    presetsUpsert(state.tableName, mode, presetName, groups, opts);

    // вернёмся к списку
    closeModal();
    openPresetsModal(mode);
  };

  modalAppendNode(box);
  modalAppendNode(save);
}

// parse value according to type + op
function coerceSingleValue(raw, type) {
  if (raw === null) return null;

  const s = String(raw ?? "").trim();
  if (s === "") return "";
  if (s.toLowerCase() === "null") return null;   // ✅ "null" -> null

  if (isIntType(type)) {
    if (/^-?\d+$/.test(s)) return Number(s);
    return s;
  }
  if (isFloatType(type)) {
    const norm = s.replace(",", ".");
    if (/^-?\d+(\.\d+)?$/.test(norm)) return Number(norm);
    return s;
  }
  return s; // string
}

function parseInValue(raw, type) {
  const s = String(raw ?? "").trim();
  if (!s) return [];

  // JSON array support
  if (s.startsWith("[") && s.endsWith("]")) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) return arr.map(v => coerceSingleValue(v, type));
    } catch {}
  }

  // comma-separated
  return s
  .split(",")
  .map(x => x.trim())
  .filter(x => x.length)
  .map(v => coerceSingleValue(v, type)); // ✅ тут coerceSingleValue уже умеет "null"
}

// Serialize groups to server format:
// - empty => {}
// - one group => object
// - multiple => [object, object]
function serializeGroups(mode) {
  const schema = getSchema();
  const meta = getFieldMeta(schema, mode);
  const groups = getGroups(mode);

  const outGroups = [];

  for (const grp of groups) {
    const obj = {};
    for (const cond of grp) {
      const field = (cond.field || "").trim();
      if (!field) continue;

      const op = (cond.op || "=").trim();
      const type = (meta.get(field) || "").toUpperCase();

      const rawVal = cond.value ?? "";
      if (String(rawVal).trim() === "") continue;

      if (op === "in") {
        let arr;
        if (Array.isArray(rawVal)) {
          arr = rawVal.map(vv => coerceSingleValue(vv, type));
        } else {
          arr = parseInValue(rawVal, type);
        }
        obj[field] = { op: "in", value: arr };
        continue;
      }

      const v = coerceSingleValue(rawVal, type);
      if (op === "=") {
        obj[field] = v; // old style
      } else {
        obj[field] = { op, value: v };
      }
    }

    // skip empty group
    if (Object.keys(obj).length) outGroups.push(obj);
  }

  if (!outGroups.length) return {};          // no filters
  if (outGroups.length === 1) return outGroups[0];
  return outGroups;
}

// ---------- Get (keep old simple grid) ----------
function getFieldMeta(schema, mode) {
  const m = new Map();
  const all = filterFieldsForMode(schema, mode);
  for (const f of all) {
    const name = f?.name;
    if (!name) continue;
    const t = (f.type || f.db_type || f.sql_type || f.data_type || f.dtype || "")
      .toString().toUpperCase().trim();
    m.set(name, t);
  }
  return m;
}

function readFiltersFromGrid(schema, boxPrefix, mode) {
  const filters = {};
  const fields = filterFieldsForMode(schema, mode);
  const meta = getFieldMeta(schema, mode);

  for (const f of fields) {
    const name = f.name;
    const el = document.querySelector(`#${boxPrefix}_${name}`);
    if (!el) continue;
    const v = el.value.trim();
    if (!v) continue;

    const t = (meta.get(name) || "").toUpperCase();

    if (isIntType(t)) {
      if (/^-?\d+$/.test(v)) filters[name] = Number(v);
      else filters[name] = v;
      continue;
    }
    if (isFloatType(t)) {
      const norm = v.replace(",", ".");
      if (/^-?\d+(\.\d+)?$/.test(norm)) filters[name] = Number(norm);
      else filters[name] = v;
      continue;
    }
    filters[name] = v;
  }
  return filters;
}

function buildEntityInputs(schema, containerId, inputPrefix) {
  const box = document.getElementById(containerId);
  box.textContent = "";

  const ef = entityFields(schema);
  if (!ef.length) {
    const m = document.createElement("div");
    m.className = "muted";
    m.textContent = "entity_fields пусто";
    box.appendChild(m);
    return;
  }

  // stretch last if odd count (needs CSS: .kv.odd > input:last-child { grid-column: 1 / -1; })
  box.classList.toggle("odd", (ef.length % 2) === 1);

  for (const f of ef) {
    const inp = document.createElement("input");
    inp.id = `${inputPrefix}_${f.name}`;
    inp.placeholder = f.name;
    box.appendChild(inp);
  }
}

function readEntityForGet(schema, inputPrefix) {
  const ef = entityFields(schema);
  const entity = {};
  for (const f of ef) {
    const el = document.querySelector(`#${inputPrefix}_${f.name}`);
    const v = el ? el.value.trim() : "";
    if (!v) throw new Error(`Нужно поле ${f.name} (Get)`);
    entity[f.name] = v;
  }
  return entity;
}

function buildFiltersGrid(schema, containerId, idPrefix, mode) {
  const box = document.getElementById(containerId);
  box.textContent = "";

  const fields = filterFieldsForMode(schema, mode);
  if (!fields.length) {
    const m = document.createElement("div");
    m.className = "muted";
    m.textContent = "Нет полей";
    box.appendChild(m);
    return;
  }

  const wrap = document.createElement("div");
  wrap.style.display = "grid";
  wrap.style.gridTemplateColumns = window.matchMedia("(max-width: 720px)").matches ? "1fr" : "1fr 1fr";
  wrap.style.gap = "10px";

  for (const f of fields) {
    const inp = document.createElement("input");
    inp.id = `${idPrefix}_${f.name}`;
    inp.placeholder = f.name;
    wrap.appendChild(inp);
  }
  box.appendChild(wrap);
}

// ---------- identifier filters for history modal ----------
function buildEntityFiltersFromRow(schema, row) {
  const ef = entityFields(schema);
  const filters = {};
  for (const f of ef) {
    const k = f.name;
    const v = row?.[k];
    if (v === null || v === undefined || String(v).trim() === "") {
      return { ok: false, error: `Нет поля ${k} в записи — историю открыть нельзя` };
    }
    filters[k] = v;
  }
  return { ok: true, filters };
}

// ---------- advanced readers ----------
function readQueryAdvanced() {
  const limit = Number($("#limitInput").value || 50);
  const offset = Number($("#offsetInput").value || 0);
  const sort = ($("#sortSelect").value || "desc").trim() || "desc";
  const at_date = ($("#atDateInput").value || "").trim() || undefined;
  return { limit, offset, sort, at_date };
}
function readHistoryAdvanced() {
  const limit = Number($("#historyLimitInput").value || 100);
  const offset = Number($("#historyOffsetInput").value || 0);
  const sort = ($("#historySortSelect").value || "desc").trim() || "desc";
  const from_date = ($("#fromDateInput").value || "").trim() || undefined;
  const to_date = ($("#toDateInput").value || "").trim() || undefined;
  return { limit, offset, sort, from_date, to_date };
}

// ---------- actions ----------
async function runQuery() {
  const { limit, offset, sort, at_date } = readQueryAdvanced();

  state.lastMode = (state.listEndpoint === "/read/history") ? "history" : "query";
  state.lastOffset = offset;
  state.lastLimit = limit;

  const filters = serializeGroups("query");
  const payload = { table: state.tableName, filters, limit, offset, sort };
  if (at_date) payload.at_date = at_date;

  const data = await apiFetch(state.listEndpoint, { method: "POST", body: payload });
  renderResults(data, state.lastMode);
}

async function doGet() {
  const schema = getSchema();
  const entity = readEntityForGet(schema, "gent");

  // keep Get filters as old "grid equals" (with typing)
  const payload = { table: state.tableName, entity };

  state.lastMode = "get";
  state.lastOffset = 0;
  state.lastLimit = 1;

  try {
    const data = await apiFetch("/read/get", { method: "POST", body: payload });
    const arr = normalizeToArray(deepDecode(data));
    if (!arr.length) renderResults({ results: [], count: 0, total: 0 }, "get");
    else renderResults(data, "get");

    // ✅ auto-collapse Get after success (особенно удобно на мобиле)
    // try {
    //   $("#getSection")?.classList.add("hidden");
    //   const b = $("#toggleGetBtn");
    //   if (b) b.textContent = "▸";
    // } catch {}
  } catch (e) {
    renderResults({ results: [], count: 0, total: 0 }, "get");
    throw e;
  }
}

async function doHistory() {
  const { limit, offset, sort, from_date, to_date } = readHistoryAdvanced();

  state.lastMode = "history";
  state.lastOffset = offset;
  state.lastLimit = limit;

  const filters = serializeGroups("history");
  const payload = { table: state.tableName, filters, limit, offset, sort };
  if (from_date) payload.from_date = from_date;
  if (to_date) payload.to_date = to_date;

  const data = await apiFetch("/read/history", { method: "POST", body: payload });
  renderResults(data, "history");
}

// ---------- render ----------
function normalizeToArray(payload) {
  if (payload && Array.isArray(payload.results)) return payload.results;
  if (payload && Array.isArray(payload.history)) return payload.history;
  if (payload && typeof payload === "object" && !Array.isArray(payload)) return [payload];
  if (Array.isArray(payload)) return payload;
  return [];
}
function getCountTotal(payload, arr) {
  const count = (payload && typeof payload === "object") ? (payload.count ?? arr.length) : arr.length;
  const total = (payload && typeof payload === "object") ? (payload.total ?? null) : null;
  return { count, total };
}
function computePageText(total, offset, limit) {
  if (total == null) return { page: Math.floor(offset / Math.max(1, limit)) + 1, pages: null };
  const pages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
  const page = Math.min(pages, Math.floor(offset / Math.max(1, limit)) + 1);
  return { page, pages };
}

function toDisplayString(val, keyName = "") {
  if (val === null || val === undefined) return "";
  const key = String(keyName || "");
  if (TS_KEYS.has(key)) {
    const ts = tryFormatTimestamp(val);
    if (ts) return ts;
  }
  if (typeof val === "string") return maybeDecodeString(val);
  if (typeof val === "object") {
    const decoded = deepDecode(val);
    try { return JSON.stringify(decoded); } catch { return String(decoded); }
  }
  return String(val);
}

function getAllowedColumnsFromSchema() {
  const schema = getSchema();
  if (state.showSearchCols) return null;

  const names = (schema.data_fields || []).map(f => f.name);

  for (const k of ALWAYS) names.push(k);
  for (const ef of entityFields(schema)) names.push(ef.name);

  const cc = changeCountName(schema);
  const uc = usedCountName(schema);
  if (cc) names.push(cc);
  if (uc) names.push(uc);

  return new Set(names);
}
function pickColumns(arr) {
  const cols = arr.length ? Object.keys(arr[0]) : [];
  const allow = getAllowedColumnsFromSchema();
  if (!allow) return cols;
  return cols.filter(c => allow.has(c));
}

function renderResults(payload, mode) {
  const schema = getSchema();
  const decodedPayload = deepDecode(payload);
  const arr = normalizeToArray(decodedPayload);

  $("#rawArea").value = decodedPayload ? JSON.stringify(decodedPayload, null, 2) : "";
  const { count, total } = getCountTotal(decodedPayload, arr);

  const offset = state.lastOffset || 0;
  const limit = state.lastLimit || 50;
  const { page, pages } = computePageText(total, offset, limit);
  const pageText = pages ? ` • page ${page}/${pages}` : ` • page ${page}/—`;

  $("#metaLine").textContent =
    total == null
      ? `offset: ${offset} • count: ${count}${pageText}`
      : `offset: ${offset} • count: ${count} / total: ${total}${pageText}`;

  $("#bottomPageText").textContent = pages ? `${page}/${pages}` : `${page}/—`;
  $("#bottomPrevBtn").disabled = (offset <= 0) || (mode === "get") || (!mode);
  $("#bottomNextBtn").disabled = (mode === "get") || (!mode) || (pages ? page >= pages : false);

  const expanded = $("#grid").classList.contains("expanded");
  if (expanded) {
    $("#expandPrevBtn").disabled = $("#bottomPrevBtn").disabled;
    $("#expandNextBtn").disabled = $("#bottomNextBtn").disabled;
  }

  const tbody = $("#tbody");
  const theadRow = $("#theadRow");
  tbody.textContent = "";
  theadRow.textContent = "";

  const cols = pickColumns(arr);
  if (!cols.length) {
    const th = document.createElement("th");
    th.textContent = "—";
    theadRow.appendChild(th);

    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.className = "muted";
    td.textContent = "Пусто";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  cols.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    theadRow.appendChild(th);
  });

  // click for history modal: any entity_fields cell
  const entityFieldSet = new Set(entityFields(schema).map(f => f.name));

  arr.forEach((row) => {
    const tr = document.createElement("tr");

    cols.forEach((c) => {
      const td = document.createElement("td");
      const text = toDisplayString(row?.[c], c);

      if ((mode === "query" || mode === "get") && entityFieldSet.has(c)) {
        td.classList.add("cellLink");
        td.textContent = text.length > MAX_CELL ? (text.slice(0, MAX_CELL) + "…") : text;
        td.onclick = async (e) => {
          e.stopPropagation();
          const built = buildEntityFiltersFromRow(schema, row);
          if (!built.ok) { openModal("History"); modalSetPre(built.error); return; }
          await openHistoryModalFromEntityFilters(built.filters);
        };
        tr.appendChild(td);
        return;
      }

      td.classList.add("cellClickable");
      if (text.length > MAX_CELL) {
        td.classList.add("cellTrunc");
        td.textContent = text.slice(0, MAX_CELL) + "…";
      } else {
        td.textContent = text;
      }
      td.onclick = (e) => { e.stopPropagation(); openModal(c); modalSetPre(text); };
      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });
}

// ---------- history modal ----------
async function openHistoryModalFromEntityFilters(entityFilters) {
  openModal("History");

  const adv = readHistoryAdvanced();
  historyModal = {
    offset: adv.offset ?? 0,
    limit: adv.limit ?? 100,
    sort: adv.sort ?? "desc",
    from_date: adv.from_date,
    to_date: adv.to_date,
    total: null,
    cols: null,
    rows: [],
    baseEntityFilters: { ...entityFilters },
  };

  $("#modalBody").innerHTML = "";

  const meta = document.createElement("div");
  meta.className = "muted";
  meta.id = "historyModalMeta";
  meta.textContent = "Загрузка…";
  modalAppendNode(meta);

  const wrap = document.createElement("div");
  wrap.className = "tableWrap";
  wrap.style.marginTop = "10px";
  wrap.id = "historyModalWrap";
  modalAppendNode(wrap);

  const table = document.createElement("table");
  table.id = "historyModalTable";
  wrap.appendChild(table);

  const bottom = document.createElement("div");
  bottom.style.marginTop = "12px";
  bottom.style.display = "flex";
  bottom.style.justifyContent = "center";
  const moreBtn = document.createElement("button");
  moreBtn.className = "small";
  moreBtn.id = "historyModalMoreBtn";
  moreBtn.textContent = "↓";
  bottom.appendChild(moreBtn);
  modalAppendNode(bottom);

  moreBtn.onclick = async () => {
    historyModal.offset += historyModal.limit;
    await historyModalAppend();
  };

  await historyModalAppend();
}

async function historyModalAppend() {
  // /read/history must include entity_fields identifier
  const filters = { ...(historyModal.baseEntityFilters || {}) };

  // Extra: also apply History builder filters (OR/groups) ONLY if it is a single-object
  // чтобы не ломать идентификатор. Если пользователь сделал OR — игнорируем тут (можно расширить позже).
  const extra = serializeGroups("history");
  if (Array.isArray(extra)) {
    // ignore OR in modal (keeps identifier stable)
  } else if (extra && typeof extra === "object") {
    for (const k of Object.keys(extra)) {
      if (!(k in filters)) filters[k] = extra[k];
    }
  }

  const payload = {
    table: state.tableName,
    filters,
    limit: historyModal.limit,
    offset: historyModal.offset,
    sort: historyModal.sort,
  };
  if (historyModal.from_date) payload.from_date = historyModal.from_date;
  if (historyModal.to_date) payload.to_date = historyModal.to_date;

  const data = await apiFetch("/read/history", { method: "POST", body: payload });
  const decoded = deepDecode(data);
  const arr = normalizeToArray(decoded);

  const count = decoded?.count ?? arr.length;
  const total = decoded?.total ?? null;
  historyModal.total = total;

  const { page, pages } = computePageText(total, historyModal.offset, historyModal.limit);
  const pageText = pages ? ` • page ${page}/${pages}` : ` • page ${page}/—`;

  $("#historyModalMeta").textContent =
    total == null
      ? `offset: ${historyModal.offset} • count: ${count}${pageText}`
      : `offset: ${historyModal.offset} • count: ${count} / total: ${total}${pageText}`;

  if (!arr.length) {
    const btn = $("#historyModalMoreBtn");
    btn.disabled = true;
    btn.textContent = "—";
    modalCurrentText = JSON.stringify({ table: state.tableName, filters, history: historyModal.rows, total: historyModal.total }, null, 2);
    return;
  }

  historyModal.rows.push(...arr);

  const table = $("#historyModalTable");
  if (!historyModal.cols) {
    historyModal.cols = pickColumns(arr);

    const thead = document.createElement("thead");
    const trh = document.createElement("tr");
    historyModal.cols.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    });
    thead.appendChild(trh);

    const tbody = document.createElement("tbody");
    tbody.id = "historyModalTbody";
    table.appendChild(thead);
    table.appendChild(tbody);
  }

  const tbody = $("#historyModalTbody");
  arr.forEach((row) => {
    const tr = document.createElement("tr");
    historyModal.cols.forEach((c) => {
      const td = document.createElement("td");
      const text = toDisplayString(row?.[c], c);
      td.classList.add("cellClickable");
      if (text.length > MAX_CELL) {
        td.classList.add("cellTrunc");
        td.textContent = text.slice(0, MAX_CELL) + "…";
      } else {
        td.textContent = text;
      }
      td.onclick = (e) => { e.stopPropagation(); openModal(c); modalSetPre(text); };
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  const btn = $("#historyModalMoreBtn");
  if (historyModal.total != null && (historyModal.offset + historyModal.limit) >= historyModal.total) {
    btn.disabled = true;
    btn.textContent = "✓";
  }

  $("#historyModalWrap").scrollTop = $("#historyModalWrap").scrollHeight;

  modalCurrentText = JSON.stringify(
    { table: state.tableName, filters, history: historyModal.rows, total: historyModal.total },
    null, 2
  );
}

// ---------- info ----------
function openTableInfoModal() {
  const schema = getSchema();
  openModal(`Info: ${state.tableName}`);
  modalSetPre(JSON.stringify(deepDecode(schema), null, 2));
}

// ---------- pagination ----------
async function paginateCurrent(dir) {
  if (state.lastMode === "query") {
    const lim = Number($("#limitInput").value || 50);
    const cur = Number($("#offsetInput").value || 0);
    const next = Math.max(0, cur + dir * lim);
    $("#offsetInput").value = String(next);
    state.lastOffset = next;
    state.lastLimit = lim;
    await runQuery();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (state.lastMode === "history") {
    const lim = Number($("#historyLimitInput").value || 100);
    const cur = Number($("#historyOffsetInput").value || 0);
    const next = Math.max(0, cur + dir * lim);
    $("#historyOffsetInput").value = String(next);
    state.lastOffset = next;
    state.lastLimit = lim;
    await doHistory();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
}

// ---------- auto load ----------
async function safeAutoLoad() {
  try {
    $("#offsetInput").value = "0";
    $("#historyOffsetInput").value = "0";
    state.lastOffset = 0;
    await runQuery();
  } catch (e) {
    setStatus(e.message);
    renderResults({ results: [], count: 0, total: 0 }, null);
  }
}

// ---------- picker ----------
function closePicker() { state.pickerOpen = false; $("#pickerMenu").classList.add("hidden"); }
function openPicker() {
  state.pickerOpen = true;
  $("#pickerMenu").classList.remove("hidden");
  if (!isTouchDevice()) $("#tableSearchInput").focus();
}
function buildTableList(filterText = "") {
  const list = $("#tableList");
  list.textContent = "";

  const names = Object.keys(state.tables);
  const ft = filterText.trim().toLowerCase();
  const filtered = ft ? names.filter(n => n.toLowerCase().includes(ft)) : names;

  if (!filtered.length) {
    const d = document.createElement("div");
    d.className = "muted";
    d.textContent = "Нет совпадений";
    list.appendChild(d);
    return;
  }

  filtered.forEach((name) => {
    const el = document.createElement("div");
    el.className = "item";
    const left = document.createElement("div");
    left.className = "itemName";
    left.textContent = name;
    el.appendChild(left);

    el.onclick = async () => {
      state.tableName = name;
      localStorage.setItem(LS_LAST_TABLE, name);
      $("#pickerBtnText").textContent = name;
      $("#tablePill").textContent = name;
      closePicker();
      onTableChange();
      await safeAutoLoad();
    };

    list.appendChild(el);
  });
}

// ---------- load/change ----------
async function loadTables() {
  const tables = await apiFetch("/config/tables", { method: "GET" });
  state.tables = tables || {};
  const names = Object.keys(state.tables);
  if (!names.length) throw new Error("Нет таблиц");

  const last = localStorage.getItem(LS_LAST_TABLE) || "";
  if (last && state.tables[last]) state.tableName = last;
  else if (!state.tableName || !state.tables[state.tableName]) state.tableName = names[0];

  $("#pickerBtnText").textContent = state.tableName;
  $("#tablePill").textContent = state.tableName;

  buildTableList($("#tableSearchInput").value || "");
  onTableChange();
}

function onTableChange() {
  const schema = getSchema();

  // reset offsets
  $("#offsetInput").value = "0";
  $("#historyOffsetInput").value = "0";
  state.lastOffset = 0;

  localStorage.setItem(LS_LAST_TABLE, state.tableName);

  state.queryDrafts = [{ id: state.draftSeq++, field: "", value: "" }];
  state.historyDrafts = [{ id: state.draftSeq++, field: "", value: "" }];
  state.queryGroups = [[]];
  state.historyGroups = [[]];

  // reset filter models
  state.queryGroups = [[]];
  state.historyGroups = [[]];

  // Get entity fields + Get extra filters (grid)
  buildEntityInputs(schema, "entityBoxGet", "gent");

  // Build new builders for Query/History
  buildFilterUI("query", "filtersBoxQuery");
  buildFilterUI("history", "filtersBoxHistory");

  applyTableCapabilities(schema);
  renderResults({ results: [], count: 0, total: 0 }, null);
}

// ---------- settings ----------
function refreshToggleButtons() {
  $("#toggleDecodeBtn").textContent = state.autoDecode ? "Автодекод: вкл" : "Автодекод: выкл";
  $("#toggleSearchColsBtn").textContent = state.showSearchCols ? "Search поля: вкл" : "Search поля: выкл";
  $("#toggleLocalTimeBtn").textContent = state.localTime ? "Локальное время: вкл" : "Локальное время: выкл";
  $("#toggleRelativeTimeBtn").textContent = state.relativeTime ? "Относительное: вкл" : "Относительное: выкл";
}

// ---------- UI wiring ----------
function bindToggle(headSel, bodySel, btnSel) {
  const head = $(headSel), body = $(bodySel), btn = $(btnSel);
  const toggle = () => {
    const open = body.classList.contains("hidden");
    body.classList.toggle("hidden", !open);
    btn.textContent = open ? "▾" : "▸";
  };
  head.onclick = () => toggle();
  btn.onclick = (e) => { e.stopPropagation(); toggle(); };
}

function presetsLoadAll() {
  try { return JSON.parse(localStorage.getItem(LS_PRESETS) || "{}"); }
  catch { return {}; }
}
function presetsSaveAll(obj) {
  localStorage.setItem(LS_PRESETS, JSON.stringify(obj || {}));
}

function presetsKey(table, mode) {
  return `${table}::${mode}`; // mode: "query" or "history"
}

function presetsList(table, mode) {
  const all = presetsLoadAll();
  const k = presetsKey(table, mode);
  return Array.isArray(all[k]) ? all[k] : [];
}

function presetsUpsert(table, mode, name, groups, opts = {}) {
  const all = presetsLoadAll();
  const k = presetsKey(table, mode);
  const arr = Array.isArray(all[k]) ? all[k] : [];
  const cleanName = (name || "").trim();
  if (!cleanName) throw new Error("Нужно имя");

  const item = {
    name: cleanName,
    groups: groups || [[]],
    autoRun: !!opts.autoRun,
  };

  const idx = arr.findIndex(x => (x?.name || "") === cleanName);
  if (idx >= 0) arr[idx] = item;
  else arr.push(item);

  all[k] = arr;
  presetsSaveAll(all);
}

function presetsRemove(table, mode, name) {
  const all = presetsLoadAll();
  const k = presetsKey(table, mode);
  const arr = Array.isArray(all[k]) ? all[k] : [];
  all[k] = arr.filter(x => (x?.name || "") !== name);
  presetsSaveAll(all);
}

function wireUI() {
  // modal
  $("#modalCloseBtn").onclick = closeModal;
  $("#modalBack").onclick = (e) => { if (e.target.id === "modalBack") closeModal(); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  $("#modalCopyBtn").onclick = async () => { await navigator.clipboard.writeText(modalCurrentText || ""); setStatus("OK"); };

  // settings
  $("#toggleSettingsBtn").onclick = () => {
    if ($("#grid").classList.contains("hidden")) return;
    $("#settingsCard").classList.toggle("hidden");
  };
  $("#closeSettingsBtn").onclick = () => $("#settingsCard").classList.toggle("hidden");

  $("#toggleDecodeBtn").onclick = () => {
    state.autoDecode = !state.autoDecode;
    localStorage.setItem(LS_DECODE, state.autoDecode ? "1" : "0");
    refreshToggleButtons();
    try { const raw = $("#rawArea").value; if (raw) renderResults(JSON.parse(raw), state.lastMode); } catch {}
  };
  $("#toggleSearchColsBtn").onclick = () => {
    state.showSearchCols = !state.showSearchCols;
    localStorage.setItem(LS_SHOW_SEARCH_COLS, state.showSearchCols ? "1" : "0");
    refreshToggleButtons();
    try { const raw = $("#rawArea").value; if (raw) renderResults(JSON.parse(raw), state.lastMode); } catch {}
  };
  $("#toggleLocalTimeBtn").onclick = () => {
    state.localTime = !state.localTime;
    localStorage.setItem(LS_LOCAL_TIME, state.localTime ? "1" : "0");
    refreshToggleButtons();
    try { const raw = $("#rawArea").value; if (raw) renderResults(JSON.parse(raw), state.lastMode); } catch {}
  };
  $("#toggleRelativeTimeBtn").onclick = () => {
    state.relativeTime = !state.relativeTime;
    localStorage.setItem(LS_RELATIVE_TIME, state.relativeTime ? "1" : "0");
    refreshToggleButtons();
    try { const raw = $("#rawArea").value; if (raw) renderResults(JSON.parse(raw), state.lastMode); } catch {}
  };

  // login
  $("#loginBtn").onclick = async () => {
    const v = $("#apiKeyInput").value.trim();
    if (!v) return;
    state.apiKey = v;
    try {
      await loadTables();
      localStorage.setItem(LS_KEY, v);
      refreshToggleButtons();
      setAuthed(true);
      setStatus("OK");
      await safeAutoLoad();
    } catch (e) {
      state.apiKey = "";
      localStorage.removeItem(LS_KEY);
      setAuthed(false);
      setStatus(e.message);
    }
  };

  $("#logoutBtn").onclick = () => {
    state.apiKey = "";
    localStorage.removeItem(LS_KEY);
    state.tables = {};
    state.tableName = "";
    $("#pickerBtnText").textContent = "Выбрать…";
    $("#tablePill").textContent = "—";
    renderResults({ results: [], count: 0, total: 0 }, null);
    setAuthed(false);
    setStatus("—");
  };

  // info
  $("#infoBtn").onclick = openTableInfoModal;

  // picker
  $("#pickerBtn").onclick = (e) => { e.preventDefault(); state.pickerOpen ? closePicker() : openPicker(); };
  $("#tableSearchInput").oninput = () => buildTableList($("#tableSearchInput").value || "");
  document.addEventListener("click", (e) => {
    const picker = $("#picker");
    if (!picker.contains(e.target) && state.pickerOpen) closePicker();
  });

  // sections
  bindToggle("#queryHead", "#querySection", "#toggleQueryBtn");
  bindToggle("#getHead", "#getSection", "#toggleGetBtn");
  bindToggle("#historyHead", "#historySection", "#toggleHistoryBtn");

  // inner toggles
  $("#toggleFiltersBtn").onclick = () => {
    state.filtersOpen = !state.filtersOpen;
    $("#filtersSection").classList.toggle("hidden", !state.filtersOpen);
    $("#toggleFiltersBtn").textContent = state.filtersOpen ? "Фильтры ▾" : "Фильтры ▸";
  };
  $("#toggleHistoryFiltersBtn").onclick = () => {
    state.historyFiltersOpen = !state.historyFiltersOpen;
    $("#historyFiltersSection").classList.toggle("hidden", !state.historyFiltersOpen);
    $("#toggleHistoryFiltersBtn").textContent = state.historyFiltersOpen ? "Фильтры ▾" : "Фильтры ▸";
  };
  $("#toggleAdvancedBtn").onclick = () => {
    state.advancedOpen = !state.advancedOpen;
    $("#advancedSection").classList.toggle("hidden", !state.advancedOpen);
    $("#toggleAdvancedBtn").textContent = state.advancedOpen ? "Дополнительно ▾" : "Дополнительно ▸";
  };
  $("#toggleHistoryAdvancedBtn").onclick = () => {
    state.historyAdvancedOpen = !state.historyAdvancedOpen;
    $("#historyAdvancedSection").classList.toggle("hidden", !state.historyAdvancedOpen);
    $("#toggleHistoryAdvancedBtn").textContent = state.historyAdvancedOpen ? "Дополнительно ▾" : "Дополнительно ▸";
  };

  // actions
  $("#runQueryBtn").onclick = async () => {
    $("#offsetInput").value = "0";
    state.lastOffset = 0;
    try { await runQuery(); setStatus("OK"); } catch (e) { setStatus(e.message); }
  };
  $("#nextBtn").onclick = async () => {
    const lim = Number($("#limitInput").value || 50);
    const cur = Number($("#offsetInput").value || 0);
    $("#offsetInput").value = String(cur + lim);
    try { await runQuery(); setStatus("OK"); } catch (e) { setStatus(e.message); }
  };
  $("#prevBtn").onclick = async () => {
    const lim = Number($("#limitInput").value || 50);
    const cur = Number($("#offsetInput").value || 0);
    $("#offsetInput").value = String(Math.max(0, cur - lim));
    try { await runQuery(); setStatus("OK"); } catch (e) { setStatus(e.message); }
  };

  $("#getBtn").onclick = async () => {
    try { await doGet(); setStatus("OK"); } catch (e) { setStatus(e.message); }
  };

  $("#historyBtn").onclick = async () => {
    $("#historyOffsetInput").value = "0";
    state.lastOffset = 0;
    try { await doHistory(); setStatus("OK"); } catch (e) { setStatus(e.message); }
  };
  $("#historyNextBtn").onclick = async () => {
    const lim = Number($("#historyLimitInput").value || 100);
    const cur = Number($("#historyOffsetInput").value || 0);
    $("#historyOffsetInput").value = String(cur + lim);
    try { await doHistory(); setStatus("OK"); window.scrollTo({ top: 0, behavior: "smooth" }); } catch (e) { setStatus(e.message); }
  };
  $("#historyPrevBtn").onclick = async () => {
    const lim = Number($("#historyLimitInput").value || 100);
    const cur = Number($("#historyOffsetInput").value || 0);
    $("#historyOffsetInput").value = String(Math.max(0, cur - lim));
    try { await doHistory(); setStatus("OK"); window.scrollTo({ top: 0, behavior: "smooth" }); } catch (e) { setStatus(e.message); }
  };

  // raw => modal
  $("#rawBtn").onclick = () => { openModal("Raw"); modalSetPre($("#rawArea").value || ""); };
  $("#copyBtn").onclick = async () => { await navigator.clipboard.writeText($("#rawArea").value || ""); setStatus("OK"); };

  // expand paging
  $("#expandBtn").onclick = () => {
    const expanded = $("#grid").classList.toggle("expanded");
    $("#expandPrevBtn").classList.toggle("hidden", !expanded);
    $("#expandNextBtn").classList.toggle("hidden", !expanded);
    if (expanded) {
      $("#expandPrevBtn").disabled = $("#bottomPrevBtn").disabled;
      $("#expandNextBtn").disabled = $("#bottomNextBtn").disabled;
    }
  };
  $("#expandPrevBtn").onclick = async () => { try { await paginateCurrent(-1); setStatus("OK"); } catch (e) { setStatus(e.message); } };
  $("#expandNextBtn").onclick = async () => { try { await paginateCurrent(+1); setStatus("OK"); } catch (e) { setStatus(e.message); } };
  $("#bottomPrevBtn").onclick = async () => { try { await paginateCurrent(-1); setStatus("OK"); } catch (e) { setStatus(e.message); } };
  $("#bottomNextBtn").onclick = async () => { try { await paginateCurrent(+1); setStatus("OK"); } catch (e) { setStatus(e.message); } };

  // init labels
  $("#toggleFiltersBtn").textContent = "Фильтры ▸";
  $("#toggleHistoryFiltersBtn").textContent = "Фильтры ▸";
  $("#toggleAdvancedBtn").textContent = "Дополнительно ▸";
  $("#toggleHistoryAdvancedBtn").textContent = "Дополнительно ▸";
}

// ---------- init ----------
(async function init() {
  wireUI();
  refreshToggleButtons();
  setAuthed(false);

  if (!state.apiKey) { setStatus("Введи ключ"); return; }

  try {
    await loadTables();
    refreshToggleButtons();
    setAuthed(true);
    setStatus("OK");
    await safeAutoLoad();
  } catch (e) {
    state.apiKey = "";
    localStorage.removeItem(LS_KEY);
    setAuthed(false);
    setStatus(e.message);
  }
})();