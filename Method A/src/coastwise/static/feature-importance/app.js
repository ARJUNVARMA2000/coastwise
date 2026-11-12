// Render the reviewed Method A analysis snapshot as inline SVG.
"use strict";

const DATA_URL = "./feature_importance.json";
const SCORE_WORDS = { 1: "Good", 2: "Fair", 3: "Poor", 4: "Serious / critical" };
const state = { data: null, run: null, model: "with_human_rating", target: "score", feature: "human_rating" };
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const signed = (v, d = 2) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const featureMeta = (key) => state.run.features.find((f) => f.key === key) || { key, label: key, kind: "numeric" };
const shortName = (filename) => filename.replace(/\.pdf$/, "");

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
// Deterministic jitter so dots don't jump between renders.
function jitter(text, spread) {
  let h = 0; for (const c of text) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return ((h % 1000) / 1000 - 0.5) * spread;
}

// ---------------------------------------------------------------- sections
function renderHeader() {
  const r = state.run;
  $("modelBadge").textContent = `${r.model} · generated ${r.generated_utc.slice(0, 10)}`;
  $("generated").textContent = `${r.run} · ${r.generated_utc}`;
  $("notes").hidden = !r.notes.length;
  $("notes").innerHTML = `<ul>${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
  const structures = new Set(r.table.map((t) => t.structure_group)).size;
  $("totals").innerHTML = [
    [r.n_analyzed, "reports analyzed"], [r.n_scored, "scored by the LLM"],
    [structures, "distinct structures"], [r.excluded.length, "excluded"],
  ].map(([v, l]) => `<div><strong>${v}</strong><span>${l}</span></div>`).join("");
}

function renderDistribution() {
  const dist = state.run.score_distribution, total = Object.values(dist).reduce((a, b) => a + b, 0);
  const W = 520, H = 230, left = 34, bottom = 46, top = 20, max = Math.max(1, ...Object.values(dist));
  const y = scale(0, max, H - bottom, top), band = (W - left) / 4, barW = 24;
  let body = "";
  for (let t = 0; t <= max; t += Math.max(1, Math.ceil(max / 4))) {
    body += `<line class="grid" x1="${left}" x2="${W}" y1="${y(t)}" y2="${y(t)}"/><text x="${left - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`;
  }
  [1, 2, 3, 4].forEach((s, i) => {
    const cx = left + band * i + band / 2, n = dist[s] || 0;
    body += `<g ${tip(`<b>Score ${s} · ${SCORE_WORDS[s]}</b>${n} of ${total} reports (${total ? Math.round((100 * n) / total) : 0}%)`)}>
      <rect class="hit" x="${cx - band / 2 + 4}" y="${top}" width="${band - 8}" height="${H - bottom - top}"/>
      ${vbar(cx - barW / 2, barW, y(0), y(n), "mark")}
      <text class="value" x="${cx}" y="${y(n) - 7}" text-anchor="middle">${n}</text></g>
      <text class="label" x="${cx}" y="${H - bottom + 18}" text-anchor="middle">${s}</text>
      <text x="${cx}" y="${H - bottom + 33}" text-anchor="middle">${SCORE_WORDS[s]}</text>`;
  });
  body += `<line class="zero" x1="${left}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`;
  $("distChart").innerHTML = svg(W, H, body, "Number of reports per LLM score");
}

function renderModelCheck() {
  const m = state.run.models;
  const cell = (name, label) => {
    const v = m[name], gain = v.baseline_mae - v.cv_mae;
    return `<div><span>${label}</span><strong>${fmt(v.cv_mae)}</strong><small>held-out error vs ${fmt(v.baseline_mae)} for the median guess (${signed(-gain)})</small></div>`;
  };
  const best = Math.max(m.with_human_rating.baseline_mae - m.with_human_rating.cv_mae,
                        m.without_human_rating.baseline_mae - m.without_human_rating.cv_mae);
  const verdict = best > 0
    ? "At least one variant has lower held-out error than the median guess. Treat the feature rankings as exploratory."
    : "Neither variant improves on the median guess. The feature rankings are unreliable for this run.";
  $("modelCheck").innerHTML = `<div class="model-check">${cell("with_human_rating", "With human rating")}${cell("without_human_rating", "Without human rating")}</div>
    <p class="verdict">${verdict}</p>
    <p class="hint">Error is mean absolute difference in score points across ${m.with_human_rating.n_structures} held-out structures.</p>`;
}

function renderImportance() {
  const model = state.run.models[state.model], rows = model.importance;
  if (!rows.length) { $("importanceChart").innerHTML = `<p class="empty">Not enough data to fit a model.</p>`; return; }
  const W = 860, rowH = 38, top = 26, labelW = 210, rankW = 190, H = top + rows.length * rowH + 30;
  const values = rows.map((r) => r.mae_increase);
  const lo = Math.min(0, ...values), hi = Math.max(0.05, ...values);
  const x = scale(lo, hi, labelW, W - rankW - 40);
  let body = `<text class="axis-title" x="${x(0)}" y="12" text-anchor="middle">0</text>`;
  body += `<text class="axis-title" x="${W - rankW + 10}" y="12">Rank stability (resamples)</text>`;
  rows.forEach((r, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2;
    const features = r.features.map((k) => featureMeta(k).label).join(", ");
    const tipHtml = `<b>${esc(r.group)}</b>${esc(features)}<br>Error increase when shuffled: ${signed(r.mae_increase, 3)} points<br>`
      + `Median rank ${fmt(r.median_rank, 0)} (10–90%: ${fmt(r.rank_p10, 0)}–${fmt(r.rank_p90, 0)})<br>In the top 3 in ${r.top3_share === null ? "—" : Math.round(100 * r.top3_share) + "%"} of resamples`;
    body += `<g class="row" ${tip(tipHtml)} tabindex="0">
      <rect class="hit" x="0" y="${y0 + 2}" width="${W}" height="${rowH - 4}" rx="4"/>
      <text class="label" x="${labelW - 14}" y="${cy + 4}" text-anchor="end">${esc(r.group)}</text>
      ${hbar(x(0), x(r.mae_increase), cy - 8, 16, r.mae_increase > 0 ? "mark" : "mark-muted")}
      <text class="value" x="${x(Math.max(0, r.mae_increase)) + 8}" y="${cy + 4}">${signed(r.mae_increase, 3)}</text>
      <text x="${W - rankW + 10}" y="${cy + 4}">#${fmt(r.median_rank, 0)} · top 3 in ${r.top3_share === null ? "—" : Math.round(100 * r.top3_share) + "%"}</text></g>`;
  });
  body += `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="${top - 4}" y2="${H - 26}"/>`;
  body += `<text class="axis-title" x="${(labelW + W - rankW - 40) / 2}" y="${H - 6}" text-anchor="middle">Increase in held-out error when the group is shuffled (score points)</text>`;
  $("importanceChart").innerHTML = svg(W, H, body, "Permutation importance by feature group");
  $("importanceFoot").textContent = `${model.bootstraps_used} structure-level resamples for rank stability. Related features (e.g. all materials) are shuffled together so they don't split credit.`;
}

function renderCorrelation() {
  const all = state.run.univariate[state.target];
  const rows = all.filter((r) => r.rho !== null), skipped = all.filter((r) => r.rho === null);
  if (!rows.length) { $("corrChart").innerHTML = `<p class="empty">Not enough data for this target.</p>`; return; }
  const W = 860, rowH = 30, top = 26, labelW = 250, valueW = 120, H = top + rows.length * rowH + 40;
  const x = scale(-1, 1, labelW, W - valueW);
  let body = "";
  [-1, -0.5, 0, 0.5, 1].forEach((t) => {
    body += `<line class="${t === 0 ? "zero" : "grid"}" x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${H - 34}"/><text x="${x(t)}" y="${H - 18}" text-anchor="middle">${t}</text>`;
  });
  body += `<text class="axis-title" x="${x(-1)}" y="12">← lower ${state.target === "score" ? "score" : "disagreement"}</text>`;
  body += `<text class="axis-title" x="${x(1)}" y="12" text-anchor="end">higher ${state.target === "score" ? "score" : "disagreement"} →</text>`;
  rows.forEach((r, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2, meta = featureMeta(r.feature);
    const clear = r.ci_low !== null && (r.ci_low > 0 || r.ci_high < 0);
    const tipHtml = `<b>${esc(meta.label)}</b>Spearman ρ = ${signed(r.rho)} (n = ${r.n})<br>95% interval: ${fmt(r.ci_low)} to ${fmt(r.ci_high)}<br>${clear ? "Interval excludes zero." : "Interval includes zero: no clear relationship."}`;
    body += `<g class="row${r.feature === state.feature ? " selected" : ""}" data-feature="${esc(r.feature)}" ${tip(tipHtml)} tabindex="0" role="button" aria-label="Show detail for ${esc(meta.label)}">
      <rect class="hit" x="0" y="${y0 + 2}" width="${W}" height="${rowH - 4}" rx="4"/>
      <text class="label" x="${labelW - 14}" y="${cy + 4}" text-anchor="end">${esc(meta.label)}</text>
      ${r.ci_low !== null ? `<line class="${clear ? "whisker" : "whisker-muted"}" x1="${x(r.ci_low)}" x2="${x(r.ci_high)}" y1="${cy}" y2="${cy}"/>` : ""}
      <circle class="dot" cx="${x(r.rho)}" cy="${cy}" r="5" ${clear ? "" : 'style="fill:#a9bcc4"'}/>
      <text class="value" x="${W - valueW + 16}" y="${cy + 4}">${signed(r.rho)}${clear ? " ●" : ""}</text></g>`;
  });
  body += `<text class="axis-title" x="${(labelW + W - valueW) / 2}" y="${H - 2}" text-anchor="middle">Spearman rank correlation (● interval excludes zero)</text>`;
  $("corrChart").innerHTML = svg(W, H, body, "Correlation of each feature with the target")
    + (skipped.length ? `<p class="hint">Not enough varied data to compute: ${skipped.map((r) => esc(featureMeta(r.feature).label)).join(", ")}.</p>` : "");
  $("corrChart").querySelectorAll("[data-feature]").forEach((g) => {
    const pick = () => { state.feature = g.dataset.feature; $("featureSelect").value = state.feature; renderCorrelation(); renderDetail(); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
}

function renderDetail() {
  const meta = featureMeta(state.feature), table = state.run.table;
  const points = table.filter((t) => t[meta.key] !== null && t[meta.key] !== undefined);
  $("detailIntro").textContent = `${meta.how} Each dot is one report; ${table.length - points.length} report(s) without a value are not shown.`;
  if (!points.length) { $("detailChart").innerHTML = `<p class="empty">No report has a value for this feature.</p>`; return; }
  const W = 860, H = 300, left = 60, right = 20, top = 16, bottom = 48;
  const y = scale(0.5, 4.5, H - bottom, top);
  let body = "";
  [1, 2, 3, 4].forEach((s) => { body += `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(s)}" y2="${y(s)}"/><text x="${left - 10}" y="${y(s) + 4}" text-anchor="end">${s}</text>`; });
  body += `<text class="axis-title" transform="translate(14 ${(H - bottom + top) / 2}) rotate(-90)" text-anchor="middle">LLM score</text>`;
  const dotTip = (t) => `<b>${esc(shortName(t.filename))}</b>${esc(t.structure_group)}<br>${esc(meta.label)}: ${fmt(t[meta.key], meta.kind === "binary" ? 0 : 2)}<br>LLM score ${t.score}${t.score_source === "worst_chunk" ? " (worst chunk)" : ""} · human ${t.human_rating ?? "—"}`;
  let xOf;
  if (meta.kind === "binary") {
    const band = (W - left - right) / 2;
    xOf = (t) => left + band * t[meta.key] + band / 2 + jitter(t.filename, band * 0.35);
    [0, 1].forEach((v) => {
      const cx = left + band * v + band / 2, group = points.filter((t) => t[meta.key] === v);
      body += `<text class="label" x="${cx}" y="${H - bottom + 22}" text-anchor="middle">${v ? "Yes" : "No"} (n = ${group.length})</text>`;
      if (group.length) {
        const mean = group.reduce((a, t) => a + t.score, 0) / group.length;
        body += `<g ${tip(`<b>${v ? "Yes" : "No"}: mean score ${fmt(mean)}</b>${group.length} reports`)}><line class="mean" x1="${cx - band * 0.25}" x2="${cx + band * 0.25}" y1="${y(mean)}" y2="${y(mean)}"/>
          <text class="value" x="${cx + band * 0.25 + 8}" y="${y(mean) + 4}">mean ${fmt(mean, 1)}</text></g>`;
      }
    });
  } else {
    const vals = points.map((t) => t[meta.key]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.06, x = scale(lo - pad, hi + pad, left, W - right);
    xOf = (t) => x(t[meta.key]);
    // Ordinal features get one tick per level; numeric ones get 5 evenly spaced ticks.
    const tickValues = meta.kind === "ordinal" ? Array.from({ length: Math.round(hi - lo) + 1 }, (_, i) => Math.round(lo) + i)
      : Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * i) / 5);
    tickValues.forEach((v) => {
      body += `<text x="${x(v)}" y="${H - bottom + 20}" text-anchor="middle">${meta.kind === "ordinal" || Math.abs(hi - lo) > 20 ? Math.round(v) : v.toFixed(1)}</text>`;
    });
    body += `<text class="axis-title" x="${(left + W - right) / 2}" y="${H - 8}" text-anchor="middle">${esc(meta.label)}</text>`;
  }
  points.forEach((t) => {
    const cx = xOf(t), cy = y(t.score) + jitter(t.filename + "y", 18);
    body += `<g ${tip(dotTip(t))} tabindex="0"><circle class="hit" cx="${cx}" cy="${cy}" r="11"/><circle class="dot" cx="${cx}" cy="${cy}" r="5"/></g>`;
  });
  $("detailChart").innerHTML = svg(W, H, body, `LLM score against ${meta.label}`);
}

function renderTables() {
  const r = state.run;
  const head = "<tr><th>Report</th><th>Structure</th><th>LLM score</th><th>Score from</th><th>Confidence</th><th>Human rating</th><th>Disagreement</th><th>Human rating text</th></tr>";
  $("reportTable").innerHTML = head + r.table.slice().sort((a, b) => b.score - a.score || a.filename.localeCompare(b.filename)).map((t) =>
    `<tr><td>${esc(shortName(t.filename))}</td><td>${esc(t.structure_group)}</td><td class="numeric">${t.score}</td>
     <td>${t.score_source === "worst_chunk" ? "worst chunk" : "whole report"}</td><td>${esc(t.confidence)}</td>
     <td class="numeric">${t.human_rating ?? "—"}</td><td class="numeric">${signed(t.disagreement, 0)}</td>
     <td>${esc((t.human_rating_raw || "—").slice(0, 160))}</td></tr>`).join("");
  $("excluded").innerHTML = r.excluded.length
    ? `<strong>Excluded (${r.excluded.length})</strong><ul>${r.excluded.map((e) => `<li>${esc(shortName(e.filename))}: ${esc(e.reason)}</li>`).join("")}</ul>` : "";
  $("featureTable").innerHTML = "<tr><th>Feature</th><th>Group</th><th>Kind</th><th>Filled</th><th>How it was coded</th></tr>"
    + r.features.map((f) => `<tr><td>${esc(f.label)}<br><code>${esc(f.key)}</code></td><td>${esc(f.group)}</td><td>${esc(f.kind)}</td>
      <td class="numeric">${f.n_nonmissing} / ${r.n_analyzed}</td><td>${esc(f.how)}</td></tr>`).join("");
}

function renderAll() {
  tips.length = 0;
  renderHeader(); renderDistribution(); renderModelCheck(); renderImportance(); renderCorrelation(); renderDetail(); renderTables();
}

function selectRun(name) {
  state.run = state.data.runs.find((r) => r.run === name);
  const select = $("featureSelect");
  select.innerHTML = state.run.features.map((f) => `<option value="${esc(f.key)}">${esc(f.label)}</option>`).join("");
  if (!state.run.features.some((f) => f.key === state.feature)) state.feature = state.run.features[0].key;
  select.value = state.feature;
  renderAll();
}

function bindToggles(attr, key) {
  document.querySelectorAll(`[data-${attr}]`).forEach((button) => button.addEventListener("click", () => {
    state[key] = button.dataset[attr];
    document.querySelectorAll(`[data-${attr}]`).forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    tips.length = 0; renderAll();
  }));
}

async function init() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    if (!state.data.runs?.length) throw new Error("No scoring runs in feature_importance.json yet.");
  } catch (error) {
    $("loadError").hidden = false;
    $("loadError").textContent = `Could not load the published analysis (${error.message}). Refresh the page to try again.`;
    return;
  }
  const runs = state.data.runs.slice().sort((a, b) => b.generated_utc.localeCompare(a.generated_utc));
  $("runSelect").innerHTML = runs.map((r) => `<option value="${esc(r.run)}">${esc(r.run)} · ${r.n_analyzed} reports</option>`).join("");
  $("runSelect").addEventListener("change", (e) => selectRun(e.target.value));
  $("featureSelect").addEventListener("change", (e) => { state.feature = e.target.value; tips.length = 0; renderAll(); });
  bindToggles("model", "model");
  bindToggles("target", "target");
  selectRun(runs[0].run);
}
init();
