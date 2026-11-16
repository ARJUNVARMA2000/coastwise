"use strict";
// Method B results: one report at a time, a plain-language summary, the most common defects and a searchable list.
// Data: data.json, built by dashboard/build_coastwise_pages.py in the msds-capstone repo.

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => Number(n).toLocaleString("en-US");
const nice = t => t === "NR" ? "type not stated" : t === "NONE" ? "none found" : String(t || "").replace(/_/g, " ");
const PAGE = 50;
const SHORT = { alameda: "Alameda 2017", berkeley: "Berkeley 2017", dsny: "DSNY 2020–25" };

let DATA = null, report = null, shown = PAGE;
const filters = { q: "", type: "", sev: "", year: "" };
const active = () => !!(filters.q || filters.type || filters.sev || filters.year);

function renderTabs() {
  $("reports").innerHTML = DATA.reports.map(r => `
    <button type="button" data-key="${r.key}" aria-pressed="${r.key === report.key}">
      ${esc(SHORT[r.key] || r.name)}${r.status === "in review" ? `<span class="tag">in review</span>` : ""}
    </button>`).join("");
  $("reports").querySelectorAll("button").forEach(b => b.addEventListener("click", () => selectReport(b.dataset.key, true)));
}

const rowsOf = key => DATA.rows.filter(r => r.r === key);

function counted(list, field) {
  const m = new Map();
  for (const r of list) m.set(r[field], (m.get(r[field]) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function options(sel, entries, allLabel, labeler) {
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    entries.map(([k, n]) => `<option value="${esc(k)}">${esc(labeler(k) || "(blank)")} (${fmt(n)})</option>`).join("");
}

function selectReport(key, focus) {
  report = DATA.reports.find(r => r.key === key) || DATA.reports[0];
  Object.assign(filters, { q: "", type: "", sev: "", year: "" });
  $("f-q").value = "";
  shown = PAGE;
  try { localStorage.setItem("cw-method-b-report", report.key); } catch (e) { /* storage blocked */ }
  history.replaceState(null, "", "#" + report.key);
  renderTabs();
  renderSummary();
  options($("f-type"), counted(rowsOf(report.key), "t"), "All defects", nice);
  options($("f-sev"), counted(rowsOf(report.key), "s"), "Any severity", s => s);
  refresh();
  if (focus) $("d-lead").focus({ preventScroll: true });
}

function renderSummary() {
  const r = report, a = r.method_a;
  const older = a.status === "placeholder" ? " (from an older run)" : "";
  $("d-lead").textContent =
    `Method B found ${fmt(r.defects)} defects. Method A found ${fmt(a.defects)} records${older} and cited ${fmt(a.pages_also_in_a)} of the ${fmt(a.pages_with_key_defects)} pages where those defects are.`;
  const where = r.from_tables != null
    ? `${fmt(r.from_tables)} come from tables and pile forms, which Method A cannot read; ${fmt(r.from_text)} from the report text.`
    : `The same fenders and piles are inspected every year, so a defect can be followed over time.`;
  const status = r.status === "in review"
    ? `<span class="tag wait">In review</span> Aayush is checking these rows now, so counts may change.`
    : `<span class="tag ok">Spot-checked</span> 10 random rows were checked against the PDF on 15 November and the fixes were applied to the whole report.`;
  $("d-sub").innerHTML = `${esc(where)}<br>${status}`;

  $("d-years-wrap").hidden = !r.groups.length;
  if (r.groups.length) renderYears();
}

function renderYears() {
  $("d-years").innerHTML = `<caption class="sr-only">Inspections by year. Choose a year to show only its defects.</caption>
    <thead><tr><th scope="col">Year</th><th scope="col">Inspected by</th><th scope="col" class="numeric">Method B</th><th scope="col" class="numeric">Method A</th></tr></thead><tbody>` +
    report.groups.map(g => `<tr${filters.year === g.id ? ` class="sel"` : ""}>
      <td><button type="button" data-y="${g.id}" aria-pressed="${filters.year === g.id}">${g.id}</button></td>
      <td>${esc(g.firm)}<span class="sub">${esc(g.scale)}</span></td>
      <td class="numeric">${fmt(g.defects)}</td><td class="numeric">${fmt(g.method_a_defects)}</td></tr>`).join("") + `</tbody>`;
  $("d-years").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    filters.year = filters.year === b.dataset.y ? "" : b.dataset.y;
    shown = PAGE; refresh();
  }));
}

function filtered(skipType) {
  const q = filters.q.toLowerCase();
  return rowsOf(report.key).filter(r =>
    (skipType || !filters.type || r.t === filters.type) &&
    (!filters.sev || r.s === filters.sev) &&
    (!filters.year || r.g === filters.year) &&
    (!q || `${r.el} ${r.loc} ${nice(r.t)} ${r.s} ${r.q} ${r.m} ${r.c} ${r.a}`.toLowerCase().includes(q)));
}

function renderTypes() {
  const entries = counted(filtered(true), "t").slice(0, 6), max = entries[0]?.[1] || 1;
  $("d-types").innerHTML = entries.map(([k, n]) => `
    <li><button type="button" data-v="${esc(k)}" aria-pressed="${filters.type === k}">
      <span class="label">${esc(nice(k))}</span><span class="n">${fmt(n)}</span>
      <span class="bar" style="width:${Math.max(1, 100 * n / max)}%"></span></button></li>`).join("");
  $("d-types").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    filters.type = filters.type === b.dataset.v ? "" : b.dataset.v;
    shown = PAGE; refresh();
  }));
}

function detailLine(r) {
  return [r.s && r.s !== "NR" ? r.s : "", r.m, r.c && "cause: " + r.c, r.a].filter(Boolean).join(" · ");
}

function renderRows() {
  const list = filtered(), total = rowsOf(report.key).length;
  $("f-count").textContent = list.length === total ? `${fmt(total)} defects` : `${fmt(list.length)} of ${fmt(total)}`;
  const canView = !!report.sha256;
  $("rows").innerHTML = list.slice(0, shown).map((r, i) => {
    const page = !r.p ? "Not stated" : canView
      ? `<button type="button" data-i="${i}" aria-label="Show page ${r.p} of the PDF">p.${r.p}</button>` : `p.${r.p}`;
    const extra = detailLine(r);
    return `<tr>
      <td class="el">${esc(r.el || r.loc || "Not stated")}${r.el && r.loc && r.loc !== r.el ? `<span class="sub">${esc(r.loc)}</span>` : ""}${r.g ? `<span class="sub">${r.g} inspection</span>` : ""}</td>
      <td class="def">${esc(nice(r.t))}${extra ? `<span class="sub">${esc(extra)}</span>` : ""}</td>
      <td class="page">${page}</td>
      <td class="quote">${esc(r.q)}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="empty">No defects match. <button type="button" class="inline-link" id="empty-clear">Clear the filters</button></td></tr>`;
  $("rows").querySelectorAll("button[data-i]").forEach(b => b.addEventListener("click", () => openPage(list[+b.dataset.i])));
  $("empty-clear")?.addEventListener("click", clearFilters);
  const left = list.length - shown;
  $("more").hidden = left <= 0;
  $("more").textContent = `Show ${fmt(Math.min(PAGE, left))} more of ${fmt(left)}`;
}

function refresh() {
  $("f-type").value = filters.type; $("f-sev").value = filters.sev;
  $("f-clear").hidden = !active();
  if (report.groups.length) renderYears();
  renderTypes();
  renderRows();
}

function clearFilters() {
  Object.assign(filters, { q: "", type: "", sev: "", year: "" });
  $("f-q").value = ""; shown = PAGE; refresh();
}

function openPage(r) {
  const params = r.bb ? "?bbox=" + r.bb.join(",") : "";
  $("viewer-title").textContent = `${SHORT[report.key]}, page ${r.p}`;
  $("viewer-quote").textContent = r.q;
  $("viewer-img").alt = `Page ${r.p} of the ${report.name} report${r.bb ? ", with the quoted words highlighted" : ""}`;
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
  $("f-q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { filters.q = e.target.value.trim(); shown = PAGE; refresh(); }, 150); });
  $("f-type").addEventListener("change", e => { filters.type = e.target.value; shown = PAGE; refresh(); });
  $("f-sev").addEventListener("change", e => { filters.sev = e.target.value; shown = PAGE; refresh(); });
  $("f-clear").addEventListener("click", clearFilters);
  $("f-csv").addEventListener("click", csvDownload);
  $("more").addEventListener("click", () => { shown += PAGE; renderRows(); });
  $("viewer-close").addEventListener("click", () => $("viewer").close());
  $("viewer").addEventListener("click", e => { if (e.target === $("viewer")) $("viewer").close(); });
}

fetch("data.json", { credentials: "same-origin" })
  .then(r => { if (!r.ok) throw new Error(r.status === 401 ? "Sign in to see Method B results." : `Could not load the results (error ${r.status}). Reload the page to try again.`); return r.json(); })
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
