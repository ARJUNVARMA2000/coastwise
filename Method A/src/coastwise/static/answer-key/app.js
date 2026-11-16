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
      <p>${esc(q.context)}</p>
      <p class="example">${esc(q.example)}</p>
      <div class="ak-options">${q.options.map((o, j) => `
        <div${j === 0 ? ` class="current"` : ""}>
          <strong><span class="letter">${i + 1}${"AB"[j]}</span>${esc(o.label.replace(/^[AB]\.\s*/, ""))}</strong>
          <span>${esc(o.effect)}</span>${j === 0 ? `<span class="today">Today</span>` : ""}
        </div>`).join("")}
      </div>
    </article>`).join("");
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
    const reports = [...new Set(data.checked.map(c => c.report))].join(" and ");
    const changed = data.checked.filter(c => !unchanged(c)).length;
    $("rows-intro").textContent = `${data.checked.length} random rows from ${reports} were checked against the PDF on 15 November. ` +
      `${changed} needed a change, and each fix was applied to the whole report. DSNY 2020–25 is being checked now.`;
    renderQuestions(); renderFilter(); renderRows(); renderRules();
    $("viewer-close").addEventListener("click", () => $("viewer").close());
    $("viewer").addEventListener("click", e => { if (e.target === $("viewer")) $("viewer").close(); });
    $("loading").hidden = true;
    $("content").hidden = false;
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  })
  .catch(err => { $("loading").hidden = true; $("load-error").hidden = false; $("load-error").textContent = err.message; });
