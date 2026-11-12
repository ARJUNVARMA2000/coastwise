const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function view(){const ctx=vm.createContext({URLSearchParams});vm.runInContext(fs.readFileSync('src/coastwise/static/defects.js','utf8'),ctx);vm.runInContext(fs.readFileSync('src/coastwise/static/reports.js','utf8'),ctx);return code=>vm.runInContext(code,ctx);}
const fixture=`const f=value=>({value,status:value==null?'not_reported':'reported',evidence_ids:value==null?[]:['e']});
const inv={structures:[{id:'s',name:f('Harbor')}],sections:[{id:'west',structure_id:'s',name:f('West'),coverage:'inspected'},{id:'east',structure_id:'s',name:f('East'),coverage:'partially_inspected'}],
components:[{id:'c1',section_id:'west',name:f('Piles'),coverage:'inspected'},{id:'c2',section_id:'east',name:f('Deck'),coverage:'partially_inspected'}],
observations:[{id:'o1',component_id:'c1',section_id:'west',description:f('Steel section loss'),location:f('Station 2')},{id:'o2',component_id:'c2',section_id:'east',description:f('Concrete spalling'),location:f('Station 3')}],
defects:[{id:'d1',observation_id:'o1',defect_type:f('Corrosion'),model_assessment:{damage_grade:'advanced'}},{id:'d2',observation_id:'o2',defect_type:f('Spalling'),model_assessment:{damage_grade:'moderate'}}],
measurements:[{id:'m1',defect_id:'d1',observation_id:'o1',property:f('Remaining thickness'),raw_value:f('12'),unit:f('mm')},{id:'m2',defect_id:'d1',observation_id:'o1',property:f('Pit depth'),raw_value:f('2'),unit:f('mm')}],
evidence:[{id:'e',page:9,quote:'Supported quote',verified:true}],component_assessments:[],section_assessments:[]};`;
test('section, grade and text filters keep the original defect and evidence identities',()=>{
  const run=view();run(fixture);
  assert.equal(run(`defectRows(inv,{section:'east'})[0].defect.id`),'d2');
  assert.equal(run(`defectRows(inv,{grade:'advanced'})[0].defect.id`),'d1');
  assert.equal(run(`defectRows(inv,{type:'Corrosion',query:'STATION 2'})[0].sources[0].page`),9);
  assert.equal(run(`defectRows(inv,{section:'west',grade:'moderate'}).length`),0);
  assert.equal(run(`defectRows(inv,{query:'12'})[0].defect.id`),'d1');
});
test('one defect retains multiple measurements and an explicit coverage-gap filter',()=>{
  const run=view();run(fixture);
  assert.equal(run(`defectRows(inv)[0].measurements.length`),2);
  assert.equal(run(`defectRows(inv,{check:true})[0].defect.id`),'d2');
  assert.equal(run(`measurementText(inv.measurements[0])`),'Remaining thickness: 12 mm');
  run(`inv.evidence[0].verified=false`);
  assert.equal(run(`defectRows(inv,{check:true}).length`),2);
});
test('missing evidence remains a visible record with an evidence-gap state',()=>{
  const run=view();run(fixture);run(`inv.evidence=[]`);
  assert.equal(run(`defectRows(inv,{check:true}).length`),2);
  assert.equal(run(`defectRows(inv)[0].sources[0].verified`),false);
  assert.equal(run(`missingText(f(null))`),'Not reported');
});
test('current defect output without an overall score is selected ahead of an old report score',()=>{
  const run=view();
  assert.equal(run(`groupReports([{id:'old',source_sha256:'same',score:3,started_utc:'2026-10-25'},
    {id:'new',source_sha256:'same',score:null,inventory_status:'complete',extraction_mode:'defect_v1',started_utc:'2026-10-27'}])[0].row.id`),'new');
  assert.equal(run(`savedOutputLabel({status:'complete',extraction_mode:'defect_v1',inventory_status:'complete',section_count:2,defect_count:4,measurement_count:6})`),'2 sections · 4 defects · 6 measurements');
  assert.equal(run(`savedOutputLabel({status:'complete',extraction_mode:'defect_v1',inventory_status:'partial'})`),'Scoped defect output');
  assert.equal(run(`assessmentName({extraction_mode:'defect_v1'})`),'Defects and sections');
});
test('report review finds the latest usable defect inventory for the exact PDF across collections',()=>{
  const run=view();
  run(`const saved={source_sha256:'same',filename:'inspection.pdf',batch_id:'defect collection',
    extraction_mode:'defect_v1',status:'complete',inventory_status:'complete'};
    allLibraryRows=[
      {...saved,id:'old',started_utc:'2026-10-25',created_at:9999999999},
      {...saved,id:'current',started_utc:'2026-10-26',inventory_status:'partial',defect_count:0},
      {...saved,id:'other-pdf',source_sha256:'different',started_utc:'2026-10-27'},
      {...saved,id:'failed',status:'failed',started_utc:'2026-10-28'},
      {...saved,id:'no-output',inventory_status:'failed',started_utc:'2026-10-29'},
      {...saved,id:'preview',preview_only:true,started_utc:'2026-10-30'},
      {...saved,id:'report',batch_id:'report collection',extraction_mode:'report_v1',started_utc:'2026-10-31'}
    ];`);
  assert.equal(run(`savedDefectRun('same').id`),'current');
  assert.equal(run(`savedDefectRun('same').defect_count`),0);
  assert.equal(run(`savedDefectRun('unknown')`),null);
  assert.equal(run(`savedDefectRun()`),null);
});
test('deep links preserve section, selected defect and filters with URL-safe encoding',()=>{
  const run=view();run(`let currentResult={extraction_mode:'defect_v1'},runId='run',libraryBatch='collection';
    const $=id=>({value:'steel & concrete'});
    defectSection='west:a';defectGrade='advanced';defectType='Section loss';defectCheck=true;selectedDefectId='c1:d1';`);
  const url=new URL(run(`reportUrl('run','defects')`),'http://localhost');
  assert.equal(url.searchParams.get('section'),'west:a');
  assert.equal(url.searchParams.get('defect'),'c1:d1');
  assert.equal(url.searchParams.get('q'),'steel & concrete');
  assert.equal(url.searchParams.get('check'),'1');
  assert.equal(new URL(run(`reportUrl('other','defects')`),'http://localhost').searchParams.has('defect'),false);
  run(`currentResult.extraction_mode='report_v1'`);
  assert.equal(new URL(run(`reportUrl('run','defects')`),'http://localhost').searchParams.get('defect'),'c1:d1');
  assert.equal(new URL(run(`reportUrl('run','judge')`),'http://localhost').searchParams.has('defect'),false);
});
test('reference material can be filtered independently of human comparison',()=>{
  const run=view();
  assert.equal(run(`libraryMatchesFilter({document_type:'reference_only'},'reference')`),true);
  assert.equal(run(`libraryMatchesFilter({document_type:'reference_only'},'inspection')`),false);
  assert.equal(run(`libraryMatchesFilter({document_type:'mixed'},'inspection')`),true);
  assert.equal(run(`libraryMatchesFilter({},'all')`),true);
});
