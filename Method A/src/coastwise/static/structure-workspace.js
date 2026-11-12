/* The reviewed register and the saved source reports have different row scopes. */
const element = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const display = value => escape(value || '—');
const human = value => value ? value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()) : '';
const disposition = row => row.disposition === 'Inspection tables' ? 'In the register' : 'Sources and review only';
const status = value => {
  const label = human(value);
  const attention = /partial|unclear|unmatched|review|unassigned/i.test(label);
  return label ? '<span class="status' + (attention ? ' attention' : '') + '">' + escape(label) + '</span>' : '—';
};
const dateText = row => {
  if (!row.inspection_start) return row.inspection_date_raw || 'Not verified';
  return row.inspection_start + (row.inspection_end && row.inspection_end !== row.inspection_start
    ? ' to ' + row.inspection_end : '');
};
const scoreText = row => row.condition_score ? row.condition_score + ' ' + row.condition_label : 'Grade unavailable';
const latestDate = row => row.latest_inspection_start || row.latest_inspection_end
  ? dateText({inspection_start: row.latest_inspection_start || row.latest_inspection_end, inspection_end: row.latest_inspection_end})
  : row.latest_report_year ? row.latest_report_year + ' report · visit date unverified' : 'Visit date unverified';
const earlierScore = row => row.previous_condition_score
  ? row.previous_report_year + ' report · ' + row.previous_condition_score + ' ' + row.previous_condition_label : '';
function conditionCell(row) {
  const score = row.condition_score ? '<strong class="score-value">' + escape(scoreText(row)) + '</strong>' :
    '<span class="muted">Grade unavailable</span>';
  return score + (row.grade_scope_limited ? '<small>Scoped grade</small>' : '') +
    (row.grade_status !== 'Graded' ? '<small>' + status('Needs review') + '</small>' : '') +
    (row.previous_condition_score ? '<small>Earlier grade · ' + escape(earlierScore(row)) + '</small>' : '');
}
const damageCell = row => '<strong class="score-value">' + display(row.damage_grade_label) + '</strong>' +
  (row.grade_status === 'Needs review' ? '<small>' + status(row.grade_status) + '</small>' : '');
const PAGE_SIZE = 25;
const recordCount = count => count + (count === 1 ? ' record' : ' records');
const CONDITION_DETAILS = [['Condition grade', scoreText], ['Grade scope', 'grade_scope'],
  ['Grade status', 'grade_status'], ['Why it needs review', 'grade_review']];
const LEVELS = [
  {
    key: 'structures', label: 'Structures', primary: 'Structure', id: 'structure_id', param: 'structure', name: r => r.name,
    description: 'See the latest inspection grade, then choose a structure to view its history. Condition grades run from 1 Good to 4 Serious / critical.',
    columns: [['Latest condition grade', conditionCell],
      ['Latest inspection', r => display(latestDate(r)) + '<small>' + escape(human(r.latest_coverage)) + ' inventory coverage</small>'],
      ['Location', 'location']],
    filters: [['Type', 'structure_type'], ['Grade status', 'grade_status']],
    details: [...CONDITION_DETAILS, ['Latest inspection', latestDate], ['Latest inventory coverage', r => human(r.latest_coverage)],
      ['Earlier grade', earlierScore], ['Location', 'location'], ['Type', 'structure_type'], ['Primary material', 'primary_material'],
      ['Exposure environment', 'exposure_environment'], ['Source references', 'source_pages']]
  },
  {
    key: 'inspections', label: 'Inspections', primary: 'Inspection', id: 'inspection_id', param: 'inspection',
    name: r => r.report_year ? r.report_year + ' inspection' : 'Inspection year unverified',
    description: 'Each row covers one structure in one source document. The visit date and report year are shown separately.',
    columns: [['Condition grade', conditionCell], ['Field visit', r => display(dateText(r))], ['Reported condition', 'overall_condition'],
      ['Coverage', r => status(r.inventory_status)]],
    filters: [['Coverage', 'inventory_status'], ['Grade status', 'grade_status']],
    details: [...CONDITION_DETAILS, ['Grade basis', 'grade_basis'], ['Model confidence', r => human(r.grade_confidence)],
      ['Grade rationale', 'grade_rationale'], ['Report year', 'report_year'], ['Field visit', dateText], ['Visit date note', 'date_note'],
      ['Inspection firm', 'inspecting_firm'], ['Inspection method', 'inspection_type'], ['Reported condition', 'overall_condition'],
      ['Report rating scale', 'rating_scale'], ['Repairs since last inspection', 'repairs_since_last'],
      ['Reported urgency', 'urgency'], ['Coverage', r => human(r.inventory_status)],
      ['Scope', 'scope_note'], ['Unverified fields', 'fields_unverified']]
  },
  {
    key: 'component_observations', label: 'Component observations', primary: 'Component', id: 'obs_id', param: 'component',
    name: r => r.component_name,
    description: 'Each row describes one component in this inspection. Unmatched identities are specific to the source report.',
    columns: [['Section', 'section'], ['Coverage', r => status(r.coverage)], ['Component identity', r => status(r.match_status)],
      ['Findings', 'extracted_defects']],
    filters: [['Section', 'section'], ['Identity', 'match_status']],
    details: [['Section', 'section'], ['Reported condition', 'condition'], ['Model condition score', 'model_condition_score'],
      ['Coverage', r => human(r.coverage)], ['Coverage note', 'coverage_note'], ['Identity', 'match_status'],
      ['Identity evidence or limitation', 'match_reason'], ['Recorded findings', 'findings_text'],
      ['Finding status', r => human(r.finding_status)], ['Current findings', 'current_defects']]
  },
  {
    key: 'defects', label: 'Defects', primary: 'Finding', id: 'defect_id', name: r => r.defect_category,
    description: 'Damage grades run from 1 Minor to 4 Severe. Open a finding to see its measurements and evidence.',
    columns: [['Damage grade', damageCell], ['Location', 'location'], ['Reported severity', 'severity'],
      ['When observed', r => status(r.temporal_context)]],
    filters: [['When observed', 'temporal_context'], ['Damage grade', 'damage_grade_label'], ['Grade status', 'grade_status']],
    details: [['Damage grade', 'damage_grade_label'], ['Grade status', 'grade_status'], ['Why it needs review', 'grade_review'],
      ['Finding', 'findings_text'], ['Location', 'location'], ['When observed', r => human(r.temporal_context)],
      ['Reported severity', 'severity'], ['Measurements', 'measurements']]
  }
];
const SOURCE_VIEWS = [
  {
    key: 'source_manifest', label: 'Reviewed sources', primary: 'Source report', id: 'document_id', name: r => r.source_filename,
    description: 'All PDFs reviewed for this cohort, including reference material and documents without a verified inspection scope.',
    columns: [['Report year', 'report_year'], ['Document type', r => status(r.document_type)], ['Inspection scopes', 'inspection_rows'],
      ['Where it appears', row => escape(disposition(row))]],
    filters: [['Document type', 'document_type'], ['Disposition', 'disposition']],
    details: [['Document type', r => human(r.document_type)], ['Report year', 'report_year'], ['PDF pages', 'source_pages'],
      ['Inspection scopes', 'inspection_rows'], ['Where it appears', disposition], ['Scope note', 'scope_note'],
      ['Component records to review', 'component_records_in_review'], ['Defects to review', 'defect_records_in_review']]
  },
  {
    key: 'review_components', label: 'Components to review', primary: 'Component', id: r => r.document_id + '/' + r.source_component_id,
    name: r => r.component_name,
    description: 'Saved component records with a scope or identity that needs review.',
    columns: [['Source report', 'source_filename'], ['Reason for review', 'review_reason']],
    filters: [],
    details: [['Section in source', 'source_section_id'], ['Structure in source', 'source_structure'],
      ['Identity', 'match_status'], ['Reason for review', 'review_reason']]
  },
  {
    key: 'review_defects', label: 'Defects to review', primary: 'Finding', id: 'defect_id', name: r => r.defect_category,
    description: 'Saved findings that could not be linked to a verified inspection and component.',
    columns: [['Damage grade', damageCell], ['Source report', 'source_filename'], ['Reason for review', 'review_reason']],
    filters: [['Damage grade', 'damage_grade_label']],
    details: LEVELS[3].details
  },
  {
    key: 'other_sources', label: 'Other saved reports', primary: 'Source report', id: 'id', name: r => r.filename,
    description: 'Saved reports outside the reviewed cohort. Their original results are available in the report library.',
    columns: [['PDF pages', 'pages'], ['Run status', r => status(r.status)]],
    filters: [['Run status', 'status']],
    details: [['Run status', r => human(r.status)], ['PDF pages', 'pages']]
  }
];
const INSPECTION_DEFECTS = {
  ...LEVELS[3], label: 'All defects in this inspection',
  description: 'Browse linked findings across every component. Filter by section, component, damage grade, or when observed.',
  columns: [LEVELS[3].columns[0], ['Component', 'component_name'], ['Section', 'section'], ...LEVELS[3].columns.slice(1)],
  filters: [['Section', 'section'], ['Component', 'component_name'], ...LEVELS[3].filters]
};

let data, indexes, context;
let returnFocus;
const rowId = (table, row) => typeof table.id === 'function' ? table.id(row) : row[table.id];
const valueAt = (row, field) => typeof field === 'function' ? field(row) : row[field];
const groupBy = (rows, key) => {
  const groups = new Map();
  for (const row of rows) {
    const parent = row[key];
    if (!groups.has(parent)) groups.set(parent, []);
    groups.get(parent).push(row);
  }
  return groups;
};

function setData(result) {
  data = result;
  const components = new Map(data.component_observations.map(row => [row.obs_id, row]));
  const linkedDefects = data.defects.map(row => {
    const {component_name, section, inspection_id} = components.get(row.obs_id);
    return {...row, component_name, section, inspection_id};
  });
  indexes = {
    structures: new Map(data.structures.map(row => [row.structure_id, row])),
    sources: new Map(data.source_manifest.map(row => [row.document_id, row])),
    inspections: groupBy(data.inspections, 'structure_id'),
    sourceInspections: groupBy(data.inspections, 'document_id'),
    components: groupBy(data.component_observations, 'inspection_id'),
    defects: groupBy(linkedDefects, 'obs_id'),
    inspectionDefects: groupBy(linkedDefects, 'inspection_id'),
    reviewByInspection: groupBy(data.review_defects, 'inspection_id'),
    reviewBySource: groupBy(data.review_defects, 'document_id'),
    identityEvidence: groupBy(data.component_match_evidence, 'component_id')
  };
}

function readContext(params) {
  if (params.get('view') === 'sources') {
    const table = SOURCE_VIEWS.find(view => view.key === params.get('table')) || SOURCE_VIEWS[0];
    let rows = data[table.key];
    if (table.key === 'review_defects' && params.has('inspection')) {
      rows = indexes.reviewByInspection.get(params.get('inspection')) || [];
    } else if (table.key === 'review_defects' && params.has('document')) {
      rows = (indexes.reviewBySource.get(params.get('document')) || []).filter(row => !row.inspection_id);
    }
    return {sources: true, table, rows, params};
  }
  const structure = indexes.structures.get(params.get('structure'));
  const inspections = indexes.inspections.get(structure?.structure_id) || [];
  const inspection = inspections.find(row => row.inspection_id === params.get('inspection'));
  const components = indexes.components.get(inspection?.inspection_id) || [];
  const component = components.find(row => row.obs_id === params.get('component'));
  const target = LEVELS.findIndex(table => table.key === params.get('level'));
  const max = inspection ? 3 : structure ? 1 : 0;
  const level = target < 0 ? (inspection && !component ? 2 : max) : Math.min(target, max);
  const inspectionDefects = indexes.inspectionDefects.get(inspection?.inspection_id) || [];
  const defects = component ? indexes.defects.get(component.obs_id) || [] : inspectionDefects;
  const rows = [data.structures, inspections, components, defects];
  const table = level === 3 && !component ? INSPECTION_DEFECTS : LEVELS[level];
  return {level, table, rows: rows[level], scopes: [structure, inspection, component],
    counts: rows.map(items => items.length), max, params, inspectionDefects};
}

function scopeUrl(level, scopes = context.scopes) {
  const params = new URLSearchParams();
  if (level) params.set('level', LEVELS[level].key);
  scopes.slice(0, level).forEach((row, index) => {
    if (row) params.set(LEVELS[index].param, rowId(LEVELS[index], row));
  });
  return params.size ? '/?' + params.toString() : '/';
}

function contextUrl(changes) {
  const params = new URLSearchParams(context.params);
  for (const [key, value] of Object.entries(changes)) {
    if (value) params.set(key, value);
    else params.delete(key);
  }
  return params.size ? '/?' + params.toString() : '/';
}

function sourceUrl(table) {
  return '/?view=sources' + (table === SOURCE_VIEWS[0].key ? '' : '&table=' + encodeURIComponent(table));
}

function runUrl(id, tab) {
  return '/?run=' + encodeURIComponent(id) + (tab ? '&tab=' + encodeURIComponent(tab) : '');
}

function inspectionLinks(structure, inspection, count) {
  const inspect = scopeUrl(1, [structure]) + '&record=' + encodeURIComponent(inspection.inspection_id);
  const defects = scopeUrl(3, [structure, inspection]);
  const review = sourceUrl('review_defects');
  const inScope = indexes.reviewByInspection.get(inspection.inspection_id)?.length || 0;
  const unassigned = (indexes.reviewBySource.get(inspection.document_id) || []).filter(row => !row.inspection_id).length;
  const links = [[defects, 'All defects in this inspection (' + count + ')']];
  if (context.table !== LEVELS[1]) links.unshift([inspect, 'Inspection assessment']);
  if (inScope) links.push([review + '&inspection=' + encodeURIComponent(inspection.inspection_id), 'Inspection findings to review (' + inScope + ')']);
  if (unassigned) links.push([review + '&document=' + encodeURIComponent(inspection.document_id), 'Unassigned findings in source PDF (' + unassigned + ')']);
  return links.map(([url, label]) => '<a data-route href="' + escape(url) + '">' + escape(label) + '</a>').join('');
}

function renderPath() {
  element('scope-path').hidden = !!context.sources;
  element('source-views').hidden = !context.sources;
  element('breadcrumbs').hidden = !!context.sources || context.level === 0;
  const inspection = context.level >= 2 ? context.scopes[1] : null;
  const actions = element('inspection-actions');
  actions.hidden = !inspection;
  actions.innerHTML = inspection ? inspectionLinks(context.scopes[0], inspection, context.inspectionDefects.length) : '';
  document.querySelectorAll('[data-view-link]').forEach(link => {
    if ((link.dataset.viewLink === 'sources') === Boolean(context.sources)) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (context.sources) {
    element('source-views').innerHTML = SOURCE_VIEWS.map(table => '<a data-route href="' + escape(sourceUrl(table.key)) +
      '"' + (table === context.table ? ' aria-current="page"' : '') + '>' + escape(table.label) +
      ' <span>(' + data[table.key].length + ')</span></a>').join('');
    return;
  }
  const steps = LEVELS.map((table, level) => {
    const available = level <= context.max;
    const selection = context.scopes[level];
    const selected = selection && level < context.level;
    const text = '<span class="scope-number">0' + (level + 1) + '</span><strong>' + table.label + '</strong><small>' +
      (selected ? escape(table.name(selection)) : available ? recordCount(context.counts[level]) :
        'Choose ' + ['a structure', 'an inspection', 'a component'][level - 1]) + '</small>';
    return available ? '<a class="scope-step" data-route href="' + escape(scopeUrl(level)) + '"' +
      (level === context.level ? ' aria-current="step"' : '') + '>' + text + '</a>' :
      '<span class="scope-step" aria-disabled="true">' + text + '</span>';
  });
  element('scope-path').innerHTML = steps.join('');
  const crumbs = context.scopes.slice(0, context.level).flatMap((row, index) => row ?
    ['<a data-route href="' + escape(scopeUrl(index)) + '">' + escape(LEVELS[index].name(row)) + '</a>'] : []);
  element('breadcrumbs').innerHTML = [...crumbs, '<span aria-current="page">' + context.table.label + '</span>']
    .join('<span aria-hidden="true">/</span>');
}

function renderFilters() {
  element('table-search').value = context.params.get('q') || '';
  element('filters').innerHTML = context.table.filters.map(([title, field]) => {
    const values = [...new Set(context.rows.map(row => row[field]).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    const selected = context.params.get(field);
    return '<label>' + title + '<select data-filter="' + field + '"><option value="">All</option>' +
      values.map(value => '<option value="' + escape(value) + '"' + (value === selected ? ' selected' : '') +
        '>' + escape(human(value)) + '</option>').join('') + '</select></label>';
  }).join('');
}

function sortedRows(rows) {
  if (context.table === LEVELS[1]) {
    return [...rows].sort((a, b) => (a.inspection_order_date || '9999').localeCompare(b.inspection_order_date || '9999') ||
      a.inspection_id.localeCompare(b.inspection_id));
  }
  if (context.table === LEVELS[0]) {
    return [...rows].sort((a, b) => Number(Boolean(b.condition_score)) - Number(Boolean(a.condition_score)) ||
      a.name.localeCompare(b.name, undefined, {numeric: true}));
  }
  return rows;
}

function primaryHref(row) {
  if (context.sources || context.level === 3) return contextUrl({record: rowId(context.table, row)});
  const scopes = [...context.scopes.slice(0, context.level), row];
  return scopeUrl(context.level + 1, scopes);
}

function renderRows() {
  const {table, params, rows} = context;
  const query = (params.get('q') || '').trim().toLowerCase();
  const filters = table.filters.map(([, field]) => [field, params.get(field)]).filter(([, value]) => value);
  const matches = sortedRows(rows.filter(row => filters.every(([field, value]) => row[field] === value) &&
    (!query || Object.values(row).some(value => String(value ?? '').toLowerCase().includes(query)))));
  const totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
  const page = Math.min(totalPages, Math.max(1, Number.parseInt(params.get('page'), 10) || 1));
  const visible = matches.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const total = table === INSPECTION_DEFECTS ? rows.length + (rows.length === 1 ? ' linked finding' : ' linked findings') : recordCount(rows.length);
  element('row-count').textContent = matches.length === rows.length ? total : matches.length + ' of ' + total;
  if (!visible.length) {
    element('table-result').innerHTML = '<div class="message"><h3>' + (rows.length ? 'No records match these filters.' :
      'No linked records at this level.') + '</h3><p>' + (rows.length ? 'Clear your search or change a filter.' :
      context.sources ? 'There are no saved records in this view.' :
        'This view reflects the reviewed records and available inspection coverage.') + '</p>' +
      (rows.length ? '<button type="button" data-clear-filters>Clear search and filters</button>' : '') + '</div>';
    element('pagination').innerHTML = '';
    return;
  }
  const detailColumn = !context.sources && context.level < 3;
  const headings = [table.primary, ...table.columns.map(([title]) => title)];
  element('table-result').innerHTML = '<div class="table-scroll"><table aria-labelledby="table-title"><thead><tr>' +
    headings.map(title => '<th scope="col">' + title + '</th>').join('') +
    (detailColumn ? '<th scope="col">Details</th>' : '') + '</tr></thead><tbody>' + visible.map(row => {
      const id = rowId(table, row);
      const name = table.name(row) || 'Unnamed record';
      const subtitle = table === LEVELS[1] ? '<small>' + escape(row.source_filename) + '</small>' +
        '<small><a data-route href="' + escape(scopeUrl(3, [context.scopes[0], row])) + '">All defects (' +
        (indexes.inspectionDefects.get(row.inspection_id)?.length || 0) + ')</a></small>' : '';
      return '<tr' + (params.get('record') === id ? ' class="selected"' : '') + '><td><a data-route href="' +
        escape(primaryHref(row)) + '">' + escape(name) + '</a>' + subtitle + '</td>' +
        table.columns.map(([, field]) => '<td>' + (typeof field === 'function'
          ? field(row) : display(row[field])) + '</td>').join('') +
        (detailColumn ? '<td><a data-route href="' + escape(contextUrl({record: id})) +
          '" aria-label="Details for ' + escape(name) + '">Details</a></td>' : '') + '</tr>';
    }).join('') + '</tbody></table></div>';
  element('pagination').innerHTML = totalPages > 1 ?
    '<button type="button" data-page="' + (page - 1) + '"' + (page === 1 ? ' disabled' : '') + '>Previous</button>' +
    '<span>Page ' + page + ' of ' + totalPages + '</span><button type="button" data-page="' + (page + 1) + '"' +
    (page === totalPages ? ' disabled' : '') + '>Next</button>' : '';
}

function evidencePages(pages) {
  return (pages || '').split(',').map(page => page.trim()).filter(page => /^\d+$/.test(page) && Number(page) > 0);
}

function sourceActions(source, inSourceRecord) {
  if (!source) return '';
  if (!inSourceRecord) {
    const link = sourceUrl('source_manifest') + '&record=' + encodeURIComponent(source.document_id);
    return '<div class="detail-actions"><a data-route href="' + escape(link) + '">Open source record</a></div>';
  }
  const links = [];
  if (source.pdf_run_id) {
    links.push('<a href="/api/runs/' + encodeURIComponent(source.pdf_run_id) + '/download/source.pdf">Download source PDF</a>');
  }
  if (!source.pdf_run_id && /^https?:\/\//i.test(source.source_url)) {
    links.push('<a href="' + escape(source.source_url) + '" target="_blank" rel="noopener">Open source document</a>');
  }
  if (source.reviewed_run_id) {
    links.push('<a href="' + escape(runUrl(source.reviewed_run_id, 'defects')) + '">Saved defect run</a>');
  }
  if (source.report_run_id) {
    links.push('<a href="' + escape(runUrl(source.report_run_id, 'new_schema')) + '">Report fields</a>');
    if (source.comparison_available) {
      links.push('<a href="' + escape(runUrl(source.report_run_id, 'judge')) + '">Human comparison</a>');
    }
    links.push('<a href="' + escape(runUrl(source.report_run_id, 'usage')) + '">Run details</a>');
  } else if (source.reviewed_run_id) {
    links.push('<a href="' + escape(runUrl(source.reviewed_run_id, 'usage')) + '">Run details</a>');
  }
  return '<div class="detail-actions">' + links.join('') + '</div>';
}

const definitionList = pairs => '<dl>' + pairs.filter(([, value]) => value != null && value !== '')
  .map(([label, value]) => '<dt>' + escape(label) + '</dt><dd>' + display(value) + '</dd>').join('') + '</dl>';

function quoteList(evidence) {
  return (evidence || []).map(item => {
    const page = Number.isInteger(item.page) && item.page > 0 ? item.page : null;
    return '<blockquote>' + escape(item.quote) + '</blockquote><div class="page-links">' +
      (page ? '<button type="button" data-source-page="' + page + '" aria-pressed="false">PDF page ' + page + '</button>' : '') +
      (item.verified === false ? '<span class="status attention">Quote needs checking</span>' : '') + '</div>';
  }).join('');
}

function savedNotes(fact, evidence) {
  if (!fact?.reason && !evidence.length) return '';
  return '<details><summary>Saved reason and evidence</summary>' +
    (fact?.reason ? '<p>' + escape(fact.reason) + '</p>' : '') + quoteList(evidence) + '</details>';
}

function factValue(fact) {
  return fact?.value ?? human(fact?.status || 'not_reported');
}

function defectDetails(saved) {
  const assessment = saved.assessment;
  const alternatives = {minor: '1 Minor', moderate: '2 Moderate', advanced: '3 Advanced', severe: '4 Severe', none: 'No damage'};
  const byId = new Map(saved.evidence.map(item => [item.id, item]));
  const quotesFor = fact => (fact?.evidence_ids || []).map(id => byId.get(id)).filter(Boolean);
  const factNames = [['Finding', 'finding'], ['Location', 'location'], ['Reported severity', 'severity'],
    ['Reported extent', 'extent'], ['Reported urgency', 'urgency'], ['Observation date', 'observed_date']];
  const facts = factNames.map(([label, field]) => {
    const fact = saved.facts[field];
    return '<dt>' + label + '</dt><dd>' + display(factValue(fact)) + savedNotes(fact, quotesFor(fact)) + '</dd>';
  }).join('');
  const measures = saved.measurements.map(measurement => {
    const label = factValue(measurement.property);
    const fields = [['Value', 'raw_value'], ['Unit', 'unit'], ['Datum', 'datum']].map(([title, key]) =>
      '<dt>' + title + '</dt><dd>' + display(factValue(measurement[key])) +
      savedNotes(measurement[key], quotesFor(measurement[key])) + '</dd>').join('');
    return '<article><h4>' + display(label) + '</h4>' + savedNotes(measurement.property, quotesFor(measurement.property)) +
      '<dl>' + fields + '</dl></article>';
  }).join('');
  const actions = saved.recommendations.map(action => '<article><h4>' + escape(action.scope) + ' recommendation</h4><p>' +
    display(factValue(action.description)) + '</p>' + savedNotes(action.description, quotesFor(action.description)) +
    (action.location.value ? '<p>' + escape(action.location.value) + '</p>' + savedNotes(action.location, quotesFor(action.location)) : '') +
    '</article>').join('');
  return '<h3>Why this damage grade</h3>' + definitionList([
    ['Model rationale', assessment.rationale], ['Rubric criterion', assessment.rubric_criterion],
    ['Model extent', assessment.extent === 'NR' ? 'Not assessed' : human(assessment.extent)],
    ['Model confidence', human(assessment.confidence)], ['Alternative grade', alternatives[assessment.alternative_grade]]
  ]) + '<h3>Source details</h3><dl>' + facts + '<dt>When observed</dt><dd>' + escape(human(saved.temporal_context)) +
    '</dd>' + (saved.date_precision !== 'unknown' ? '<dt>Date precision</dt><dd>' + display(human(saved.date_precision)) + '</dd>' : '') +
    '</dl><h3>Measurements</h3>' + (measures || '<p>No measurements were saved for this finding.</p>') +
    (actions ? '<h3>Source-stated recommendations</h3><p class="detail-context">These actions apply to the named component or section.</p>' + actions : '');
}

function reportDetails(saved, source) {
  const title = saved.original ? 'Original report assessment' : 'Saved report assessment';
  const scopeCount = indexes.sourceInspections.get(source?.document_id)?.length || 0;
  const priority = ['Defect Description', 'Recommended action', 'Defect Severity/ Action urgency'];
  const labels = {'Defect Description': 'Defect narrative', 'Defect Severity/ Action urgency': 'Reported severity and action urgency'};
  const field = ([name, item]) => '<article><h4>' + escape(labels[name] || name) + '</h4><p>' + display(factValue(item)) +
    '</p>' + savedNotes(item, item.evidence || []) + '</article>';
  const keyFields = priority.filter(name => saved.fields[name]).map(name => field([name, saved.fields[name]])).join('');
  const otherFields = Object.entries(saved.fields).filter(([name]) => !priority.includes(name));
  return '<details class="report-assessment"><summary>' + title + ' · ' + Object.keys(saved.fields).length + ' fields</summary>' +
    '<p class="detail-context">This assessment describes the source PDF as a whole.' +
    (scopeCount > 1 ? ' It covers ' + scopeCount + ' separate inspection scopes.' : '') + '</p>' +
    definitionList([['Report-wide model condition grade', saved.assessment.score],
      ['Report-wide rationale', saved.assessment.rationale], ['Model confidence', human(saved.assessment.confidence)]]) +
    (saved.evidence.length ? '<details><summary>Evidence for the report-wide grade</summary>' +
      quoteList(saved.evidence) + '</details>' : '') +
    keyFields + (otherFields.length ? '<details><summary>More report fields (' + otherFields.length + ')</summary>' +
      otherFields.map(field).join('') + '</details>' : '') +
    '<p><a href="' + escape(runUrl(saved.run_id, 'new_schema')) + '">Open saved report run</a></p></details>';
}

async function loadDetail(target, path, renderSaved, unavailable) {
  try {
    const response = await fetch(path);
    if (!response.ok) throw new Error('Saved detail unavailable');
    const saved = await response.json();
    if (target.isConnected) renderSaved(saved);
  } catch {
    if (target.isConnected) target.textContent = unavailable;
  }
}

function renderDetail() {
  const {table, params} = context;
  const detail = element('record-detail');
  const row = context.rows.find(item => rowId(table, item) === params.get('record'));
  detail.hidden = !row;
  element('work-area').classList.toggle('with-detail', !!row);
  if (!row) {
    detail.innerHTML = '';
    return;
  }

  const inspection = table === LEVELS[1] ? row : context.scopes?.[1];
  const source = indexes.sources.get(row.document_id || inspection?.document_id);
  const sourceScopeLinks = table.key === 'source_manifest' ?
    (indexes.sourceInspections.get(row.document_id) || []).map(scope => {
      const structure = indexes.structures.get(scope.structure_id);
      return '<a data-route href="' + escape(scopeUrl(2, [structure, scope])) + '">' + escape(structure.name) + '</a>';
    }) : [];
  const details = table.details.map(([label, field]) => [label, valueAt(row, field)])
    .filter(([, value]) => value !== '' && value != null);
  const matchEvidence = table === LEVELS[2] ?
    (indexes.identityEvidence.get(row.component_id) || []).find(match => match.source_filename === inspection.source_filename) : null;
  const pageValues = table.key === 'source_manifest' ? ['1'] : evidencePages(row.source_pages);
  const gradePages = evidencePages(row.grade_pages);
  const pages = [...new Set([...gradePages, ...pageValues, ...evidencePages(matchEvidence?.identity_pages)])];
  const latestLink = table === LEVELS[0] && row.latest_inspection_id
    ? scopeUrl(1, [row]) + '&record=' + encodeURIComponent(row.latest_inspection_id) : '';
  const defect = table.key === 'defects' || table.key === 'review_defects';
  const report = table === LEVELS[1];
  const savedPanel = '<div class="saved-detail" id="saved-detail"><p role="status">Loading saved ' +
    (defect ? 'finding details' : 'report assessment') + '…</p></div>';
  const inspectionActions = report ? inspectionLinks(context.scopes[0], row, indexes.inspectionDefects.get(row.inspection_id)?.length || 0) : '';
  detail.innerHTML = '<div class="detail-header"><h2 id="detail-title" tabindex="-1">' + display(table.name(row)) +
    '</h2><button type="button" class="detail-close" id="close-detail">Close</button></div>' +
    (source && table.key !== 'source_manifest' ? '<p>' + escape(source.source_filename) + '</p>' : '') +
    (inspectionActions ? '<div class="detail-actions">' + inspectionActions + '</div>' : '') +
    (report ? savedPanel : '') + '<div id="record-summary">' + definitionList(details) + '</div>' + (defect ? savedPanel : '') +
    (latestLink ? '<div class="detail-actions"><a data-route href="' + escape(latestLink) + '">Open latest inspection and grading evidence</a></div>' : '') +
    sourceActions(source, table.key === 'source_manifest') +
    (table.key === 'other_sources' ? '<div class="detail-actions"><a href="' + escape(runUrl(row.id)) +
      '">Open saved report</a></div>' : '') +
    (sourceScopeLinks.length ? '<h3>Inspection scopes from this PDF</h3><div class="detail-actions">' +
      sourceScopeLinks.join('') + '</div>' : '') +
    (row.grade_evidence ? '<h3>Condition grade evidence</h3><blockquote>' + escape(row.grade_evidence) + '</blockquote>' : '') +
    (row.source_evidence ? '<h3>Source quotes</h3><blockquote>' + escape(row.source_evidence) + '</blockquote>' : '') +
    (matchEvidence?.identity_evidence ? '<h3>Component match evidence</h3><blockquote>' +
      escape(matchEvidence.identity_evidence) + '</blockquote>' : '') +
    (pages.length && source?.pdf_run_id ? '<h3>PDF pages</h3><div class="page-links">' +
      pages.map(page => '<button type="button" data-source-page="' + page +
        '" aria-pressed="false">Page ' + page + '</button>').join('') + '</div>' :
      pages.length && source ? '<p>PDF pages ' + escape(pages.join(', ')) + ' · Source PDF unavailable in this workspace.</p>' : '') +
    '<div id="source-page"></div>';
  detail.onclick = event => {
    const button = event.target.closest('[data-source-page]');
    if (!button) return;
    detail.querySelectorAll('[data-source-page]').forEach(link => link.setAttribute('aria-pressed', String(link === button)));
    const page = button.dataset.sourcePage;
    if (!source?.pdf_run_id) {
      element('source-page').textContent = 'Source PDF unavailable in this workspace.';
      return;
    }
    const path = '/api/runs/' + encodeURIComponent(source.pdf_run_id) + '/page/' + page;
    element('source-page').innerHTML = '<img src="' + path + '" alt="Source PDF page ' + page + '">' +
      '<a href="' + path + '" target="_blank" rel="noopener">Open page ' + page + ' in a new tab</a>';
    element('source-page').querySelector('img').onerror = event => {
      event.currentTarget.replaceWith(Object.assign(document.createElement('p'), {textContent: 'This PDF page could not load.'}));
    };
    element('source-page').scrollIntoView({block: 'nearest'});
  };
  const target = element('saved-detail');
  if (defect) {
    loadDetail(target, '/api/structure-workspace/defects/' + encodeURIComponent(row.defect_id), saved => {
      const rich = defectDetails(saved);
      const labels = new Set(['Damage grade', 'Grade status', 'Why it needs review']);
      element('record-summary').innerHTML = definitionList(details.filter(([label]) => labels.has(label)));
      target.innerHTML = rich;
    }, 'The full saved finding details are unavailable. The reviewed summary is shown above.');
  } else if (report) {
    loadDetail(target, '/api/structure-workspace/inspections/' + encodeURIComponent(row.inspection_id) + '/report', saved => {
      target.innerHTML = reportDetails(saved, source);
      if (saved.run_id === row.grade_run_id && saved.assessment.rationale === row.grade_rationale) {
        element('record-summary').innerHTML = definitionList(details.filter(([label]) => label !== 'Grade rationale'));
      }
    }, 'The original report assessment is unavailable in this workspace.');
  }
}

function render() {
  context = readContext(new URLSearchParams(location.search));
  element('register-label').textContent = 'Coastal infrastructure · ' + (context.sources ? 'Source library' : 'Reviewed cohort');
  element('register-title').textContent = context.sources ? 'Sources & review' :
    context.level ? context.scopes[0].name : 'Inspection register';
  element('register-intro').textContent = context.sources ?
    'Open the original PDFs and saved runs, or review records that need a confirmed scope.' :
    'Follow an asset from its inspection history to individual findings.';
  element('table-title').textContent = context.table.label;
  const graded = context.table === LEVELS[0] ? context.rows.filter(row => row.condition_score).length : null;
  element('table-description').textContent = graded === null ? context.table.description :
    graded + ' of ' + context.rows.length + ' latest inspections have a grade. ' + context.table.description;
  renderPath();
  renderFilters();
  renderRows();
  renderDetail();
}

function navigate(url, {replace = false, filtersOnly = false} = {}) {
  history[replace ? 'replaceState' : 'pushState'](null, '', url);
  if (filtersOnly) {
    context = readContext(new URLSearchParams(location.search));
    renderRows();
    if (!context.params.has('record') && !element('record-detail').hidden) renderDetail();
  } else {
    render();
  }
}

async function loadRegister() {
  element('loading').hidden = false;
  element('load-error').hidden = true;
  try {
    const response = await fetch('/api/structure-workspace');
    if (!response.ok) throw new Error('Register unavailable');
    setData(await response.json());
    element('snapshot-version').textContent = 'Reviewed cohort · ' + data.version;
    element('register-content').hidden = false;
    render();
  } catch {
    element('register-content').hidden = true;
    element('load-error').hidden = false;
  } finally {
    element('loading').hidden = true;
  }
}

document.addEventListener('click', event => {
  const route = event.target.closest('a[data-route]');
  if (route && data && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    const fromDetail = element('record-detail').contains(route);
    const next = new URL(route.href);
    if (next.searchParams.has('record') && !fromDetail) returnFocus = route.getAttribute('href');
    navigate(next.pathname + next.search);
    (element('detail-title') || element('table-title')).focus();
    return;
  }
  const button = event.target.closest('button');
  if (button?.id === 'close-detail') {
    navigate(contextUrl({record: null}));
    const previous = [...element('table-result').querySelectorAll('a')].find(link => link.getAttribute('href') === returnFocus);
    (previous || element('table-title')).focus();
  } else if (button?.dataset.page) {
    navigate(contextUrl({page: button.dataset.page}), {filtersOnly: true});
    element('table-title').focus();
  } else if (button?.hasAttribute('data-clear-filters')) {
    const cleared = Object.fromEntries(context.table.filters.map(([, field]) => [field, null]));
    navigate(contextUrl({...cleared, q: null, page: null, record: null}), {replace: true});
    element('table-search').focus();
  }
});
element('table-search').oninput = event => navigate(contextUrl({q: event.target.value, page: null, record: null}),
  {replace: true, filtersOnly: true});
element('filters').onchange = event => {
  if (event.target.dataset.filter) {
    navigate(contextUrl({[event.target.dataset.filter]: event.target.value, page: null, record: null}),
      {replace: true, filtersOnly: true});
  }
};
element('retry').onclick = loadRegister;
window.onpopstate = () => { if (data) render(); };
loadRegister();
