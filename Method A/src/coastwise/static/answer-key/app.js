"use strict";
// Answer key: status, questions for Mira, agreed rules and the spot-checked rows.
// Data: data.json, built by dashboard/build_coastwise_pages.py in the msds-capstone repo.

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nice = t => String(t || "").replace(/_/g, " ");
const OUTCOME = {
  match: ["ok", "Matches the PDF"],
  corrected: ["info", "Corrected after check"],
  removed: ["bad", "Removed: not a defect"],
  open: ["wait", "Open question"],
};
let DATA = null;
const view = { report: "All", outcome: "All" };

function renderStatus() {
  $("status").innerHTML = DATA.status.map(s => `<div>
    <strong>${esc(s.report)}</strong>
    <span class="chip ${s.state === "In review" ? "wait" : "ok"}">${esc(s.state)}</span>
    <span class="detail">${esc(s.detail)}</span></div>`).join("");
}

function renderQuestions() {
  $("questions").innerHTML = DATA.questions.map((q, i) => `
    <article class="panel ak-q" id="${q.id}">
      <div class="head"><span class="qn">Question ${i + 1}</span><h3>${esc(q.title)}</h3></div>
      <p>${esc(q.context)}</p>
      <p class="example">${esc(q.example)}</p>
      <div class="ak-options">${q.options.map((o, j) => `
        <div class="${j === 0 ? "current" : ""}"><strong>${esc(o.label)}</strong><span>${esc(o.effect)}</span>${j === 0 ? `<span class="tag">What the answer key does today</span>` : ""}</div>`).join("")}
      </div>
      <p class="why">${esc(q.why)}</p>
    </article>`).join("");
}

function renderRules() {
  $("rules").innerHTML = DATA.rules.map(r => `<li>${esc(r.title)}<span>${esc(r.example)}</span></li>`).join("");
}

function renderFilter() {
  const reports = ["All", ...new Set(DATA.checked.map(c => c.report))];
  const count = o => DATA.checked.filter(c => (view.report === "All" || c.report === view.report) && (o === "All" || c.outcome === o)).length;
  const outcomes = ["All", ...Object.keys(OUTCOME).filter(o => DATA.checked.some(c => c.outcome === o))];
  $("filter").innerHTML =
    reports.map(r => `<button type="button" data-k="report" data-v="${r}" aria-pressed="${view.report === r}">${r === "All" ? "Both reports" : r}</button>`).join("") +
    `<span aria-hidden="true" style="width:1px;background:var(--line);margin:0 4px"></span>` +
    outcomes.map(o => `<button type="button" data-k="outcome" data-v="${o}" aria-pressed="${view.outcome === o}">${o === "All" ? "Any result" : OUTCOME[o][1]} (${count(o)})</button>`).join("");
  $("filter").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    view[b.dataset.k] = b.dataset.v;
    renderFilter(); renderRows();
  }));
}

function changeLines(c) {
  if (c.outcome === "removed") {
    return `<dt>Before</dt><dd><del>${esc(nice(c.before.type))}, ${esc(c.before.severity)}</del></dd><dt>Now</dt><dd>No defect row</dd>`;
  }
  const after = c.after.map(a => {
    const bits = [nice(a.type), a.severity, a.attributes, a.cause && "cause: " + a.cause].filter(Boolean);
    return esc(bits.join(", "));
  }).join("<br>");
  const before = `${nice(c.before.type)}, ${c.before.severity}`;
  if (c.outcome === "corrected") return `<dt>Before</dt><dd><del>${esc(before)}</del></dd><dt>Now</dt><dd>${after}</dd>`;
  return `<dt>Answer key</dt><dd>${after}</dd>`;
}

function renderRows() {
  const list = DATA.checked.filter(c => (view.report === "All" || c.report === view.report) && (view.outcome === "All" || c.outcome === view.outcome));
  $("rows").innerHTML = list.map((c, i) => {
    const [tone, label] = OUTCOME[c.outcome];
    return `<article class="panel ak-row">
      <button type="button" class="crop" data-i="${i}" aria-label="Enlarge the highlighted PDF crop for ${esc(c.report)} page ${c.page}"><img src="${esc(c.img)}" alt="" loading="lazy"></button>
      <div>
        <div class="meta"><span class="where">${esc(c.report)} · p.${c.page} · ${c.kind === "table" ? "table row" : "report text"}</span><span class="chip ${tone}">${label}</span></div>
        <h3 style="margin-top:6px">${esc(c.element)}</h3>
        <blockquote>${esc(c.quote)}</blockquote>
        <dl>${changeLines(c)}</dl>
        ${c.outcome === "open" ? `<p class="note">See question 3 above.</p>` : ""}
      </div></article>`;
  }).join("") || `<p class="message">No rows match.</p>`;
  $("rows").querySelectorAll("button.crop").forEach(b => b.addEventListener("click", () => openCrop(list[+b.dataset.i])));
}

function openCrop(c) {
  $("viewer-title").textContent = `${c.report}, PDF page ${c.page}`;
  $("viewer-quote").textContent = c.quote;
  $("viewer-img").alt = `Highlighted crop of ${c.report} page ${c.page}`;
  $("viewer-img").src = c.img;
  $("viewer").showModal();
}

fetch("data.json", { credentials: "same-origin" })
  .then(r => { if (!r.ok) throw new Error(r.status === 401 ? "Sign in to see the answer key." : `Could not load the answer key (${r.status}).`); return r.json(); })
  .then(data => {
    DATA = data;
    renderStatus(); renderQuestions(); renderRules(); renderFilter(); renderRows();
    $("viewer-close").addEventListener("click", () => $("viewer").close());
    $("viewer").addEventListener("click", e => { if (e.target === $("viewer")) $("viewer").close(); });
    $("loading").hidden = true;
    $("content").hidden = false;
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  })
  .catch(err => { $("loading").hidden = true; $("load-error").hidden = false; $("load-error").textContent = err.message; });
