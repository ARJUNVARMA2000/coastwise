"use strict";
// Answer key: three questions for Mira first, then the rows a person checked, then the agreed rules.
// Data: data.json, built by dashboard/build_coastwise_pages.py in the msds-capstone repo.

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nice = t => String(t || "").replace(/_/g, " ");
const OUTCOME = {
  match: ["ok", "Matches the PDF"],
  kept: ["ok", "Kept after review"],
  corrected: ["info", "Fixed after the check"],
  removed: ["bad", "Removed, not a defect"],
  open: ["wait", "Question 3"],
};
const VIEWS = [["changed", "What changed"], ["match", "Matched"], ["all", "All"]];
let DATA = null, view = "changed";

function renderQuestions() {
  $("questions").innerHTML = DATA.questions.map((q, i) => `
    <article class="ak-q" id="${q.id}">
      <h3><span class="qn">${i + 1}.</span> ${esc(q.title)}</h3>
      <dl class="ak-now"><dt>Today</dt><dd>${esc(q.today)}</dd><dt>Example</dt><dd>${esc(q.example)}</dd></dl>
      <div class="ak-options">${q.options.map((o, j) => `
        <div${j === 0 ? ` class="current"` : ""}>
          <span class="letter">${i + 1}${"AB"[j]}</span>
          <span class="what"><strong>${esc(o.label)}</strong><span>${esc(o.note)}</span></span>
          <span class="impact">${esc(o.impact)}</span>
        </div>`).join("")}
      </div>
    </article>`).join("");
}

function renderOutcomes() {
  const order = ["match", "kept", "corrected", "removed", "open"];
  const changed = DATA.checked.filter(c => !unchanged(c)).length;
  $("rows-headline").innerHTML = `<strong>${changed} of ${DATA.checked.length}</strong> randomly chosen rows needed a change. Each fix was applied to the whole report.`;
  const reports = [...new Set(DATA.checked.map(c => c.report))];
  const bars = reports.map(r => {
    const rows = DATA.checked.filter(c => c.report === r);
    return `<div class="ak-obar"><span class="name">${esc(r)}</span>
      <span class="stack" role="img" aria-label="${esc(r)}: ${order.map(o => `${rows.filter(c => c.outcome === o).length} ${OUTCOME[o][1].toLowerCase()}`).join(", ")}">${
        order.map(o => { const n = rows.filter(c => c.outcome === o).length;
          return n ? `<span class="seg ${o}" style="flex:${n}" title="${OUTCOME[o][1]}: ${n}">${n}</span>` : ""; }).join("")}</span>
      <span class="total">${rows.length} rows</span></div>`;
  }).join("");
  const legend = order.filter(o => DATA.checked.some(c => c.outcome === o))
    .map(o => `<li><span class="key ${o}"></span>${o === "open" ? "Open, see question 3" : OUTCOME[o][1]} <b>${DATA.checked.filter(c => c.outcome === o).length}</b></li>`).join("");
  $("outcomes").innerHTML = bars + `<ul class="ak-legend">${legend}<li class="pending"><span class="key"></span>DSNY 2020–25: being checked now</li></ul>`;
}

function renderRules() {
  $("rules").innerHTML = DATA.rules.map(r => `<li>${esc(r.title)}<span>${esc(r.example)}</span></li>`).join("");
}

const unchanged = c => c.outcome === "match" || c.outcome === "kept";
const inView = c => view === "all" || (view === "match" ? unchanged(c) : !unchanged(c));

function renderFilter() {
  const n = v => DATA.checked.filter(c => v === "all" || (v === "match" ? unchanged(c) : !unchanged(c))).length;
  $("filter").innerHTML = VIEWS.map(([v, label]) =>
    `<button type="button" data-v="${v}" aria-pressed="${view === v}">${label} <span class="n">${n(v)}</span></button>`).join("");
  $("filter").querySelectorAll("button").forEach(b => b.addEventListener("click", () => { view = b.dataset.v; renderFilter(); renderRows(); }));
}

function valueText(a) {
  return [nice(a.type), a.severity, a.attributes, a.cause && "cause: " + a.cause].filter(Boolean).join(", ");
}

function change(c) {
  if (c.outcome === "removed") {
    return `<dt>Was</dt><dd><del>${esc(nice(c.before.type))}, ${esc(c.before.severity)}</del></dd><dt>Now</dt><dd>No defect row</dd>`;
  }
  const now = c.after.map(a => esc(valueText(a))).join("<br>");
  if (c.outcome === "corrected") {
    return `<dt>Was</dt><dd><del>${esc(nice(c.before.type))}, ${esc(c.before.severity)}</del></dd><dt>Now</dt><dd>${now}</dd>`;
  }
  return `<dt>Recorded</dt><dd>${now}</dd>`;
}

function renderRows() {
  const list = DATA.checked.filter(inView);
  $("rows").innerHTML = list.map((c, i) => {
    const [tone, label] = OUTCOME[c.outcome];
    return `<article class="ak-row">
      <button type="button" class="crop" data-i="${i}" aria-label="Enlarge the ${esc(c.report)} page ${c.page} excerpt">
        <img src="${esc(c.img)}" alt="" loading="lazy" width="440" height="300"></button>
      <div>
        <p class="where"><span class="tag ${tone}">${label}</span> ${esc(c.report)}, page ${c.page}</p>
        <h3>${esc(c.element)}</h3>
        <p class="said">“${esc(c.quote)}”</p>
        <dl>${change(c)}</dl>
      </div></article>`;
  }).join("");
  $("rows").querySelectorAll("button.crop").forEach(b => b.addEventListener("click", () => openCrop(list[+b.dataset.i])));
}

function openCrop(c) {
  $("viewer-title").textContent = `${c.report}, page ${c.page}`;
  $("viewer-quote").textContent = c.quote;
  $("viewer-img").alt = `Excerpt of ${c.report} page ${c.page}, with the quoted words highlighted`;
  $("viewer-img").src = c.img;
  $("viewer").showModal();
}

fetch("data.json", { credentials: "same-origin" })
  .then(r => { if (!r.ok) throw new Error(r.status === 401 ? "Sign in to see the answer key." : `Could not load the answer key (error ${r.status}). Reload the page to try again.`); return r.json(); })
  .then(data => {
    DATA = data;
    renderQuestions(); renderOutcomes(); renderFilter(); renderRows(); renderRules();
    $("viewer-close").addEventListener("click", () => $("viewer").close());
    $("viewer").addEventListener("click", e => { if (e.target === $("viewer")) $("viewer").close(); });
    $("loading").hidden = true;
    $("content").hidden = false;
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  })
  .catch(err => { $("loading").hidden = true; $("load-error").hidden = false; $("load-error").textContent = err.message; });
