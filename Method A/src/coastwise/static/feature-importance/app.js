// Render the defect-level analysis of the Method B rows as inline SVG.
// Data: defect_importance.json, built by scripts/analyze_defect_importance.py from method-b/data.json.
"use strict";

const DATA_URL = "./defect_importance.json";
const LEVELS = [1, 2, 3, 4];
const state = { data: null, report: null, model: "all", feature: null };
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const count = (v) => Number(v).toLocaleString("en-US");
const signed = (v, d = 2) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const pct = (n, total) => (total ? Math.round((100 * n) / total) : 0);
const word = (s) => state.data.severity_words[s];
const featureMeta = (key) => state.report.features.find((f) => f.key === key) || { key, label: key, kind: "numeric", levels: [] };

// ---------------------------------------------------------------- tooltip (event delegation on data-tip)
const tips = [];
function tip(html) { tips.push(html); return `data-tip="${tips.length - 1}"`; }
function showTip(event) {
  const target = event.target.closest("[data-tip]");
  const box = $("tooltip");
  if (!target) { box.hidden = true; return; }
  box.innerHTML = tips[Number(target.dataset.tip)];
  box.hidden = false;
  const pad = 14, rect = box.getBoundingClientRect();
  const x = event.clientX + pad + rect.width > innerWidth ? event.clientX - rect.width - pad : event.clientX + pad;
  const y = event.clientY + pad + rect.height > innerHeight ? event.clientY - rect.height - pad : event.clientY + pad;
  box.style.left = `${x}px`; box.style.top = `${y}px`;
}
document.addEventListener("mousemove", showTip);
document.addEventListener("focusin", (e) => {
  const t = e.target.closest("[data-tip]"); if (!t) return;
  const r = t.getBoundingClientRect(); showTip({ target: t, clientX: r.right, clientY: r.top });
});
document.addEventListener("focusout", () => { $("tooltip").hidden = true; });

// ---------------------------------------------------------------- small svg helpers
const svg = (w, h, body, label) => `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body}</svg>`;
const scale = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
// A bar with a 4px rounded data end and a square baseline end, growing left or right from x0.
function hbar(x0, x1, y, h, cls) {
  const r = Math.min(4, Math.abs(x1 - x0), h / 2);
  if (Math.abs(x1 - x0) < 0.5) return "";
  if (x1 >= x0) return `<path class="${cls}" d="M${x0},${y} H${x1 - r} Q${x1},${y} ${x1},${y + r} V${y + h - r} Q${x1},${y + h} ${x1 - r},${y + h} H${x0} Z"/>`;
  return `<path class="${cls}" d="M${x0},${y} H${x1 + r} Q${x1},${y} ${x1},${y + r} V${y + h - r} Q${x1},${y + h} ${x1 + r},${y + h} H${x0} Z"/>`;
}
function vbar(x, w, yBase, yTop, cls) {
  const r = Math.min(4, (yBase - yTop), w / 2);
  if (yBase - yTop < 0.5) return "";
  return `<path class="${cls}" d="M${x},${yBase} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + w - r} Q${x + w},${yTop} ${x + w},${yTop + r} V${yBase} Z"/>`;
}

// ---------------------------------------------------------------- sections
function renderHeader() {
  const r = state.report, d = state.data;
  const excluded = r.n_rows - r.n_analyzed;
  const waiting = d.withheld.map((w) => `${esc(w.name)} (${esc(w.status)})`).join(", ");
  $("scopeNote").innerHTML = `${esc(r.title)} · ${esc(r.firm || "firm not recorded")}, ${esc(r.edition)}. Each report is analyzed on its own.`
    + (waiting ? ` Not yet included: ${waiting}.` : "");
  $("generated").textContent = `Method B rows generated ${d.source.generated} · analysis generated ${d.generated_utc}`;
  $("notes").hidden = !r.notes.length;
  $("analysis-notes").hidden = !r.notes.length;
  $("notes").innerHTML = `<ul>${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
  $("totals").innerHTML = [
    [r.n_analyzed, "defects analyzed"], [r.n_elements, "piles and other elements"],
    [r.n_groups, "bents held out together"], [excluded, "rows excluded"],
  ].map(([v, l]) => `<div><strong>${count(v)}</strong><span>${l}</span></div>`).join("");
}

function renderDistribution() {
  const dist = state.report.severity_distribution, total = state.report.n_analyzed;
  const W = 520, H = 230, left = 40, bottom = 46, top = 20, max = Math.max(1, ...Object.values(dist));
  const step = Math.pow(10, Math.floor(Math.log10(max))) * (max / Math.pow(10, Math.floor(Math.log10(max))) >= 4 ? 2 : 1);
  const y = scale(0, max, H - bottom, top), band = (W - left) / 4, barW = 24;
  let body = "";
  for (let t = 0; t <= max; t += step) {
    body += `<line class="grid" x1="${left}" x2="${W}" y1="${y(t)}" y2="${y(t)}"/><text x="${left - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`;
  }
  LEVELS.forEach((s, i) => {
    const cx = left + band * i + band / 2, n = dist[s] || 0;
    body += `<g ${tip(`<b>${s} · ${word(s)}</b>${count(n)} of ${count(total)} defects (${pct(n, total)}%)`)}>
      <rect class="hit" x="${cx - band / 2 + 4}" y="${top}" width="${band - 8}" height="${H - bottom - top}"/>
      ${vbar(cx - barW / 2, barW, y(0), y(n), "mark")}
      <text class="value" x="${cx}" y="${y(n) - 7}" text-anchor="middle">${count(n)}</text></g>
      <text class="label" x="${cx}" y="${H - bottom + 18}" text-anchor="middle">${s}</text>
      <text x="${cx}" y="${H - bottom + 33}" text-anchor="middle">${word(s)}</text>`;
  });
  body += `<line class="zero" x1="${left}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`;
  $("distChart").innerHTML = svg(W, H, body, "Number of defects per severity code");
}

function renderModelCheck() {
  const m = state.report.models;
  const cell = (name, label) => {
    const v = m[name], gain = v.baseline_mae - v.cv_mae;
    return `<div><span>${label}</span><strong>${fmt(v.cv_mae)}</strong><small>held-out error vs ${fmt(v.baseline_mae)} for the median guess (${signed(-gain)})</small></div>`;
  };
  const best = Math.max(m.all.baseline_mae - m.all.cv_mae, m.without_defect_type.baseline_mae - m.without_defect_type.cv_mae);
  const share = best / m.all.baseline_mae;
  const verdict = best <= 0
    ? "Neither variant improves on the median guess. The feature rankings are unreliable for this report."
    : share > 0.8
      ? "Severity is almost fully recovered from the other fields. That usually means some of them restate the rating; see the limitations."
      : "The fields explain part of the severity rating within this report. Treat the rankings as descriptive of this structure.";
  $("modelCheck").innerHTML = `<div class="model-check">${cell("all", "All features")}${cell("without_defect_type", "Without defect type")}</div>
    <p class="verdict">${verdict}</p>
    <details class="cw-details"><summary>How the model is checked</summary><p>A random forest predicts each defect's severity after training on other bents only. Error is the mean absolute difference over five held-out folds of the ${count(m.all.n_groups)} bents, compared with always predicting the median.</p></details>`;
}

function renderImportance() {
  const model = state.report.models[state.model], rows = model.importance;
  if (!rows.length) { $("importanceChart").innerHTML = `<p class="empty">Not enough data to fit a model.</p>`; return; }
  const W = 860, rowH = 38, top = 26, labelW = 230, rankW = 190, H = top + rows.length * rowH + 30;
  const values = rows.map((r) => r.mae_increase);
  const lo = Math.min(0, ...values), hi = Math.max(0.05, ...values);
  const x = scale(lo, hi, labelW, W - rankW - 60);
  let body = `<text class="axis-title" x="${x(0)}" y="12" text-anchor="middle">0</text>`;
  body += `<text class="axis-title" x="${W - rankW + 10}" y="12">Rank stability (resamples)</text>`;
  rows.forEach((r, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2;
    const features = r.features.map((k) => featureMeta(k).label).join(", ");
    const tipHtml = `<b>${esc(r.group)}</b>${esc(features)}<br>Error increase when shuffled: ${signed(r.mae_increase, 3)} points<br>`
      + `Median rank ${fmt(r.median_rank, 0)} (10–90%: ${fmt(r.rank_p10, 0)}–${fmt(r.rank_p90, 0)})<br>In the top 3 in ${Math.round(100 * r.top3_share)}% of resamples`;
    body += `<g class="row" ${tip(tipHtml)} tabindex="0">
      <rect class="hit" x="0" y="${y0 + 2}" width="${W}" height="${rowH - 4}" rx="4"/>
      <text class="label" x="${labelW - 14}" y="${cy + 4}" text-anchor="end">${esc(r.group)}</text>
      ${hbar(x(0), x(r.mae_increase), cy - 8, 16, r.mae_increase > 0 ? "mark" : "mark-muted")}
      <text class="value" x="${x(Math.max(0, r.mae_increase)) + 8}" y="${cy + 4}">${signed(r.mae_increase, 3)}</text>
      <text x="${W - rankW + 10}" y="${cy + 4}">#${fmt(r.median_rank, 0)} · top 3 in ${Math.round(100 * r.top3_share)}%</text></g>`;
  });
  body += `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="${top - 4}" y2="${H - 26}"/>`;
  body += `<text class="axis-title" x="${(labelW + W - rankW - 60) / 2}" y="${H - 6}" text-anchor="middle">Increase in held-out error when the group is shuffled (severity points)</text>`;
  $("importanceChart").innerHTML = svg(W, H, body, "Permutation importance by feature group");
  $("importanceFoot").textContent = `${model.bootstraps_used} bent-level resamples for rank stability. Related features (e.g. all defect types) are shuffled together so they don't split credit.`;
}

function renderCorrelation() {
  const all = state.report.univariate;
  const rows = all.filter((r) => r.rho !== null), skipped = all.filter((r) => r.rho === null);
  if (!rows.length) { $("corrChart").innerHTML = `<p class="empty">Not enough data for this report.</p>`; return; }
  const W = 860, rowH = 30, top = 26, labelW = 270, valueW = 150, H = top + rows.length * rowH + 40;
  const x = scale(-1, 1, labelW, W - valueW);
  let body = "";
  [-1, -0.5, 0, 0.5, 1].forEach((t) => {
    body += `<line class="${t === 0 ? "zero" : "grid"}" x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${H - 34}"/><text x="${x(t)}" y="${H - 18}" text-anchor="middle">${t}</text>`;
  });
  body += `<text class="axis-title" x="${x(-1)}" y="12">← less severe</text>`;
  body += `<text class="axis-title" x="${x(1)}" y="12" text-anchor="end">more severe →</text>`;
  rows.forEach((r, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2, meta = featureMeta(r.feature);
    const clear = r.ci_low !== null && (r.ci_low > 0 || r.ci_high < 0);
    const tipHtml = `<b>${esc(meta.label)}</b>Spearman ρ = ${signed(r.rho)} (n = ${count(r.n)})<br>95% interval: ${fmt(r.ci_low)} to ${fmt(r.ci_high)}<br>${clear ? "Interval excludes zero." : "Interval includes zero: no clear relationship."}`;
    body += `<g class="row${r.feature === state.feature ? " selected" : ""}" data-feature="${esc(r.feature)}" ${tip(tipHtml)} tabindex="0" role="button" aria-label="Show detail for ${esc(meta.label)}">
      <rect class="hit" x="0" y="${y0 + 2}" width="${W}" height="${rowH - 4}" rx="4"/>
      <text class="label" x="${labelW - 14}" y="${cy + 4}" text-anchor="end">${esc(meta.label)}</text>
      ${r.ci_low !== null ? `<line class="${clear ? "whisker" : "whisker-muted"}" x1="${x(r.ci_low)}" x2="${x(r.ci_high)}" y1="${cy}" y2="${cy}"/>` : ""}
      <circle class="${clear ? "dot" : "dot dot-muted"}" cx="${x(r.rho)}" cy="${cy}" r="5"/>
      <text class="value" x="${W - valueW + 16}" y="${cy + 4}">${signed(r.rho)}${clear ? " ●" : ""} · n ${count(r.n)}</text></g>`;
  });
  body += `<text class="axis-title" x="${(labelW + W - valueW) / 2}" y="${H - 2}" text-anchor="middle">Spearman rank correlation with severity (● interval excludes zero)</text>`;
  $("corrChart").innerHTML = svg(W, H, body, "Correlation of each feature with severity")
    + (skipped.length ? `<p class="hint">Not enough varied data to compute: ${skipped.map((r) => esc(featureMeta(r.feature).label)).join(", ")}.</p>` : "");
  $("corrChart").querySelectorAll("[data-feature]").forEach((g) => {
    const pick = () => { state.feature = g.dataset.feature; $("featureSelect").value = state.feature; tips.length = 0; renderAll(); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
}

// Severity mix at each level of the selected feature: one 100% bar per level, darker = more severe.
function renderDetail() {
  const meta = featureMeta(state.feature), levels = meta.levels;
  $("detailIntro").textContent = `${meta.how} Each bar is the severity mix of the defects at that level.`;
  $("detailLegend").innerHTML = LEVELS.map((s) => `<span><i class="sev sev-${s}"></i>${s} ${esc(word(s))}</span>`).join("");
  if (!levels.length) { $("detailChart").innerHTML = `<p class="empty">No defect has a value for this feature.</p>`; $("detailTable").innerHTML = ""; return; }
  const W = 860, rowH = 44, top = 24, labelW = 170, rightW = 170, gap = 2, H = top + levels.length * rowH + 30;
  const x0 = labelW, x1 = W - rightW;
  let body = `<text class="axis-title" x="${x1 + 14}" y="12">Mean severity · defects</text>`;
  [0, 25, 50, 75, 100].forEach((t) => {
    const tx = x0 + ((x1 - x0) * t) / 100;
    body += `<line class="grid" x1="${tx}" x2="${tx}" y1="${top - 4}" y2="${H - 28}"/><text x="${tx}" y="${H - 12}" text-anchor="middle">${t}%</text>`;
  });
  levels.forEach((level, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2;
    let cursor = x0;
    body += `<text class="label" x="${labelW - 14}" y="${cy + 4}" text-anchor="end">${esc(level.label)}</text>`;
    LEVELS.forEach((s, j) => {
      const n = level.counts[j], width = ((x1 - x0) * n) / level.n;
      if (!n) return;
      const share = pct(n, level.n);
      body += `<g ${tip(`<b>${esc(meta.label)}: ${esc(level.label)}</b>${s} · ${word(s)}: ${count(n)} of ${count(level.n)} defects (${share}%)`)} tabindex="0">
        <rect class="sev sev-${s}" x="${cursor + gap / 2}" y="${cy - 10}" width="${Math.max(1, width - gap)}" height="20"/>
        ${width > 34 ? `<text class="on-${s}" x="${cursor + width / 2}" y="${cy + 4}" text-anchor="middle">${share}%</text>` : ""}</g>`;
      cursor += width;
    });
    body += `<text class="value" x="${x1 + 14}" y="${cy + 4}">${fmt(level.mean)} · n ${count(level.n)}</text>`;
  });
  $("detailChart").innerHTML = svg(W, H, body, `Severity mix by ${meta.label}`);
  $("detailTable").innerHTML = `<tr><th>${esc(meta.label)}</th><th>Defects</th><th>Mean severity</th>${LEVELS.map((s) => `<th>${s} ${esc(word(s))}</th>`).join("")}</tr>`
    + levels.map((l) => `<tr><td>${esc(l.label)}</td><td class="numeric">${count(l.n)}</td><td class="numeric">${fmt(l.mean)}</td>${l.counts.map((n) => `<td class="numeric">${count(n)}</td>`).join("")}</tr>`).join("");
}

function renderTables() {
  const r = state.report;
  $("rowsIntro").textContent = `${count(r.n_analyzed)} of the ${count(r.n_rows)} Method B rows for ${r.name} carry one of the inspector's severity codes (MN, MD, MJ, SV) and are analyzed.`;
  $("rowsTable").innerHTML = "<tr><th>Rows</th><th>Count</th><th>Examples of what was recorded</th></tr>"
    + `<tr><td>Analyzed</td><td class="numeric">${count(r.n_analyzed)}</td><td>MN, MD, MJ, SV</td></tr>`
    + r.excluded.map((e) => `<tr><td>Excluded: ${esc(e.reason)}</td><td class="numeric">${count(e.n)}</td><td>${esc(e.examples.join(", ") || "—")}</td></tr>`).join("");
  $("featureTable").innerHTML = "<tr><th>Feature</th><th>Group</th><th>Kind</th><th>Filled</th><th>How it was coded</th></tr>"
    + r.features.map((f) => `<tr><td>${esc(f.label)}<br><code>${esc(f.key)}</code></td><td>${esc(f.group)}</td><td>${esc(f.kind)}</td>
      <td class="numeric">${count(f.n_nonmissing)} / ${count(r.n_analyzed)}</td><td>${esc(f.how)}</td></tr>`).join("");
}

function renderAll() {
  tips.length = 0;
  renderHeader(); renderDistribution(); renderModelCheck(); renderImportance(); renderCorrelation(); renderDetail(); renderTables();
}

function selectReport(key) {
  state.report = state.data.reports.find((r) => r.key === key) || state.data.reports[0];
  try { localStorage.setItem("cw-feature-importance-report", state.report.key); } catch (e) { /* storage blocked */ }
  document.querySelectorAll("[data-report]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.report === state.report.key)));
  const select = $("featureSelect");
  select.innerHTML = state.report.features.map((f) => `<option value="${esc(f.key)}">${esc(f.label)}</option>`).join("");
  // Open on the feature most strongly tied to severity in this report.
  const strongest = state.report.univariate.find((u) => u.rho !== null);
  if (!state.report.features.some((f) => f.key === state.feature)) state.feature = strongest ? strongest.feature : state.report.features[0].key;
  select.value = state.feature;
  renderAll();
}

async function init() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    if (!state.data.reports?.length) throw new Error("No analyzed reports in defect_importance.json yet.");
  } catch (error) {
    $("loadError").hidden = false;
    $("loadError").textContent = `Could not load the published analysis (${error.message}). Refresh the page to try again.`;
    return;
  }
  $("reportButtons").innerHTML = state.data.reports.map((r) => `<button type="button" data-report="${esc(r.key)}" aria-pressed="false">${esc(r.name)}</button>`).join("");
  document.querySelectorAll("[data-report]").forEach((b) => b.addEventListener("click", () => { state.feature = null; selectReport(b.dataset.report); }));
  $("featureSelect").addEventListener("change", (e) => { state.feature = e.target.value; renderAll(); });
  document.querySelectorAll("[data-model]").forEach((button) => button.addEventListener("click", () => {
    state.model = button.dataset.model;
    document.querySelectorAll("[data-model]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    renderAll();
  }));
  let start = "";
  try { start = localStorage.getItem("cw-feature-importance-report") || ""; } catch (e) { start = ""; }
  selectReport(start);
}
init();
