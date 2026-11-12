/* Evidence-linked defect output. Human labeling and cross-report trends are separate. */
let defectSection='', defectGrade='', defectType='', defectCheck=false, selectedDefectId='';
let pendingDefectState=null;
let linkedDefectView=null;
const factText=f=>f?.value==null?'':String(f.value);
const missingText=f=>factText(f)||({not_reported:'Not reported',not_applicable:'Not applicable',not_inspected:'Not inspected',unreadable:'Unreadable',ambiguous:'Unclear'})[f?.status]||'Not reported';
function inventoryIndexes(inv){
  const indexes={};
  for(const table of ['structures','sections','components','observations','defects','measurements','evidence','component_assessments','section_assessments'])indexes[table]=new Map((inv?.[table]||[]).map(r=>[r.id,r]));
  return indexes;
}
function inventorySources(value,inv){
  const ids=new Set();
  function walk(v){if(!v||typeof v!=='object')return;if(Array.isArray(v.evidence_ids))v.evidence_ids.forEach(id=>ids.add(id));for(const [k,child] of Object.entries(v))if(k!=='evidence_ids')walk(child);}
  walk(value);const sources=new Map((inv?.evidence||[]).map(e=>[e.id,e]));
  return [...ids].map(id=>sources.get(id)||{id,page:null,quote:'Evidence reference missing',verified:false});
}
function defectRows(inv,{section='',grade='',type='',check=false,query=''}={}){
  const ix=inventoryIndexes(inv);
  return (inv?.defects||[]).map(defect=>{
    const observation=ix.observations.get(defect.observation_id)||{};
    const component=ix.components.get(observation.component_id)||{};
    const sectionRecord=ix.sections.get(observation.section_id)||{};
    const structure=ix.structures.get(sectionRecord.structure_id)||{};
    const measurements=(inv.measurements||[]).filter(m=>m.defect_id===defect.id||(m.defect_id==null&&m.observation_id===observation.id));
    const sources=inventorySources([defect,observation,measurements],inv);
    const needsCheck=!sources.length||sources.some(e=>e.verified!==true)||component.coverage!=='inspected'||sectionRecord.coverage!=='inspected';
    return {defect,observation,component,section:sectionRecord,structure,measurements,sources,needsCheck};
  }).filter(r=>(!section||r.section.id===section)&&(!grade||r.defect.model_assessment?.damage_grade===grade)&&(!type||factText(r.defect.defect_type)===type)&&(!check||r.needsCheck)&&(!query||JSON.stringify(r).toLowerCase().includes(query.toLowerCase())));
}
function sectionName(section){return factText(section?.name)||'Unassigned section';}
function measurementText(m){return [factText(m.property),[missingText(m.raw_value),factText(m.unit)].filter(Boolean).join(' ')].filter(Boolean).join(': ');}
function resetDefectView(inv,p=new URLSearchParams()){
  defectSection=(inv?.sections||[]).some(s=>s.id===p.get('section'))?p.get('section'):'';
  defectGrade=p.get('grade')||'';defectType=p.get('type')||'';defectCheck=p.get('check')==='1';
  selectedDefectId=(inv?.defects||[]).some(d=>d.id===p.get('defect'))?p.get('defect'):'';
  $('search').value=p.get('q')||'';
}
function initLinkedDefectView(report){
  const pending=pendingDefectState?.run===runId?pendingDefectState.params:null;
  linkedDefectView={report,row:savedDefectRun(currentReportRow?.source_sha256||report.input_sha256),
    params:pending?.get('tab')==='defects'?pending:undefined};
  pendingDefectState=null;
  resetDefectView(null);
}
async function loadLinkedDefects(view){
  view.loading=true;
  try{
    const result=await api(`/api/runs/${encodeURIComponent(view.row.id)}/result`);
    if(result.extraction_mode!=='defect_v1'||!result.defect_inventory||
       (result.input_sha256&&result.input_sha256!==view.row.source_sha256))throw new Error('Invalid saved inventory');
    view.result=result;
  }catch{
    view.failed=true;
  }finally{
    view.loading=false;
    if(view===linkedDefectView&&view.report===currentResult&&activeTab==='defects')renderLinkedDefectTab();
  }
}
function renderLinkedDefectTab(){
  const view=linkedDefectView;
  if(view.result){
    const initial=!view.ready;
    if(initial){resetDefectView(view.result.defect_inventory,view.params);view.ready=true;}
    renderDefectTab(view.result,view.row.id);
    if(initial&&selectedDefectId)showDefectDetail(selectedDefectId,false,view.result,view.row.id);
    return;
  }
  syncResultTabs();
  history.replaceState(null,'',reportUrl(runId,activeTab));
  document.querySelector('.table-actions').hidden=true;
  const content=$('tableContent');
  if(!view.row){
    content.innerHTML='<div class="comparison-empty"><h3>No saved defect inventory</h3><p>Create a defect assessment for this report to extract individual findings and source evidence.</p><button class="secondary-button" id="prepareDefectAssessment">Create defect assessment →</button></div>';
    $('prepareDefectAssessment').onclick=prepareDefectAssessment;
  }else if(view.failed){
    content.innerHTML='<div class="comparison-empty"><h3>Could not load the saved defects</h3><button class="secondary-button" id="retryLinkedDefects">Try again</button></div>';
    $('retryLinkedDefects').onclick=()=>{view.failed=false;renderLinkedDefectTab();};
  }else{
    content.innerHTML='<p class="field-intro" role="status">Loading saved defects…</p>';
    if(!view.loading)loadLinkedDefects(view);
  }
}
function renderDefectResult(job){
  const r=currentResult,inv=r.defect_inventory,m=r.metrics,preview=r.preview_only;
  setWorkspaceView('assessment');renderReportNavigation();$('results').hidden=false;
  $('workspaceTitle').textContent=reportTitle(job);$('contractBadge').textContent=preview?'Source preview':'Defects and sections';
  $('resultBanner').hidden=preview?false:inv.status==='complete';
  $('resultBanner').className='result-banner'+(!preview?' warning':'');
  $('resultBanner').textContent=preview?'Source preview · no model call or scoring cost.':inv.status==='partial'?'Partial extraction · findings below cover the available input. Section scores are withheld.':'No validated defect output. Check review flags and run details before starting a new assessment.';
  $('assessmentSummary').hidden=preview;
  $('assessmentSummary').classList.add('inventory-mode');$('assessmentSummary').setAttribute('aria-label','Extraction summary');
  $('assessmentSummary').innerHTML=`<div class="inventory-summary"><span><b>${inv.sections.length}</b> sections</span><span><b>${inv.defects.length}</b> defects</span><span><b>${inv.measurements.length}</b> measurements</span><span class="inventory-kind">${escapeHtml(label(inv.document_type))}</span></div><p class="inventory-context">${escapeHtml(r.new_schema?.['Inspection date']?.value==='NR'?'Inspection date not reported':r.new_schema?.['Inspection date']?.value||'Inspection date not reported')} · Source facts and provisional model assessments</p>`;
  $('usageSummary').hidden=true;$('metrics').innerHTML=`<span>${m.pages} of ${r.coverage.pdf_pages} PDF pages</span><span>${r.coverage.partial_report?'Selected pages only':'Native-text extraction'}</span>`;
  const path=`/api/runs/${runId}/download/`;
  $('downloadZip').href=path+'outputs.zip';$('downloadJson').href=path+'result.json';
  $('runId').textContent=`RUN ${runId.slice(0,8)} · ${r.schema_version}`;$('evidencePanel').classList.remove('open');
  resetDefectView(inv,pendingDefectState?.run===runId?pendingDefectState.params:undefined);
  pendingDefectState=null;
  setResultTabs(preview?[['source_pages','Source pages',m.pages],['usage','Run details']]:[['defects','Defects',inv.defects.length],['sections','Section assessments',inv.sections.length],['observations','Other observations'],['new_schema','Report details'],['diagnostics','Review flags'],['usage','Run details']]);
  activeTab=preview?'source_pages':'defects';renderTab();
  if(selectedDefectId&&!preview)showDefectDetail(selectedDefectId,false);
}
function renderDefectTab(result=currentResult,sourceRunId=runId){
  syncResultTabs();const inv=result.defect_inventory,content=$('tableContent'),ix=inventoryIndexes(inv);
  const linked=sourceRunId!==runId,path=`/api/runs/${encodeURIComponent(sourceRunId)}/download/`;
  history.replaceState(null,'',reportUrl(runId,activeTab));document.querySelector('.table-actions').hidden=false;
  $('downloadTable').hidden=false;$('downloadTable').textContent='CSV ↓';
  $('downloadTable').href=path+`${activeTab==='defects'?'defects':activeTab==='sections'?'sections':'observations'}.csv`;
  if(activeTab==='sections')return renderSectionAssessments(inv,ix);
  if(activeTab==='observations'){
    const rows=(inv.observations||[]).filter(o=>o.kind!=='defect'&&(!defectSection||o.section_id===defectSection)&&JSON.stringify(o).toLowerCase().includes($('search').value.toLowerCase()));
    content.innerHTML=`<p class="field-intro">Inspected absences, repairs, recommendations and coverage limits are distinct from defects.</p><div class="record-list">${rows.map(o=>`<article class="record-card"><div class="record-number">${escapeHtml(label(o.kind))} · ${escapeHtml(label(o.temporal_context))}</div><h3>${escapeHtml(missingText(o.description))}</h3><p>${escapeHtml(sectionName(ix.sections.get(o.section_id)))} · ${escapeHtml(missingText(o.location))}</p>${inventorySources(o,inv).map(e=>`<button class="text-button" data-inventory-source="${escapeHtml(e.id)}">PDF ${escapeHtml(e.page??'?')} ↗</button>`).join(' ')}</article>`).join('')||'<div class="empty-table">No other observations in this scope. Adjust the section or search.</div>'}</div>`;
    bindInventorySources(content,inv);return;
  }
  const rows=defectRows(inv,{section:defectSection,grade:defectGrade,type:defectType,check:defectCheck,query:$('search').value});
  const types=[...new Set(inv.defects.map(d=>factText(d.defect_type)).filter(Boolean))].sort();
  const context=linked?`<p class="field-intro">${inv.status==='partial'?'<strong>Partial coverage.</strong> Findings cover the available inspection evidence. ':'Saved defect inventory. '}<a href="${escapeHtml(reportUrl(sourceRunId,'defects'))}">Open defect assessment ↗</a></p>`:'';
  content.innerHTML=`${context}<div class="inventory-controls"><label>Section<select id="defectSection"><option value="">All sections</option>${inv.sections.map(s=>`<option value="${escapeHtml(s.id)}" ${s.id===defectSection?'selected':''}>${escapeHtml(factText(ix.structures.get(s.structure_id)?.name)||'Structure')} · ${escapeHtml(sectionName(s))}</option>`).join('')}</select></label><label>Model grade<select id="defectGrade"><option value="">All grades</option>${['none','minor','moderate','advanced','severe','NR'].map(g=>`<option value="${g}" ${g===defectGrade?'selected':''}>${g==='NR'?'Not assessed':label(g)}</option>`).join('')}</select></label><label>Defect type<select id="defectType"><option value="">All types</option>${types.map(t=>`<option ${t===defectType?'selected':''} value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('')}</select></label><label class="inventory-check"><input id="defectCheck" type="checkbox" ${defectCheck?'checked':''}> Evidence / coverage gaps</label></div>
    <div class="inventory-section-strip" aria-label="Section navigation">${inv.sections.map(s=>`<button class="section-chip" data-section="${escapeHtml(s.id)}" aria-pressed="${String(s.id===defectSection)}"><span>${escapeHtml(sectionName(s))}</span><small>${inv.defects.filter(d=>ix.observations.get(d.observation_id)?.section_id===s.id).length} defects · ${escapeHtml(label(s.coverage))}</small></button>`).join('')}</div>
    <p class="inventory-count" role="status">${rows.length} of ${inv.defects.length} extracted defects · counts represent findings, not physical elements</p>
    ${rows.length?`<table class="defect-table"><caption class="sr-only">Extracted defects, measurements, model grades and source evidence</caption><thead><tr><th>Location</th><th>Observed finding</th><th>Model assessment</th><th>Measurements</th><th>Evidence</th></tr></thead><tbody>${rows.map(row=>{const d=row.defect,o=row.observation,a=d.model_assessment;return `<tr ${d.id===selectedDefectId?'class="selected"':''}><td data-column="Location"><b>${escapeHtml(sectionName(row.section))}</b><span>${escapeHtml(factText(row.component.name)||'Component not reported')}</span><small>${escapeHtml(missingText(o.location))}</small></td><td data-column="Finding"><button class="defect-open" data-defect="${escapeHtml(d.id)}">${escapeHtml(missingText(d.defect_type))} <span aria-hidden="true">↗</span></button><p>${escapeHtml(missingText(o.description))}</p><small>${escapeHtml(label(o.temporal_context||'unclear'))} · ${escapeHtml(missingText(o.observed_date))}</small></td><td data-column="Assessment"><span class="damage-grade grade-${escapeHtml(a.damage_grade)}">${escapeHtml(a.damage_grade==='NR'?'Not assessed':label(a.damage_grade))}</span><small>${escapeHtml(label(a.extent))} · ${escapeHtml(a.confidence)} confidence</small><small>Reported: ${escapeHtml(missingText(d.severity_raw))}</small></td><td data-column="Measurements">${row.measurements.map(m=>`<span class="measurement-value">${escapeHtml(measurementText(m))}</span>`).join('')||'<span class="muted">Not reported</span>'}</td><td data-column="Evidence">${row.sources.slice(0,2).map(e=>`<button class="text-button" data-inventory-source="${escapeHtml(e.id)}">PDF ${escapeHtml(e.page??'?')} ↗${e.verified?'':' · check'}</button>`).join('')||'<span class="muted">Evidence missing</span>'}${row.sources.length>2?`<button class="text-button" data-defect="${escapeHtml(d.id)}">${row.sources.length-2} more quotes</button>`:''}</td></tr>`;}).join('')}</tbody></table>`:`<div class="comparison-empty"><h3>${inv.document_type==='reference_only'?'Reference material has no inspection defects.':inv.defects.length?'No defects match these filters.':'No defect findings are available.'}</h3><p>${inv.defects.length?'Clear the filters to see all extracted findings.':linked?'Open the saved defect assessment to inspect coverage and other observations.':'Open Other observations, Report details or Review flags to inspect coverage and explicit findings.'}</p>${inv.defects.length?'<button id="clearDefectFilters" class="secondary-button">Clear filters</button>':''}</div>`}
    <details class="inventory-legend"><summary>Missing values and inspection coverage</summary><p>Not reported: absent from the supplied text. Not applicable: explicitly inapplicable. Not inspected: explicitly outside inspection. Unreadable: unavailable native text. Unclear: ambiguous source. An inspected absence is shown under Other observations. Model grade and extent are inferences; reported severity retains the source wording.</p><a class="text-button" href="${path}measurements.csv">Download measurements CSV ↓</a></details>`;
  $('defectSection').onchange=()=>{defectSection=$('defectSection').value;renderTab();$('defectSection').focus();};
  $('defectGrade').onchange=()=>{defectGrade=$('defectGrade').value;renderTab();$('defectGrade').focus();};
  $('defectType').onchange=()=>{defectType=$('defectType').value;renderTab();$('defectType').focus();};
  $('defectCheck').onchange=()=>{defectCheck=$('defectCheck').checked;renderTab();$('defectCheck').focus();};
  content.querySelectorAll('[data-section]').forEach(el=>el.onclick=()=>{const id=el.dataset.section;defectSection=id===defectSection?'':id;renderTab();[...content.querySelectorAll('[data-section]')].find(button=>button.dataset.section===id)?.focus();});
  content.querySelectorAll('[data-defect]').forEach(el=>el.onclick=()=>showDefectDetail(el.dataset.defect,true,result,sourceRunId));
  if($('clearDefectFilters'))$('clearDefectFilters').onclick=()=>{defectSection=defectGrade=defectType='';defectCheck=false;$('search').value='';renderTab();$('defectSection').focus();};
  bindInventorySources(content,inv,sourceRunId);
}
function bindInventorySources(container,inv,sourceRunId=runId){
  container.querySelectorAll('[data-inventory-source]').forEach(el=>el.onclick=()=>{
    const source=inv.evidence.find(e=>e.id===el.dataset.inventorySource);if(source)showScoringSource({...source,name:'Report evidence'},sourceRunId);
  });
}
function showDefectDetail(id,updateUrl=true,result=currentResult,sourceRunId=runId){
  const inv=result.defect_inventory,row=defectRows(inv).find(r=>r.defect.id===id);if(!row)return;
  selectedDefectId=id;if(updateUrl)history.replaceState(null,'',reportUrl(runId,activeTab));
  const d=row.defect,a=d.model_assessment,o=row.observation;
  const first=row.sources.find(s=>Number.isInteger(s.page));
  $('evidenceContent').innerHTML=`<div class="record-number">${escapeHtml(sectionName(row.section))} · ${escapeHtml(factText(row.component.name))}</div><h4>${escapeHtml(missingText(d.defect_type))}</h4><p>${escapeHtml(missingText(o.description))}</p><div class="evidence-fact"><b>Location · observation date</b>${escapeHtml(missingText(o.location))}<br>${escapeHtml(missingText(o.observed_date))} · ${escapeHtml(o.date_precision)} precision · ${escapeHtml(label(o.temporal_context))}</div><div class="evidence-fact"><b>Reported severity / urgency</b>${escapeHtml(missingText(d.severity_raw))} / ${escapeHtml(missingText(d.urgency_raw))}</div><div class="evidence-fact"><b>Measurements as reported</b>${row.measurements.map(m=>`<p>${escapeHtml(measurementText(m))}${factText(m.datum)?' · '+escapeHtml(factText(m.datum)):''}</p>`).join('')||'Not reported'}</div><div class="model-inference"><b>Model assessment · provisional</b><p>${escapeHtml(label(a.damage_grade))} · ${escapeHtml(label(a.extent))} · ${escapeHtml(a.confidence)} confidence</p><p>${escapeHtml(a.rationale)}</p><small>${escapeHtml(a.rubric_criterion)}${a.alternative_grade?' · Alternative: '+escapeHtml(a.alternative_grade):''}</small></div>${row.sources.map(e=>`<blockquote>${escapeHtml(e.quote)}</blockquote><button class="text-button" data-inventory-source="${escapeHtml(e.id)}">PDF page ${escapeHtml(e.page??'?')} ↗ · ${e.verified?'quote matched':'check quote'}</button>`).join('')||'<p>Source evidence is missing.</p>'}${first?`<img src="/api/runs/${encodeURIComponent(sourceRunId)}/page/${first.page}" alt="Source PDF page ${first.page}" loading="lazy">`:''}`;
  bindInventorySources($('evidenceContent'),inv,sourceRunId);revealSourceEvidence();
}
function prepareDefectAssessment(){
  if(busy||!runId)return;
  uploadId=runId;currentResult=null;currentReportRow=null;runId=null;clearBulkSelection();
  $('workflow').value='scoring';$('extractionMode').value='defect_v1';updateWorkflow();
  clearResults();setWorkspaceView('new');setBusy(false);$('workspaceTitle').textContent='New defect assessment';
  history.replaceState(null,'','?new=1');updateBulkButton();$('extract').focus();
}
function renderSectionAssessments(inv,ix){
  const q=$('search').value.toLowerCase();
  const assessments=new Map(inv.section_assessments.map(a=>[a.section_id,a]));
  const componentAssessments=new Map(inv.component_assessments.map(a=>[a.component_id,a]));
  $('tableContent').innerHTML=`<p class="field-intro">Section scores apply the rubric to linked component findings. Coverage and controlling findings remain visible. Scores are never averaged.</p><div class="section-assessment-list">${inv.sections.filter(s=>(!defectSection||s.id===defectSection)&&JSON.stringify(s).toLowerCase().includes(q)).map(s=>{const a=assessments.get(s.id),children=inv.components.filter(c=>c.section_id===s.id);return `<article class="section-assessment"><div class="section-score-heading"><div><span class="eyebrow">${escapeHtml(factText(ix.structures.get(s.structure_id)?.name)||'Structure')}</span><h3>${escapeHtml(sectionName(s))}</h3><p>${escapeHtml(label(s.coverage))} · Reported rating: ${escapeHtml(missingText(s.reported_rating))}</p></div>${severityBadge(a?.effective_score)}</div>${a?`<p>${escapeHtml(a.rationale)}</p><p class="hint">${escapeHtml(a.confidence)} confidence · ${escapeHtml(a.rubric_criterion)}</p>${a.withheld_reasons.length?`<div class="section-withheld"><b>Score unavailable</b><p>${escapeHtml(a.withheld_reasons.join(' '))}</p></div>`:''}`:'<p>No supported section assessment was returned.</p>'}<div class="section-components">${children.map(c=>{const ca=componentAssessments.get(c.id);return `<div><b>${escapeHtml(missingText(c.name))}</b><span>${escapeHtml(label(c.structural_role))} · ${escapeHtml(label(c.coverage))}</span><span>Model score: ${ca?.effective_score??'Unavailable'}</span>${ca?`<small>${escapeHtml(ca.rationale)}</small>${ca.withheld_reasons.length?`<small>${escapeHtml(ca.withheld_reasons.join(' '))}</small>`:''}`:''}</div>`;}).join('')}</div><button class="text-button" data-section-findings="${escapeHtml(s.id)}">Open section findings →</button></article>`;}).join('')||'<div class="empty-table">No sections in this scope. Open Model output to change the section filter.</div>'}</div>`;
  $('tableContent').querySelectorAll('[data-section-findings]').forEach(el=>el.onclick=()=>{defectSection=el.dataset.sectionFindings;activeTab='defects';$('search').value='';renderTab();});
}
