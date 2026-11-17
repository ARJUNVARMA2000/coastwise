const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function view() {
  const elements = new Map();
  const context = vm.createContext({
    URLSearchParams, location: {search: '?inspection=report'},
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, {value: '', addEventListener() {}});
        return elements.get(id);
      },
      addEventListener() {},
    },
    window: {addEventListener() {}},
    // Leave the page's startup request pending; no network or application bootstrap.
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync('src/coastwise/static/extraction.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}

test('fastener variants and location-specific labels share categories without changing their text', () => {
  const run = view();
  for (const type of ['Loose bolt', 'Loose bolts', 'Loose washer and bolt', 'loose_bolt',
                      'bolt and washer loose and corroded', 'missing/loose cap bolts',
                      'loose/broken washers', 'loose/tightened bolt', 'loose/marine growth']) {
    assert.equal(run(`defectTypeCategories({defect_type: ${JSON.stringify(type)}}).includes('loose-fasteners')`), true, type);
    assert.equal(run(`typeLabel({defect_type: ${JSON.stringify(type)}})`), type);
  }
  for (const type of ['Missing bolt', 'missing_bolts', 'Missing nut at both bottom bolts',
                      'Missing nut at 2nd bolt from bottom on South side', 'Missing lock washers/nuts']) {
    assert.equal(run(`defectTypeCategories({defect_type: ${JSON.stringify(type)}}).includes('missing-fasteners')`), true, type);
    assert.equal(run(`defectTypeCategories({defect_type: ${JSON.stringify(type)}}).includes('missing-material')`), false, type);
  }
  assert.equal(run(`defectTypeCategories({defect_type: 'Missing coating'}).join()`), 'coating');
  assert.equal(run(`defectTypeCategories({defect_type: 'missing timber caps'}).join()`), 'missing-material');
  assert.equal(run(`defectTypeCategories({defect_type: 'missing material and loose bolt'}).includes('missing-fasteners')`), false);
});

test('combined types support each named category and ignore an explicit absence', () => {
  const run = view();
  assert.equal(run(`defectTypeCategories({defect_type: 'minor coation loss/corrosion; loose bolts'}).join()`),
               'coating,corrosion,loose-fasteners');
  assert.equal(run(`defectTypeCategories({defect_type: 'heavy marine growth; no scouring'}).join()`), 'marine-growth');
  assert.equal(run(`defectTypeCategories({defect_type: 'spall with exposed rebar'}).sort().join()`), 'reinforcement,spalling');
});

test('unreported and ambiguous labels stay discoverable without inventing a defect type', () => {
  const run = view();
  for (const type of [null, '', '  ', 'NR', 'NA', 'NONE']) {
    assert.equal(run(`defectTypeCategories({defect_type: ${JSON.stringify(type)}}).join()`), 'unreported');
  }
  for (const type of ['component', 'Bolts to Mounting Plate', 'CS', 'source rating only', 'unfamiliar future type']) {
    assert.equal(run(`defectTypeCategories({defect_type: ${JSON.stringify(type)}}).join()`), 'other', type);
  }
});

test('options count records once per category and contain only applicable alphabetical categories', () => {
  const run = view();
  run(`const defects = ['Loose bolt', 'loose_bolts', 'coating loss & corrosion', 'corrosion/corrosion', 'component']
    .map(defect_type => Object.freeze({defect_type}));
    const original = JSON.stringify(defects);`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(defectTypeOptions(defects))')), [
    ['coating', 'Coating damage (1)'], ['corrosion', 'Corrosion (2)'],
    ['loose-fasteners', 'Loose fasteners & connections (2)'], ['other', 'Other / unspecified (1)'],
  ]);
  assert.equal(run('JSON.stringify(defects) === original'), true);
  assert.equal(run('defectTypeOptions([]).length'), 0);
});

const fixture = `
  const observations = {o1: {component_name: 'Fender', section: 'North'}, o2: {component_name: 'Pile'}};
  const defects = [
    {defect_id: 'd1', obs_id: 'o1', defect_type: 'Loose bolt', damage_grade: 2, grading_status: 'graded',
     findings_text: 'Washer at north edge is loose.', findings_text_src: 'p2 ¶1'},
    {defect_id: 'd2', obs_id: 'o2', defect_type: 'loose bolts', damage_grade: 3, grading_status: 'uncertain',
     findings_text: 'South bolts loose.', findings_text_src: 'p1 ¶1'},
    {defect_id: 'd3', obs_id: 'o1', defect_type: 'coating loss and corrosion', grading_status: 'insufficient_evidence',
     findings_text: 'Coating is missing.', findings_text_src: 'p3 ¶1'},
  ];
  const measurements = [{defect_id: 'd1', property: 'Gap', value: 2, unit: 'mm'}];
  current = {citations: [{row_id: 'd1', field: 'findings_text', evidence_id: 'e1', page: 2,
                        verified_quote: 'Washer at north edge is loose.'}]};
  const rows = () => defectTable(defects, observations, measurements, () => true);
`;

test('category filters intersect existing filters, preserve document order and never duplicate a finding', () => {
  const run = view();
  run(fixture);
  run(`$('x-type').value = 'loose-fasteners';`);
  assert.equal(run(`rows().rows.map(d => d.defect_id).join()`), 'd2,d1');
  run(`$('x-component').value = 'Fender'; $('x-grade').value = '2'; $('x-assessment').value = 'graded';`);
  assert.equal(run('rows().rows[0] === defects[0]'), true);
  assert.equal(run('rows().rows.length'), 1);
  run(`$('x-grade').value = '3';`);
  assert.equal(run('rows().rows.length'), 0);
  run(`$('x-grade').value = ''; $('x-assessment').value = 'uncertain'; $('x-type').value = 'coating';`);
  assert.equal(run(`rows().rows.map(d => d.defect_id).join()`), 'd3');
  run(`$('x-type').value = 'corrosion';`);
  assert.equal(run(`rows().rows.map(d => d.defect_id).join()`), 'd3');
});

test('raw wording, measurement and evidence links survive consolidation and exact-text search', () => {
  const run = view();
  run(fixture);
  run(`$('x-type').value = 'loose-fasteners';`);
  const html = run('rows().line(defects[0])');
  assert.match(html, />Loose bolt<\/a>/);
  assert.match(html, /data-defect="d1"/);
  assert.match(html, /data-source="e1"/);
  assert.match(html, /defect=d1/);
  assert.match(html, /2 mm/);
  assert.equal(run(`defectTable(defects, observations, measurements,
    d => Object.values(d).join(' ').toLowerCase().includes('north edge')).rows[0].defect_id`), 'd1');
});
