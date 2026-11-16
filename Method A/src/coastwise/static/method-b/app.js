"use strict";
// Method B results: report cards, Method A comparison, defect breakdowns and a filterable row table.
// Data: data.json, built by dashboard/build_coastwise_pages.py in the msds-capstone repo.

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => Number(n).toLocaleString("en-US");
const nice = t => t === "NR" ? "type not stated (NR)" : t === "NONE" ? "none found (NONE)" : String(t || "").replace(/_/g, " ");
const PAGE = 50;
const SRC_LABEL = { narrative: "Report text", table: "Table or form", "table-comment": "Table comment", "table-hand-fixed": "Table (fixed by hand)" };
const srcLabel = s => SRC_LABEL[s] || (s.startsWith("rule") || s.includes("paragraph") ? "Report text" : s);

let DATA = null, report = null, shown = PAGE;
const filters = { q: "", type: "", sev: "", year: "", src: "" };

function statusChip(r) {
  if (r.status === "spot-checked") return `<span class="chip ok">Spot-checked</span>`;
  if (r.status === "in review") return `<span class="chip wait">In review</span>`;
  return `<span class="chip">${esc(r.status)}</span>`;
}

function renderCards() {
  $("reports").innerHTML = DATA.reports.map(r => `
    <button type="button" class="mb-report" data-key="${r.key}" aria-pressed="${r.key === report.key}">
      <span class="top"><span><span class="name">${esc(r.name)}</span><br><span class="edition">${esc(r.edition)}</span></span>${statusChip(r)}</span>
      <span class="big num">${fmt(r.defects)}<small>defects</small></span>
      <span class="vs">Method A: ${fmt(r.method_a.defects)} records${r.method_a.status === "placeholder" ? " (older run)" : ""}</span>
    </button>`).join("");
  $("reports").querySelectorAll("button").forEach(b => b.addEventListener("click", () => selectReport(b.dataset.key, true)));
}

function rowsOf(key) { return DATA.rows.filter(r => r.r === key); }

function counted(list, field) {
  const m = new Map();
  for (const r of list) m.set(r[field], (m.get(r[field]) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function bars(el, entries, field) {
  const top = entries.slice(0, 8), max = top[0]?.[1] || 1;
  el.innerHTML = top.map(([k, n]) => `
    <li><button type="button" data-v="${esc(k)}" aria-pressed="${filters[field] === k}">
      <span><span class="label" title="${esc(nice(k))}">${esc(nice(k) || "(blank)")}</span><span class="bar" style="width:${Math.max(2, 100 * n / max)}%"></span></span>
      <span class="n">${fmt(n)}</span></button></li>`).join("")
    + (entries.length > 8 ? `<li class="lede" style="margin:4px 6px 0;font-size:12px">+ ${entries.length - 8} more in the filter list</li>` : "");
  el.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    filters[field] = filters[field] === b.dataset.v ? "" : b.dataset.v;
    syncSelects(); shown = PAGE; renderBreakdowns(); renderRows();
  }));
}

function options(sel, entries, allLabel, labeler = nice) {
  sel.innerHTML = `<option value="">${allLabel}</option>` + entries.map(([k, n]) => `<option value="${esc(k)}">${esc(labeler(k) || "(blank)")} (${fmt(n)})</option>`).join("");
}

function syncSelects() {
  $("f-type").value = filters.type; $("f-sev").value = filters.sev; $("f-year").value = filters.year; $("f-src").value = filters.src;
}

function selectReport(key, focus) {
  report = DATA.reports.find(r => r.key === key) || DATA.reports[0];
  Object.assign(filters, { q: "", type: "", sev: "", year: "", src: "" });
  $("f-q").value = "";
  shown = PAGE;
  try { localStorage.setItem("cw-method-b-report", report.key); } catch (e) { /* storage blocked */ }
  history.replaceState(null, "", "#" + report.key);
  renderCards();
  renderDetail();
  if (focus) $("d-title").focus?.();
}

function renderDetail() {
  const r = report, list = rowsOf(r.key);
  $("d-title").textContent = `${r.title} · ${r.firm}, ${r.edition}`;
  $("d-title").tabIndex = -1;
  $("d-scope").textContent = r.status_note + " " + r.scope_note;
  $("d-status").innerHTML = statusChip(r);

  const stats = [[fmt(r.defects), "defects"], [fmt(r.observations), "rated items and measurements"]];
  if (r.from_tables != null) {
    stats.push([fmt(r.from_tables), "rows read from tables and forms"], [fmt(r.from_text), "rows labelled from report text"],
      [fmt(r.checked_rows), "rows checked by a person"]);
  } else {
    stats.push([String(r.groups.length), "inspections, same fenders each year"], [fmt(r.pdf_pages), "PDF pages read"], ["0", "rows checked so far"]);
  }
  $("d-stats").innerHTML = stats.map(([n, l]) => `<div><strong>${n}</strong><span>${esc(l)}</span></div>`).join("");

  const a = r.method_a, pct = a.pages_with_key_defects ? Math.round(100 * a.pages_also_in_a / a.pages_with_key_defects) : 0;
  $("d-compare").innerHTML = `
    <p class="big num" style="margin-top:10px;color:var(--navy)">${pct}%</p>
    <div class="meter" role="img" aria-label="${pct} percent"><span style="width:${pct}%"></span></div>
    <p>Of the <strong>${fmt(a.pages_with_key_defects)}</strong> PDF pages where Method B found defects, Method A cited <strong>${fmt(a.pages_also_in_a)}</strong>.</p>
    <p>Method A: ${fmt(a.defects)} records, ${esc(a.run)}. Method B: ${fmt(r.defects)} defects.</p>
    <p>A page check only, not a row-by-row score. Method A cannot read most tables and forms, which is where the per-pile defects are.</p>`;

  $("d-years-wrap").hidden = !r.groups.length;
  $("f-year-wrap").hidden = !r.groups.length;
  $("f-src-wrap").hidden = !!r.groups.length;
  if (r.groups.length) renderYears();

  options($("f-type"), counted(list, "t"), "All types");
  options($("f-sev"), counted(list, "s"), "All severities", s => s);
  options($("f-year"), r.groups.map(g => [g.id, g.defects]), "All years", y => y);
  options($("f-src"), counted(list, "src"), "Tables and text", srcLabel);
  syncSelects();
  renderBreakdowns();
  renderRows();
}

function renderYears() {
  $("d-years").innerHTML = `<thead><tr><th>Year</th><th>Inspected by</th><th>Rating words used</th><th class="numeric">Method B defects</th><th class="numeric">Method A records</th><th class="numeric">Pages A also cited</th></tr></thead><tbody>` +
    report.groups.map(g => `<tr class="${filters.year === g.id ? "sel" : ""}">
      <td><button type="button" data-y="${g.id}" aria-pressed="${filters.year === g.id}">${g.id}</button><span class="date">${esc(g.date)}</span></td>
      <td>${esc(g.firm)}</td><td>${esc(g.scale)}</td>
      <td class="numeric">${fmt(g.defects)}</td><td class="numeric">${fmt(g.method_a_defects)}</td>
      <td class="numeric">${g.pages_also_in_a} of ${g.pages_with_key_defects}</td></tr>`).join("") + `</tbody>`;
  $("d-years").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    filters.year = filters.year === b.dataset.y ? "" : b.dataset.y;
    syncSelects(); shown = PAGE; renderYears(); renderBreakdowns(); renderRows();
  }));
}

function filtered(skip) {
  const q = filters.q.toLowerCase();
  return rowsOf(report.key).filter(r =>
    (skip === "type" || !filters.type || r.t === filters.type) &&
    (skip === "sev" || !filters.sev || r.s === filters.sev) &&
    (!filters.year || r.g === filters.year) &&
    (!filters.src || r.src === filters.src) &&
    (!q || `${r.el} ${r.loc} ${r.t} ${r.s} ${r.q} ${r.m} ${r.c} ${r.a}`.toLowerCase().includes(q)));
}

function renderBreakdowns() {
  bars($("d-types"), counted(filtered("type"), "t"), "type");
  bars($("d-sev"), counted(filtered("sev"), "s"), "sev");
}

function renderRows() {
  const list = filtered();
  const total = rowsOf(report.key).length;
  $("f-count").textContent = list.length === total ? `${fmt(total)} rows` : `${fmt(list.length)} of ${fmt(total)} rows`;
  const canView = !!report.sha256;
  $("rows").innerHTML = list.slice(0, shown).map((r, i) => {
    const extra = [r.c && `cause: ${r.c}`, r.a].filter(Boolean).join(" · ");
    const page = r.p ? (canView ? `<button type="button" data-i="${i}" aria-label="Open page ${r.p}">p.${r.p}</button>` : `p.${r.p}`) : "NR";
    return `<tr>
      <td class="el">${esc(r.el || r.loc || "Not stated")}${r.el && r.loc && r.loc !== r.el ? `<span class="sub">${esc(r.loc)}</span>` : ""}${r.g ? `<span class="sub">${r.g} inspection</span>` : ""}</td>
      <td>${esc(nice(r.t))}${extra ? `<span class="sub">${esc(extra)}</span>` : ""}</td>
      <td>${esc(r.s)}</td>
      <td class="m">${esc(r.m)}</td>
      <td class="page">${page}<span class="sub">${esc(srcLabel(r.src))}</span></td>
      <td class="quote">${esc(r.q)}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="message">No rows match these filters.</td></tr>`;
  $("rows").querySelectorAll("button[data-i]").forEach(b => b.addEventListener("click", () => openPage(list[+b.dataset.i])));
  $("more").hidden = list.length <= shown;
  $("more").textContent = `Show ${Math.min(PAGE, list.length - shown)} more`;
}

function openPage(r) {
  const params = r.bb ? "?bbox=" + r.bb.join(",") : "";
  $("viewer-title").textContent = `${report.name}, PDF page ${r.p}${r.bb ? " (quote highlighted)" : ""}`;
  $("viewer-quote").textContent = r.q;
  $("viewer-img").alt = `Page ${r.p} of the ${report.name} report`;
  $("viewer-img").src = `/api/extraction/page/${report.sha256}/${r.p}${params}`;
  $("viewer").showModal();
}

function csvDownload() {
  const cols = [["id", "row_id"], ["el", "element"], ["loc", "location"], ["g", "inspection_year"], ["t", "defect_type"], ["s", "severity_raw"],
    ["m", "measure"], ["c", "cause"], ["a", "attributes"], ["p", "page"], ["q", "quote"], ["src", "read_from"], ["rv", "review_status"]];
  const cell = v => /[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? "");
  const text = [cols.map(c => c[1]).join(",")].concat(filtered().map(r => cols.map(([k]) => cell(r[k])).join(","))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = `method-b-${report.key}${filters.year ? "-" + filters.year : ""}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function wire() {
  let t;
  $("f-q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { filters.q = e.target.value.trim(); shown = PAGE; renderBreakdowns(); renderRows(); }, 150); });
  for (const [id, f] of [["f-type", "type"], ["f-sev", "sev"], ["f-year", "year"], ["f-src", "src"]]) {
    $(id).addEventListener("change", e => { filters[f] = e.target.value; shown = PAGE; if (report.groups.length) renderYears(); renderBreakdowns(); renderRows(); });
  }
  $("f-clear").addEventListener("click", () => { Object.assign(filters, { q: "", type: "", sev: "", year: "", src: "" }); $("f-q").value = ""; syncSelects(); shown = PAGE; if (report.groups.length) renderYears(); renderBreakdowns(); renderRows(); });
  $("f-csv").addEventListener("click", csvDownload);
  $("more").addEventListener("click", () => { shown += PAGE; renderRows(); });
  $("viewer-close").addEventListener("click", () => $("viewer").close());
  $("viewer").addEventListener("click", e => { if (e.target === $("viewer")) $("viewer").close(); });
}

fetch("data.json", { credentials: "same-origin" })
  .then(r => { if (!r.ok) throw new Error(r.status === 401 ? "Sign in to see Method B results." : `Could not load results (${r.status}).`); return r.json(); })
  .then(data => {
    DATA = data;
    wire();
    let start = location.hash.slice(1);
    if (!start) { try { start = localStorage.getItem("cw-method-b-report") || ""; } catch (e) { start = ""; } }
    $("loading").hidden = true;
    $("content").hidden = false;
    selectReport(start, false);
  })
  .catch(err => { $("loading").hidden = true; $("load-error").hidden = false; $("load-error").textContent = err.message; });
