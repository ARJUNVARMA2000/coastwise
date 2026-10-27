const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function view(){const ctx=vm.createContext({URLSearchParams});vm.runInContext(fs.readFileSync('src/coastwise/static/reports.js','utf8'),ctx);return code=>vm.runInContext(code,ctx);}
test('group by exact PDF identity, not matching filenames; latest generation survives late imports',()=>{
  const run=view();
  const result=JSON.parse(run(`JSON.stringify(groupReports([
    {id:'old',source_sha256:'a',report_id:'original',filename:'same.pdf',score:2,started_utc:'2026-10-25T10:00:00Z',created_at:9999999999},
    {id:'new',source_sha256:'a',report_id:'revision',filename:'same.pdf',score:4,started_utc:'2026-10-26T10:00:00Z',created_at:1},
    {id:'different-pdf',source_sha256:'b',filename:'same.pdf',score:1}
  ]))`));
  assert.equal(result.length,2);assert.equal(result[0].row.id,'new');assert.equal(result[0].versions.length,2);
});
test('duplicate imports are one assessment; previous means earlier and never a newer run',()=>{
  const run=view();
  run(`allLibraryRows=[{id:'new',source_sha256:'a',report_id:'revision',score:4,started_utc:'2026-10-26'},
    {id:'original',source_sha256:'a',report_id:'original',score:2,started_utc:'2026-10-25'},
    {id:'duplicate',source_sha256:'a',report_id:'original',score:2,started_utc:'2026-10-25'}];`);
  assert.equal(run('reportVersions(allLibraryRows[0]).length'),2);
  assert.equal(run('previousAssessment(allLibraryRows[0]).id'),'original');
  assert.equal(run('previousAssessment(allLibraryRows[1])'),null);
});
test('workflow metadata, missing references and unresolved evidence are not factual disagreements',()=>{
  const run=view();const counts=JSON.parse(run(`JSON.stringify(comparisonCounts({fields:[
    {status:'equivalent'},{status:'partial'},{status:'insufficient_evidence'},
    {status:'both_missing'},{status:'human_missing'},{status:'workflow_metadata'}]}))`));
  assert.deepEqual(counts,{agreement:1,difference:1,uncertain:1,missing:2,total:5});
  assert.equal(run(`verdictLabel({status:'equivalent',evidence_verified:false})`),'Agrees · check evidence');
});
test('attention includes unverified agreeing evidence without reclassifying it as disagreement',()=>{
  const run=view();
  run(`const comparison={reference_id:'human-row',fields:[
    {field:'Material',status:'equivalent',evidence_verified:false},
    {field:'Date',status:'both_missing'},{field:'Location',status:'human_missing'}]};`);
  assert.equal(run('reportHasAttention({human_comparison:comparison})'),true);
  assert.equal(run('comparisonCounts(comparison).difference'),0);
  assert.equal(run('filteredComparisonFields(comparison.fields,"attention").length'),1);
  assert.equal(run('filteredComparisonFields(comparison.fields,"agreement").length'),1);
  assert.equal(run('reportHasAttention({human_comparison:{...comparison,reference_id:null}})'),false);
});
test('comparison filtering keeps original evidence indexes and sorts discrepancies ahead of quote checks',()=>{
  const run=view();
  run(`const fields=[{field:'Rating',status:'equivalent',evidence_verified:false},
    {field:'Material',status:'different',predicted:'Concrete'},
    {field:'Date',status:'insufficient_evidence'},
    {field:'Environment',status:'human_missing'}];`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(filteredComparisonFields(fields,"attention").map(x=>x.i))')),[1,2,0]);
  assert.equal(run('filteredComparisonFields(fields,"all","CONCRETE")[0].i'),1);
  assert.equal(run('filteredComparisonFields(fields,"attention","Environment").length'),0);
  assert.equal(run('filteredComparisonFields(fields,"all").length'),4);
  assert.equal(run('filteredComparisonFields([{field:"Primary_Material",status:"equivalent",predicted:"Concrete"}],"all","primary material").length'),1);
});
test('all fields keep their saved order; targeted difference filtering keeps the correct source indexes',()=>{
  const run=view();
  run(`const fields=[{field:'Title',status:'equivalent'},
    {field:'Date',status:'both_missing'},
    {field:'Material',status:'different'},
    {field:'Exposure',status:'insufficient_evidence'},
    {field:'Repairs',status:'partial'}];`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(filteredComparisonFields(fields,"all").map(x=>x.i))')),[0,1,2,3,4]);
  assert.deepEqual(JSON.parse(run('JSON.stringify(filteredComparisonFields(fields,"difference").map(x=>x.i))')),[2,4]);
  assert.equal(run('filteredComparisonFields(fields,"difference","Repairs")[0].i'),4);
  assert.deepEqual(JSON.parse(run('JSON.stringify(comparisonCounts({fields}))')),{agreement:1,difference:2,uncertain:1,missing:1,total:5});
});
test('output availability does not label previews, failed or partial reports as complete extractions',()=>{
  const run=view();
  assert.equal(run('savedOutputLabel({status:"complete",new_schema_values:{Title:"Pier",Date:"NR"}})'),'2 extracted fields');
  assert.equal(run('savedOutputLabel({status:"complete",preview_only:true,new_schema_values:{Title:"Pier"}})'),'Source preview');
  assert.equal(run('savedOutputLabel({status:"complete",has_failures:true,new_schema_values:{Title:"Pier"}})'),'Partial model output');
  assert.equal(run('savedOutputLabel({status:"failed"})'),'Processing incomplete');
  assert.equal(run('savedOutputLabel({status:"queued"})'),'Queued');
});
test('report links retain collection and selected tab with URL-safe identifiers',()=>{
  const run=view();run(`let libraryBatch='collection & two';`);
  const url=new URL(run(`reportUrl('saved / report','judge')`),'http://localhost');
  assert.equal(url.searchParams.get('batch'),'collection & two');
  assert.equal(url.searchParams.get('run'),'saved / report');
  assert.equal(url.searchParams.get('tab'),'judge');
});
