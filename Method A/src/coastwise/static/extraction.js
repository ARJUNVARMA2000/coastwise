'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const MISSING = {NR: 'Not reported', NA: 'Not applicable', NONE: 'None found'};
const PAGE = 50;
let data, current = null, currentTab, routeRequest = 0;

async function getJson(url) {
  const response = await fetch(url, {headers: {'X-Coastwise-Request': '1'}});
  if (!response.ok) throw new Error(response.status === 401 ? 'Sign in to the workspace first.' : `Request failed (${response.status})`);
  return response.json();
}

// A value as written, or a plain-language missing status.
function value(text) {
  if (!text || MISSING[text]) return `<span class="x-missing">${MISSING[text] || '—'}</span>`;
  return esc(text);
}

// Source chips such as "p12 ¶6" open the cited paragraph on its PDF page.
function chips(src, rowId, field) {
  if (!src) return '';
  return src.split(';').map(s => s.trim()).filter(Boolean).map(label => {
    const match = label.match(/p(\d+) ¶(\d+)$/);
    if (!match) return `<span class="x-chip">${esc(label)}</span>`;
    return `<button type="button" class="x-chip" data-row="${esc(rowId)}" data-field="${esc(field)}" data-page="${match[1]}" data-para="${match[2]}"
      aria-label="Show source: page ${match[1]}, paragraph ${match[2]}">p${match[1]} ¶${match[2]}</button>`;
  }).join(' ');
}
const cell = (row, field, rowId) => `${value(row[field])}${row[field + '_src'] ? '<br>' + chips(row[field + '_src'], rowId, field) : ''}`;
// Order rows as they appear in the PDF: first cited page, then paragraph.
const firstCite = src => { const m = String(src || '').match(/p(\d+) ¶(\d+)/); return m ? Number(m[1]) * 10000 + Number(m[2]) : 1e9; };
const docOrder = (a, b, keys) => Math.min(...keys.map(k => firstCite(a[k]))) - Math.min(...keys.map(k => firstCite(b[k])));

function params() { return new URLSearchParams(location.search); }
function reportEntries() {
  return [...(data.reports || [])].sort((a, b) => String(b.report_year).localeCompare(String(a.report_year)));
}

function publicationNotice(warnings, title, collapsed = false) {
  const notes = [...new Set((warnings || []).filter(Boolean))];
  if (!notes.length) return '';
  const content = `<ul>${notes.map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
  return collapsed
    ? `<details class="x-publication-warning"><summary>${esc(title)} · ${notes.length}</summary>${content}</details>`
    : `<strong>${esc(title)}</strong>${content}`;
}

function renderSide() {
  const q = $('x-search').value.trim().toLowerCase();
  const selected = params().get('inspection');
  $('x-structures').innerHTML = '<ul class="x-report-list">' + reportEntries().filter(i =>
    !q || (i.source_file + ' ' + i.report_year).toLowerCase().includes(q)).map(i =>
    `<li><a href="?inspection=${encodeURIComponent(i.inspection_id)}" data-route
      ${i.inspection_id === selected || i.inspection_ids.includes(selected) ? 'aria-current="true"' : ''}>
      <b>${esc(i.report_year || 'Undated')}</b><span>${esc(i.source_file)}</span>
      <small>${esc(i.n_defects)} defects · latest saved extraction</small></a></li>`).join('') + '</ul>';
}

const TABS = [['defects', 'Defects'], ['components', 'Components'], ['notes', 'Review notes']];
const GRADES = {1: 'Minor', 2: 'Moderate', 3: 'Advanced', 4: 'Severe'};
const STATUSES = {
  graded: 'Graded', uncertain: 'Uncertain', insufficient_evidence: 'Uncertain', failed: 'Uncertain · processing failed',
  legacy_ungraded: 'Uncertain · ungraded', pending: 'Uncertain · pending',
};
const humanize = text => String(text || '').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const reported = text => Boolean(text && !MISSING[text]);
const list = item => Array.isArray(item) ? item : (item ? JSON.parse(item) : []);
const componentName = o => [o?.component_name, o?.identifier].filter(reported).join(' ');
const typeLabel = d => reported(d.defect_type) ? d.defect_type : 'Defect type not reported';
const gradeLabel = d => d.damage_grade ? `${d.damage_grade} ${d.damage_label || GRADES[d.damage_grade]}` : (STATUSES[d.grading_status] || 'Uncertain');
const gradeBadge = d => `<span class="x-grade" data-grade="${esc(d.damage_grade || d.grading_status)}">${esc(gradeLabel(d))}</span>`;
const reportRating = d => reported(d.severity) && (!d.rating_scope || d.rating_scope === 'defect')
  ? esc(d.severity) : '<span class="x-missing">Not reported</span>';
const originLabel = i => i.origin === 'replay' ? 'Graded replay' : i.origin === 'upload' ? 'Uploaded' : 'Historical extraction';

function defectUrl(id) {
  const query = params();
  query.set('defect', id);
  return '?' + query;
}

function ranges(pages) {
  const spans = [];
  for (const page of [...new Set(pages.map(Number))].sort((a, b) => a - b)) {
    const previous = spans.at(-1);
    if (previous && page === previous[1] + 1) previous[1] = page;
    else spans.push([page, page]);
  }
  return spans.map(([start, end]) => start === end ? start : `${start}–${end}`).join(', ');
}

function renderBrowse() {
  const reports = reportEntries();
  $('x-main').innerHTML = `<h2>Your Method A reports</h2>
    <p class="x-lede">Open one report to see all its saved defect records, original ratings, uncertainty and PDF evidence. Coverage limitations remain explicit.</p>
    <div class="x-pilots">${reports.map(i => {
      const manifest = data.manifest.find(m => m.source_sha256 === i.source_sha256 && m.origin === i.origin);
      return `<a class="x-pilot" href="?inspection=${encodeURIComponent(i.inspection_id)}" data-route>
        <span class="eyebrow">${i.coverage === 'complete' ? 'Results ready' : 'Partial results'}</span><strong>${esc(i.source_file)} <span aria-hidden="true">→</span></strong>
        <span>${esc(i.n_defects)} defects · ${list(manifest?.selected_pdf_pages).length ? 'PDF pp. '+esc(ranges(list(manifest.selected_pdf_pages))) : esc(i.pdf_pages)+' PDF pages'}</span></a>`;
    }).join('') || '<p class="x-empty">No Method A reports yet. <a href="/?new=1">Upload a native-text PDF</a> to extract its defects.</p>'}</div>`;
}

function coverageNotice(i, m) {
  const selected = list(m.selected_pdf_pages);
  const selectedSet = new Set(selected.map(Number));
  const allPages = selectedSet.size === Number(i.pdf_pages) &&
    Array.from({length: Number(i.pdf_pages)}, (_, n) => n + 1).every(page => selectedSet.has(page));
  const selectionOnly = selected.length > 0 && !allPages;
  const lowText = list(m.low_text_pdf_pages);
  const context = list(m.context_pdf_pages);
  const partial = i.coverage !== 'complete' || lowText.length;
  let title = allPages ? `All ${i.pdf_pages} PDF pages in scope` : selectionOnly
    ? `Selected findings pages ${ranges(selected)} of ${i.pdf_pages}`
    : i.coverage === 'complete' ? `All ${i.pdf_pages} PDF pages in scope`
    : i.document_kind === 'reference_only' ? 'Reference document · no defect extraction'
    : `Partial coverage · ${i.pdf_pages}-page PDF`;
  if (selectionOnly && !['sample_complete', 'complete'].includes(i.coverage)) title += ' · incomplete extraction';
  if (allPages && !['sample_complete', 'complete'].includes(i.coverage)) title += ' · incomplete extraction';
  const notes = [
    selectionOnly ? 'Defect counts apply to this selection.' : '',
    context.length ? `Context pages ${ranges(context)}.` : '',
    lowText.length ? `Low or no native text on pages ${ranges(lowText)}.` : '',
    m.failed_pages ? `Failed pages ${m.failed_pages}.` : '',
    list(m.body_coverage_gaps).length ? 'See review notes for extraction gaps.' : '',
    i.pdf_available ? '' : 'Source PDF unavailable on this server.',
  ];
  return `<div class="x-coverage ${partial ? 'x-coverage-partial' : ''}">
    <strong>${esc(title)}</strong><span>${esc(notes.filter(Boolean).join(' '))}</span></div>`;
}

function downloadLinks(inspection) {
  const base = `/api/extraction/download?scope=method_a_one_shot&amp;inspection=${encodeURIComponent(inspection)}&amp;format=`;
  return `<details class="x-download"><summary>Download inspection</summary><div>
    <a href="${base}csv">Defects CSV</a><a href="${base}json">JSON</a>
    <a href="${base}xlsx">Excel</a><a href="${base}zip">All tables ZIP</a></div></details>`;
}

function filterSelect(id, label, options, all = 'All') {
  return `<label>${label}<select id="x-${id}"><option value="">${all}</option>
    ${options.map(([key, text]) => `<option value="${esc(key)}">${esc(text)}</option>`).join('')}</select></label>`;
}

async function recordReadyTime() {
  const target = $('x-ready-time');
  if (!target || !current) return;
  const renderedAt = Date.now();
  const inspectionId=current.inspection.inspection_id;
  const run = params().get('run') || current.inspection.run_id;
  let metrics = null;
  try { if(run)metrics=(await getJson(`/api/runs/${encodeURIComponent(run)}/result`)).metrics; } catch { /* Table and exports remain usable if usage cannot load. */ }
  if(current?.inspection.inspection_id!==inspectionId||target!==$('x-ready-time'))return;
  const duration = n => n < 60 ? n.toFixed(1) + ' seconds' : `${Math.floor(n / 60)}m ${(n % 60).toFixed(1)}s`;
  const usage=metrics?` ${metrics.requests??metrics.request_count??'Unknown'} requests · ${metrics.billed_usd==null?'cost unavailable':'$'+Number(metrics.billed_usd).toFixed(5)}.`:'';
  let timing;
  try { timing = run && JSON.parse(sessionStorage.getItem('coastwise-method-a-' + run) || 'null'); } catch { timing = null; }
  if (!timing?.processingStartedAt || timing.preview) {
    const elapsed=metrics?.upload_elapsed_seconds;
    target.textContent = elapsed==null
      ? `Saved defect table · ${metrics?.runtime_seconds==null?'upload-to-table elapsed time unavailable':duration(metrics.runtime_seconds)+' server processing; upload-to-table elapsed time unavailable'}.${usage}`
      : `Saved ${metrics.observed_complete?'complete':'partial'} table · upload to table ${duration(elapsed)}.${usage}`;
    return;
  }
  const readyAt = timing.readyAt || renderedAt;
  const processing = Math.max(0, (readyAt - timing.processingStartedAt) / 1000);
  const upload = timing.uploadStartedAt ? Math.max(0, (readyAt - timing.uploadStartedAt) / 1000) : null;
  const complete = current.inspection.coverage === 'complete' && !['partial', 'failed'].includes(current.inspection.grading_status);
  target.textContent = `${complete ? 'Complete graded table ready' : 'Partial table available · completion target not met'} · ${upload == null ? '' : 'upload to table ' + duration(upload) + ' · '}processing to table ${duration(processing)}. Includes table rendering, ready downloads and any wait before starting.${usage}`;
  if (timing.readyAt) return;
  try { sessionStorage.setItem('coastwise-method-a-' + run, JSON.stringify({...timing, readyAt})); } catch { /* Timing remains visible if browser storage is unavailable. */ }
  const payload = {processing_elapsed_seconds: processing, ...(upload == null ? {} : {upload_elapsed_seconds: upload})};
  fetch(`/api/runs/${encodeURIComponent(run)}/ready`, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Coastwise-Request': '1'}, body: JSON.stringify(payload),
  }).catch(() => {});
}

function renderInspection(tab) {
  const {inspection: i, observations, defects, manifest, review} = current;
  const structure = data.structures.find(s => s.structure_id === i.structure_id) || {};
  const facts = [['Inspecting firm', 'inspecting_firm'], ['Inspection type', 'inspection_type'],
    ['Inspection-level condition', 'overall_condition'], ['Inspection rating scale', 'rating_scale'],
    ['Repairs since last inspection', 'repairs_since_last'], ['Urgency', 'urgency']];
  const counts = {components: observations.length, defects: defects.length, notes: review.length};
  const options = values => [...new Set(values.filter(Boolean))].sort().map(v => [v, v]);
  const grading = {failed: 'Grading failed. Extracted defects are available.', partial: 'Some assessments could not be completed.',
    legacy_ungraded: 'Historical extraction · project grading unavailable.'};
  $('x-main').innerHTML = `<div class="x-inspection-head"><div>
    <p class="eyebrow">${esc(i.report_year || 'Undated')} inspection · ${originLabel(i)}</p>
    <h2 class="x-title" id="x-inspection-title" tabindex="-1">${esc(i.report_name || structure.name || i.structure_id)}</h2>
    <p class="x-file">${esc(i.source_file)}</p>
    <p class="x-date">Inspection date ${reported(i.inspection_date) ? esc(i.inspection_date) : 'not reported'}
      ${chips(i.inspection_date_src, i.inspection_id, 'inspection_date')}</p></div>
    ${downloadLinks(i.inspection_id)}</div>
    <p class="message" id="x-route-error" role="alert" hidden></p>
    ${coverageNotice(i, manifest)}
    ${publicationNotice(list(manifest.publication_warnings), 'Known report limitations', true)}
    <p class="x-timing" id="x-ready-time" role="status"></p>
    <div class="x-inspection-meta">
      <span>Method A · source-linked findings and provisional grades</span>
      ${grading[i.grading_status] || (i.origin === 'dataset' ? grading.legacy_ungraded : '')}
    </div>
    <details class="x-report-facts"><summary>Report details</summary>
      <dl class="x-facts">${facts.map(([name, f]) => `<div><dt>${name}</dt><dd>${cell(i, f, i.inspection_id)}</dd></div>`).join('')}</dl>
      ${list(i.scoped_conditions).map(r => `<p>${esc(humanize(r.scope))} condition · ${esc(r.scope_name || r.name || r.section || '')} · ${esc(r.condition)}</p>`).join('')}
    </details>
    <nav class="x-tabs" aria-label="Inspection views">${TABS.map(([t, name]) =>
      `<a href="?inspection=${encodeURIComponent(i.inspection_id)}&amp;tab=${t}" data-route ${t === tab ? 'aria-current="page"' : ''}>${name}${counts[t] == null ? '' : ` <span>${counts[t]}</span>`}</a>`).join('')}</nav>
    ${tab === 'defects' ? `<div class="x-legend" aria-label="Damage grade legend"><strong>Project grade</strong>
      ${Object.entries(GRADES).map(([n, name]) => `<span data-grade="${n}">${n} ${name}</span>`).join('<span class="x-arrow" aria-hidden="true">→</span>')}</div>` : ''}
    <div class="toolbar"><label class="search-field">${tab === 'defects' ? 'Search defects' : 'Search this table'}
      <input id="x-filter" type="search" value="${esc(params().get('q') || '')}" ${tab === 'defects' ? 'placeholder="Type, location or finding"' : ''}></label>
    ${tab === 'defects' ? [
      filterSelect('component', 'Component', options(observations.map(componentName))),
      filterSelect('type', 'Defect type', options(defects.map(typeLabel))),
      filterSelect('grade', 'Grade', Object.entries(GRADES).map(([n, name]) => [n, `${n} ${name}`])),
      filterSelect('assessment', 'Assessment status', Object.entries(STATUSES).filter(([key]) => key !== 'insufficient_evidence'), 'All statuses'),
    ].join('') : ''}
    </div>
    <p class="row-count" id="x-count" role="status"></p>
    <div id="x-table"></div><nav class="pagination" id="x-pages" aria-label="Table pages"></nav>`;
  renderTable(tab);
  $('x-filter').oninput = () => renderTable(tab);
  $('x-main').querySelectorAll('.toolbar select').forEach(select => select.onchange = () => renderTable(tab));
}

function measurementValue(m) {
  return `<b>${esc(m.value)}${reported(m.unit) ? ' ' + esc(m.unit) : ''}</b>${m.unit === 'NR' ? ' <span class="x-missing">unit not reported</span>' : ''}`;
}

function defectTable(defects, observations, measurements, matches) {
  const byDefect = {};
  measurements.forEach(m => (byDefect[m.defect_id] ||= []).push(m));
  const component = $('x-component').value, type = $('x-type').value;
  const grade = $('x-grade').value, status = $('x-assessment').value;
  const rows = defects.filter(d => {
    const o = observations[d.obs_id];
    return (!component || componentName(o) === component) && (!type || typeLabel(d) === type)
      && (!grade || String(d.damage_grade) === grade) && (!status || (status === 'uncertain'
        ? ['uncertain', 'insufficient_evidence'].includes(d.grading_status) : d.grading_status === status))
      && matches({...d, component: componentName(o), section: o?.section, parent: o?.parent_area,
                  gradeLabel: gradeLabel(d)});
  }).sort((a, b) => docOrder(a, b, ['findings_text_src', 'defect_type_src', 'severity_src']));
  const head = ['Finding', 'Component & location', 'Original rating', 'Project grade', 'Measurements', 'Quote & PDF'];
  const line = d => {
    const o = observations[d.obs_id], readings = byDefect[d.defect_id] || [];
    const site = [o?.parent_area, o?.section, d.location].filter(reported);
    const findings = current.citations.filter(c => c.row_id === d.defect_id && c.field === 'findings_text');
    const pages = [...new Set(findings.map(c => Number(c.page)))];
    const link = defectUrl(d.defect_id);
    return [
      `<a class="x-defect-link" data-defect="${esc(d.defect_id)}" data-route href="${esc(link)}">${esc(typeLabel(d))}</a>
        ${reported(d.temporal_status) && d.temporal_status !== 'current' ? `<span class="x-badge">${esc(humanize(d.temporal_status))}</span>` : ''}
        <p class="x-text">${esc(d.findings_text)}</p>`,
      `<b>${esc(componentName(o)) || '—'}</b><small class="x-scale">${esc([...new Set(site)].join(' · '))}</small>`,
      `${reportRating(d)}${reported(d.severity_scale) ? `<small class="x-scale">${esc(d.severity_scale)}</small>` : ''}`,
      `${gradeBadge(d)}<small class="x-grade-reason">${esc(d.assessment_rationale || 'Supporting rule or evidence unavailable.')}</small>`,
      readings.map(m => `<div class="x-measure"><small>${esc(m.property)}</small> ${measurementValue(m)}</div>`).join('') || '—',
      pages.length ? `${findings.find(c=>c.verified_quote)?.verified_quote ? `<blockquote class="x-table-quote">${esc(findings.find(c=>c.verified_quote).verified_quote)}</blockquote>` : '<small class="x-missing">Quotation needs review</small>'}<a class="x-chip" data-route data-source="${esc(findings.find(c => c.verified_quote)?.evidence_id || findings[0].evidence_id)}"
        href="${esc(link)}" aria-label="Open PDF evidence for ${esc(typeLabel(d))}">p. ${esc(ranges(pages))}</a>` : '<span class="x-missing">Evidence unavailable</span>',
    ].map((content, index) => `<td role="cell" data-label="${head[index]}">${content}</td>`).join('');
  };
  return {rows, head, line};
}

function renderTable(tab, page = 0) {
  const {observations, defects, measurements, review} = current;
  const q = ($('x-filter')?.value || '').toLowerCase();
  const matches = row => !q || Object.values(row).join(' ').toLowerCase().includes(q);
  const obsName = Object.fromEntries(observations.map(o => [o.obs_id, o]));
  let rows, head, line;
  if (tab === 'components') {
    rows = observations.filter(matches).sort((a, b) => docOrder(a, b, ['component_name_src', 'findings_text_src', 'condition_src']));
    head = ['Component', 'Section', 'Reported condition', 'Findings', 'Defects'];
    line = o => `<td><b>${esc(o.component_name)}</b>${o.identifier ? ' <span class="x-id">' + esc(o.identifier) + '</span>' : ''}<br>${chips(o.component_name_src, o.obs_id, 'component_name')}</td>
      <td>${cell(o, 'section', o.obs_id)}</td>
      <td>${value(o.condition)}${o.rating_scope && reported(o.condition) ? `<small class="x-scale">${esc(humanize(o.rating_scope))} scope</small>` : ''}${o.condition !== 'NR' && o.rating_scale && !MISSING[o.rating_scale] ? `<small class="x-scale">${esc(o.rating_scale)}${o.rating_scale_basis === 'assumed from inspection' ? ' (report scale)' : ''}</small>` : ''}${o.condition_src ? '<br>' + chips(o.condition_src, o.obs_id, 'condition') : ''}</td>
      <td class="x-text">${cell(o, 'findings_text', o.obs_id)}</td>
      <td>${o.defect_status === 'NONE' ? '<span class="x-missing">None reported</span>' : esc(o.n_defects)}</td>`;
  } else if (tab === 'defects') {
    ({rows, head, line} = defectTable(defects, obsName, measurements, matches));
  } else {
    rows = review.filter(matches);
    head = ['Table', 'Field', 'Note'];
    line = r => `<td>${esc(r.table.replaceAll('_', ' '))}</td><td>${esc(r.field.replaceAll('_', ' '))}</td><td>${esc(r.message)}</td>`;
  }
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  page = Math.min(page, pages - 1);
  const shown = rows.slice(page * PAGE, (page + 1) * PAGE);
  $('x-count').textContent = `${rows.length.toLocaleString()} ${tab === 'defects' ? (rows.length === 1 ? 'defect' : 'defects') : (rows.length === 1 ? 'row' : 'rows')}`;
  $('x-table').innerHTML = rows.length ? `
    <div class="x-table-wrap"><table class="x-${tab}" role="table"><thead role="rowgroup"><tr role="row">${head.map(h => `<th scope="col" role="columnheader">${h}</th>`).join('')}</tr></thead>
    <tbody role="rowgroup">${shown.map(r => `<tr role="row">${line(r)}</tr>`).join('')}</tbody></table></div>`
    : '<p class="x-empty">No extracted records match this view. Missing findings or grades do not establish sound condition.</p>';
  $('x-pages').innerHTML = pages > 1 ? Array.from({length: pages}, (_, n) =>
    `<button type="button" data-page="${n}" ${n === page ? 'aria-current="page"' : ''}>${n + 1}</button>`).join('') : '';
  $('x-pages').querySelectorAll('button').forEach(b => b.onclick = () => { renderTable(tab, Number(b.dataset.page)); $('x-table').scrollIntoView({block: 'start'}); });
}

let panelTrigger, detailScroll = 0, passage = null, sourcePage = 0, evidenceRequest = 0;

const citationName = c => {
  if (c.field.startsWith('rating_scale_definition_')) return 'Scale definition';
  return ({findings_text: 'Finding', severity: 'Original rating', severity_scale: 'Original rating scale',
    rating_scale_applicability: 'Scale applicability', condition: 'Parent condition',
    value: 'Measurement', unit: 'Measurement unit'})[c.field] || humanize(c.field);
};

function sourceButton(c, label = `PDF p. ${c.page}`) {
  return `<button type="button" class="x-chip" data-citation="${esc(c.evidence_id)}">${esc(label)}</button>`;
}

function detailRow(label, content) {
  return `<div><dt>${label}</dt><dd>${content}</dd></div>`;
}

function renderDefect(d) {
  const {observations, measurements, citations} = current;
  const o = observations.find(row => row.obs_id === d.obs_id);
  const readings = measurements.filter(m => m.defect_id === d.defect_id);
  const refs = new Set([d.defect_id, ...readings.map(m => m.measurement_id)]);
  const used = new Set(list(d.assessment_evidence));
  const evidence = citations.filter(c => refs.has(c.row_id));
  const grouped = new Map();
  for (const c of evidence) {
    const key = `${c.page}:${c.paragraph}:${c.verified_quote || c.paragraph_text}`;
    const group = grouped.get(key) || {citation: c, names: new Set(), used: false};
    group.names.add(citationName(c));
    group.used ||= used.has(c.evidence_id);
    grouped.set(key, group);
  }
  const order = group => group.names.has('Finding') ? 0 : group.used ? 1 : 2;
  const passages = [...grouped.values()].sort((a, b) => order(a) - order(b));
  const definitions = list(d.rating_scale_definitions);
  const criteria = [
    ['Assessment basis', humanize(d.assessment_basis)],
    ['Rule applied', d.assessment_rule],
    ['Confidence', humanize(d.assessment_confidence)],
    ['Ambiguity', d.assessment_ambiguity],
    ['Missing information', d.assessment_missing_information],
  ].filter(([, content]) => reported(content));
  const parent = o && reported(o.condition) ? `<p class="x-parent-rating"><strong>${esc(humanize(o.rating_scope || 'component'))} condition</strong>
    ${cell(o, 'condition', o.obs_id)} ${reported(o.rating_scale) ? ' · ' + esc(o.rating_scale) : ''}</p>` : '';
  $('x-detail-title').textContent = typeLabel(d);
  $('x-detail-body').innerHTML = `<p class="x-detail-context">${esc([componentName(o), o?.parent_area, o?.section, d.location].filter(reported).join(' · '))}</p>
    <div class="x-rating-pair">
      <section><h3>Original defect rating</h3><p>${reportRating(d)}</p><small>${reported(d.severity_scale) ? esc(d.severity_scale) : 'Scale not reported'}</small>
      <div>${chips(d.severity_src, d.defect_id, 'severity')}${chips(d.severity_scale_src, d.defect_id, 'severity_scale')}</div></section>
      <section><h3>Project damage grade</h3><p>${gradeBadge(d)}</p></section>
    </div>
    ${parent}
    ${d.rating_association === 'unresolved' || d.rating_association === 'conflict' ? '<p class="x-note">Rating applicability needs source review.</p>' : ''}
    ${definitions.map((rule, n) => `<div class="x-definition"><b>${esc(rule.scale_name)} · ${esc(rule.scale_label)}</b>
      <p>${esc(rule.scale_definition)}</p>${evidence.filter(c => c.field === `rating_scale_definition_${n + 1}`).map(c => sourceButton(c)).join(' ')}</div>`).join('')}
    ${reported(d.rating_scale_applicability) ? `<p><b>Scale applicability</b> ${cell(d, 'rating_scale_applicability', d.defect_id)}</p>` : ''}
    <section class="x-detail-section"><h3>Assessment reasoning</h3>
      <p>${esc(d.assessment_rationale || ({
        failed: 'Grading did not finish for this defect. The original extraction and source evidence are available below.',
        pending: 'No current assessment is available for this defect.',
        legacy_ungraded: 'This extraction predates Method A defect grading.',
      })[d.grading_status] || 'Rationale unavailable.')}</p>
      ${criteria.length ? `<dl class="x-detail-facts">${criteria.map(([label, content]) => detailRow(label, esc(content))).join('')}</dl>` : ''}
      ${list(d.assessment_conversions).map(c => `<p class="x-note">Conversion · ${esc(c.raw_value)} ${esc(c.raw_unit)} ÷ ${esc(c.divide_by)}
        = ${esc(c.value_number ?? [c.lower, c.upper].filter(v => v != null).join('–'))} ${esc(c.target_unit)}</p>`).join('')}
    </section>
    <section class="x-detail-section"><h3>Measurements &amp; source context</h3>
      <ul class="x-reading-list">${readings.map(m => `<li>${esc(m.property)} · ${measurementValue(m)}
        ${chips(m.src, m.measurement_id, 'value')}${m.unit_src && m.unit_src !== m.src ? chips(m.unit_src, m.measurement_id, 'unit') : ''}
        ${m.source_association === 'unresolved' || m.source_association === 'conflict' ? '<small>Association needs review</small>' : ''}</li>`).join('') ||
        '<li class="x-missing">No linked measurements</li>'}</ul>
      <dl class="x-detail-facts">${[
        ['Status', reported(d.temporal_status) ? esc(humanize(d.temporal_status)) : 'Not reported'],
        ['Material', reported(d.material) ? cell(d, 'material', d.defect_id) : o ? cell(o, 'material', o.obs_id) : 'Not reported'],
        ['Material grade', reported(d.material_grade) ? cell(d, 'material_grade', d.defect_id) : o ? cell(o, 'material_grade', o.obs_id) : 'Not reported'],
        ['Structural role', reported(d.structural_role) ? cell(d, 'structural_role', d.defect_id) : o ? cell(o, 'structural_role', o.obs_id) : 'Not reported'],
      ].map(([label, content]) => detailRow(label, content)).join('')}</dl>
    </section>
    <section class="x-detail-section"><h3>Source passages <small>${passages.length}</small></h3>
      ${passages.map(({citation: c, names, used}) => `<article class="x-passage">
        <div class="x-passage-head"><strong>${esc([...names].join(' · '))}</strong>${sourceButton(c, `PDF p. ${c.page} ¶${c.paragraph}`)}</div>
        ${used ? '<small class="x-scale">Used in assessment</small>' : ''}
        ${c.verified_quote ? `<blockquote>${esc(c.verified_quote)}</blockquote>` : `<p class="x-note">Saved paragraph · quotation not verified</p>
          <blockquote>${esc(c.paragraph_text)}</blockquote>`}
      </article>`).join('') || '<p>No linked PDF passages.</p>'}
    </section>`;
}

function showPanel(trigger) {
  const panel = $('x-panel');
  if (!panel.open) {
    panelTrigger = trigger || document.activeElement;
    panel.showModal();
  }
}

function resetSource() {
  evidenceRequest++;
  passage = null;
  $('x-source').hidden = true;
  $('x-panel').classList.remove('x-with-source');
}

function showDefect(id, trigger) {
  const defect = current.defects.find(d => d.defect_id === id);
  if (!defect) {
    $('x-route-error').textContent = 'This defect link does not match a defect in the selected inspection.';
    $('x-route-error').hidden = false;
    return;
  }
  resetSource();
  renderDefect(defect);
  $('x-detail').hidden = false;
  $('x-panel').setAttribute('aria-labelledby', 'x-detail-title');
  showPanel(trigger);
  $('x-detail-title').focus({preventScroll: true});
}

async function renderSource(number, focus = false) {
  const serial = ++evidenceRequest;
  const i = current.inspection;
  sourcePage = number;
  const cite = Number(passage?.page) === number ? passage : null;
  const args = cite?.evidence_id ? '?' + new URLSearchParams({citation: cite.evidence_id}) : '';
  $('x-source-title').textContent = `PDF page ${number} of ${i.pdf_pages}`;
  $('x-source-file').textContent = i.source_file;
  $('x-previous-page').disabled = number <= 1 || !i.pdf_available;
  $('x-next-page').disabled = number >= Number(i.pdf_pages) || !i.pdf_available;
  $('x-source-back').hidden = $('x-detail').hidden;
  $('x-source').hidden = false;
  $('x-panel').classList.add('x-with-source');
  $('x-panel').setAttribute('aria-labelledby', 'x-source-title');
  const quote = cite && (cite.verified_quote || cite.paragraph_text) ? `<div class="x-source-quote">
    <strong>${esc(citationName(cite))} · ¶${esc(cite.paragraph)}</strong>
    <blockquote>${esc(cite.verified_quote || cite.paragraph_text)}</blockquote></div>` : '';
  const body = $('x-source-body');
  body.innerHTML = quote + `<p class="x-precision" role="status">${i.pdf_available ? 'Locating source…' : 'Source PDF unavailable on this server.'}</p>`;
  if (focus) {
    $('x-panel').scrollTop = 0;
    $('x-source-title').focus({preventScroll: true});
  }
  if (!i.pdf_available) return;
  const address = `${encodeURIComponent(i.source_sha256)}/${number}${args}`;
  try {
    const info = await getJson('/api/extraction/evidence/' + address);
    if (serial !== evidenceRequest) return;
    const precision = {
      quotation: 'Quotation highlight', paragraph: 'Paragraph highlight · saved source excerpt',
      unresolved_quotation: 'Paragraph highlight · exact quotation location unresolved',
      unresolved: 'Citation unresolved on this page', image_only: 'Image-only evidence · no native-text highlight',
      page: 'No selected passage on this page',
    }[cite && !cite.evidence_id ? 'unresolved' : info.precision];
    body.querySelector('.x-precision').textContent = precision;
    const image = new Image();
    image.alt = `PDF page ${number}. ${precision}.`;
    image.onerror = () => { if (serial === evidenceRequest) body.querySelector('.x-precision').textContent = 'This PDF page could not be rendered.'; };
    image.src = '/api/extraction/page/' + address;
    body.append(image);
  } catch (error) {
    if (serial === evidenceRequest) body.querySelector('.x-precision').textContent = error.message;
  }
}

function showSource(cite, trigger) {
  if (!cite) return;
  if ($('x-source').hidden) detailScroll = $('x-panel').scrollTop;
  passage = cite;
  showPanel(trigger);
  renderSource(Number(cite.page), true);
}

function closePanel({restore = true} = {}) {
  if (!$('x-panel').open) return;
  $('x-panel').close();
  $('x-detail').hidden = true;
  resetSource();
  if (restore && panelTrigger?.isConnected) panelTrigger.focus({preventScroll: true});
  panelTrigger = null;
}

function closePanelLink() {
  const query = params();
  query.delete('defect');
  history.replaceState(null, '', query.size ? '?' + query : '/extraction');
  closePanel();
}

async function route({trigger, source, focus = false} = {}) {
  const request = ++routeRequest;
  const id = params().get('inspection');
  if (!id || current?.inspection.inspection_id !== id) {
    renderSide();
    closePanel({restore: false});
    if (!id) {
      current = null;
      renderBrowse();
      return;
    }
    $('x-main').innerHTML = '<p class="x-empty">Loading the inspection…</p>';
    try {
      const detail = await getJson('/api/extraction/inspections/' + encodeURIComponent(id));
      if (request !== routeRequest) return;
      current = detail;
      currentTab = null;
    } catch (error) {
      if (request === routeRequest) $('x-main').innerHTML = `<p class="x-empty" role="alert">${esc(error.message)}</p>`;
      return;
    }
  }
  const requestedTab = params().get('tab');
  const tab = TABS.some(([key]) => key === requestedTab) ? requestedTab : 'defects';
  if (currentTab !== tab) {
    renderInspection(tab);
    currentTab = tab;
    if (tab === 'defects') requestAnimationFrame(() => requestAnimationFrame(recordReadyTime));
    if (focus) $('x-inspection-title').focus({preventScroll: true});
  }
  const idOfDefect = params().get('defect');
  $('x-route-error').hidden = true;
  if (idOfDefect) {
    showDefect(idOfDefect, trigger);
    if (source) showSource(current.citations.find(c => c.evidence_id === source), trigger);
  } else closePanel();
}

document.addEventListener('click', event => {
  const chip = event.target.closest('.x-chip[data-page], [data-citation]');
  if (chip) {
    const {row, field, page, para, citation} = chip.dataset;
    const cite = citation ? current.citations.find(c => c.evidence_id === citation) :
      current.citations.find(c => c.row_id === row && c.field === field &&
        String(c.page) === page && String(c.paragraph) === para);
    showSource(cite || (page ? {page, paragraph: para, field} : null), chip);
    return;
  }
  const link = event.target.closest('a[data-route]');
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  history.pushState(null, '', link.href);
  if (!params().has('defect')) window.scrollTo(0, 0);
  route({trigger: link, source: link.dataset.source, focus: true});
});
$('x-panel-close').onclick = closePanelLink;
$('x-panel').addEventListener('cancel', event => { event.preventDefault(); closePanelLink(); });
$('x-source-back').onclick = () => {
  resetSource();
  $('x-panel').scrollTop = detailScroll;
  $('x-panel').setAttribute('aria-labelledby', 'x-detail-title');
  $('x-detail-title').focus({preventScroll: true});
};
$('x-previous-page').onclick = () => renderSource(sourcePage - 1);
$('x-next-page').onclick = () => renderSource(sourcePage + 1);
window.addEventListener('popstate', () => route());
$('x-search').oninput = renderSide;

(async () => {
  try {
    data = await getJson('/api/extraction?scope=method_a_one_shot');
    $('x-totals').textContent = `${reportEntries().length} latest report versions · ${data.inspections.reduce((n, i) => n + Number(i.n_defects || 0), 0)} defect records`;
    const warnings = data.release?.warnings || [];
    $('x-release-warnings').innerHTML = publicationNotice(warnings, 'Known limitations in this release');
    $('x-release-warnings').hidden = !warnings.length;
    $('x-loading').hidden = true;
    $('x-content').hidden = false;
    route();
  } catch (error) {
    $('x-loading').hidden = true;
    $('x-error').hidden = false;
    $('x-error').textContent = 'The extraction could not load. ' + error.message;
  }
})();
