/* Report identity and review views. Saved data only; opening a view never starts inference. */
let allLibraryRows=[], reportIndex=null, currentReportRow=null;
let comparisonFilter='all', libraryFilter='all';
const severityNames={1:'Good',2:'Fair',3:'Poor',4:'Serious / critical'};
const fieldNames={'Source/Report Name':'Report title','Structure ID/name':'Structure','Component/element':'Components',Inspecting_Firm:'Inspecting firm',Primary_Material:'Primary material','Secondary_Material(s)':'Other materials','Reported condition rating_raw':'Reported condition','Rating scale_raw':'Report’s rating scale',Number_defects:'Number of defects','Defect type(s)':'Defect types','Defect Severity/ Action urgency':'Severity and action urgency'};
const fieldName=name=>fieldNames[name]||name;
const displayValue=value=>Array.isArray(value)?value.join(', '):value==null||value===''?'Not recorded':String(value);
const reportTime=r=>Date.parse(r.started_utc)||Number(r.created_at||0)*1000;
const reportKey=r=>r.source_sha256||r.id;
function reportTitle(r){return String(r.filename||'Untitled report').replace(/\.pdf$/i,'').replace(/[_-]+/g,' ').replace(/\bmts\b/gi,'MTS').replace(/\bdsny\b/gi,'DSNY').replace(/\b[a-z]/g,c=>c.toUpperCase());}
function assessmentName(r){return r.preview_only?'Source preview':r.full_schema?'LLM assessment':'Rubric assessment';}
function savedOutputLabel(r){
  if(r.preview_only)return 'Source preview';
  if(r.status!=='complete')return ({running:'Processing',queued:'Queued',failed:'Processing incomplete',skipped:'Not processed'})[r.status]||'Saved report';
  if(r.has_failures)return 'Partial model output';
  const count=Object.keys(r.new_schema_values||{}).length;
  return count?`${count} extracted fields`:'Rubric findings';
}
function uniqueVersions(rows){
  const seen=new Set();
  return [...rows].sort((a,b)=>reportTime(b)-reportTime(a)).filter(r=>{const key=r.report_id||r.id;if(seen.has(key))return false;seen.add(key);return true;});
}
function reportVersions(row){return uniqueVersions([row,...allLibraryRows.filter(r=>reportKey(r)===reportKey(row)&&r.id!==row.id)]);}
function previousAssessment(row){const versions=reportVersions(row).filter(r=>r.score!=null&&!r.preview_only);return versions[versions.findIndex(r=>r.id===row.id)+1]||null;}
function groupReports(rows){
  const grouped=new Map();
  for(const r of rows){const key=reportKey(r);if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(r);}
  return [...grouped.values()].map(items=>{const versions=uniqueVersions(items);return {row:versions.find(r=>r.score!=null&&!r.preview_only)||versions[0],versions};}).sort((a,b)=>reportTitle(a.row).localeCompare(reportTitle(b.row),undefined,{numeric:true}));
}
async function ensureReportIndex(force=false){
  if(!settings.bulk_available)return null;
  if(force||!reportIndex){reportIndex=await api('/api/library');allLibraryRows=reportIndex.rows;}
  $('libraryCount').textContent=String(groupReports(allLibraryRows).length);
  return reportIndex;
}
function setWorkspaceView(view){
  document.body.dataset.view=view;
  document.querySelector('.controls').hidden=view!=='new';
  $('openLibrary').setAttribute('aria-current',view==='reports'?'page':'false');
  $('newAssessment').setAttribute('aria-current',view==='new'?'page':'false');
  $('reportNavigation').hidden=view!=='assessment';
  $('reportRail').hidden=view!=='assessment'||!settings.bulk_available;
  $('reportMetadata').hidden=view!=='assessment';
  $('workspaceEyebrow').textContent=view==='reports'?'SAVED REPORTS':view==='assessment'?'REPORT REVIEW':'NEW ASSESSMENT';
}
function severityBadge(score){return score==null?'<span class="muted">No overall score</span>':`<span class="severity-badge severity-${Number(score)}"><b>${escapeHtml(score)}</b> ${escapeHtml(severityNames[score]||'Unclassified')}</span>`;}
function factualFields(c){return (c?.fields||[]).filter(f=>f.status!=='workflow_metadata');}
function verdictKind(f){
  if(['partial','different','ai_missing','missing_prediction'].includes(f.status))return 'difference';
  if(['equivalent','match','compatible_precision'].includes(f.status))return 'agreement';
  if(['both_missing','human_missing','reference_missing'].includes(f.status))return 'missing';
  return 'uncertain';
}
function verdictLabel(f){const name=({equivalent:'Agrees',match:'Agrees',compatible_precision:'Compatible detail',partial:'Partly agrees',different:'Disagrees',ai_missing:'AI missed information',missing_prediction:'AI missed information',both_missing:'Neither records it',human_missing:'Human value missing',reference_missing:'Human value missing',insufficient_evidence:'Unresolved evidence',manual_review:'Needs review',not_assessed:'Field not extracted'})[f.status]||'Comparison unavailable';return name+(f.evidence_verified===false?' · check evidence':'');}
function comparisonCounts(c){
  const fields=factualFields(c),counts={agreement:0,difference:0,uncertain:0,missing:0};
  fields.forEach(f=>counts[verdictKind(f)]++);return {...counts,total:fields.length};
}
function needsAttention(f){return ['difference','uncertain'].includes(verdictKind(f))||f.evidence_verified===false;}
function reportHasAttention(row){return Boolean(row.human_comparison?.reference_id&&factualFields(row.human_comparison).some(needsAttention));}
function reportUrl(id,tab){
  const p=new URLSearchParams({run:id});
  if(tab)p.set('tab',tab);
  if(libraryBatch)p.set('batch',libraryBatch);
  return '?'+p.toString();
}
function returnToReports(){
  history.replaceState(null,'',libraryBatch?'?batch='+encodeURIComponent(libraryBatch):'?library=1');
  showLibrary();
}
function navigatorGroups(){
  const groups=groupReports(libraryRows.length?libraryRows:allLibraryRows);
  return currentReportRow&&!groups.some(g=>reportKey(g.row)===reportKey(currentReportRow))?groupReports(allLibraryRows):groups;
}
function renderReportRail(){
  if(!settings.bulk_available)return;
  const key=currentReportRow&&reportKey(currentReportRow);
  const groups=navigatorGroups().map(g=>reportKey(g.row)===key?{...g,row:currentReportRow}:g);
  $('reportPicker').innerHTML=groups.map(({row:r})=>`<option value="${escapeHtml(r.id)}" ${reportKey(r)===key?'selected':''}>${escapeHtml(reportTitle(r))}</option>`).join('');
  $('reportRailList').innerHTML=groups.map(({row:r})=>`<a class="rail-report" href="${reportUrl(r.id)}" ${reportKey(r)===key?'aria-current="page"':''}><span>${escapeHtml(reportTitle(r))}</span><small>${escapeHtml(savedOutputLabel(r))}</small></a>`).join('');
  $('reportRailList').querySelectorAll('a').forEach(el=>el.onclick=e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();openSavedRun(new URL(el.href).searchParams.get('run'));});
  $('reportPicker').onchange=()=>openSavedRun($('reportPicker').value);
  $('railBack').onclick=returnToReports;
  $('reportRailList').querySelector('[aria-current="page"]')?.scrollIntoView({block:'nearest'});
}
function renderReportNavigation(){
  const row=currentReportRow;
  $('reportNavigation').hidden=false;
  $('reportNavigation').innerHTML='<button id="backToReports" class="text-button">← Reports</button>';
  $('backToReports').onclick=returnToReports;
  $('reportMetadata').textContent=[currentResult?.config?.model||row?.model,currentResult?.started_utc?new Date(currentResult.started_utc).toLocaleDateString():null].filter(Boolean).join(' · ');
  renderReportRail();
}
function renderOverview(){
  const r=currentResult,a=r.assessment,c=currentReportRow?.human_comparison,fieldCount=Object.keys(r.new_schema||{}).length;
  const components=scoredComponents().map((x,i)=>({...x,i})).filter(x=>x.component.observed_condition!=='NR').sort((x,y)=>(y.component.component_score||0)-(x.component.component_score||0));
  $('tableContent').innerHTML=`<div class="overview-content"><section><div class="output-progress" aria-label="Saved output coverage"><span>${fieldCount?`<b>${fieldCount}</b> extracted fields`:'Rubric findings saved'}</span><span><b>${escapeHtml(r.metrics.pages)} / ${escapeHtml(r.coverage.pdf_pages)}</b> source pages included</span><button class="text-button" data-go-tab="${r.full_schema?'new_schema':'components'}">View model output →</button></div><h3>Assessment summary</h3><p>${escapeHtml(a?.rationale||'An overall assessment is unavailable. Inspect the saved model output and source evidence.')}</p></section><section><div class="section-heading"><h3>Key component findings</h3><button class="text-button" data-go-tab="components">All findings →</button></div>${components.slice(0,3).map(x=>`<article class="finding-row"><div><b>${escapeHtml(x.component.component)}</b><p>${escapeHtml(x.component.observed_condition)}</p></div><button class="text-button" data-overview-component="${x.i}">Evidence ↗</button></article>`).join('')||'<p>No validated component findings are available.</p>'}</section><section class="overview-support"><div class="section-heading"><h3>Human-label comparison</h3><button class="text-button" data-go-tab="judge">Open comparison →</button></div><p class="hint">${c?.reference_id?(c.judge_status==='complete'?`${factualFields(c).length} fields compared with the saved human labels. Read the paired answers and supporting explanations.`:'Matched human labels are available; a completed comparison is not available.'):'No matched human labels are available for this report.'}</p><details class="overview-review"><summary>Assessment notes & quality checks</summary><p>${r.review_flags?.length||0} automated flags · Model confidence: ${escapeHtml(a?.confidence||'unavailable')}</p><button class="text-button" data-go-tab="diagnostics">View review flags →</button><p class="hint">Model severity is provisional. Source quotations and valid output formats do not establish engineering accuracy.</p></details></section></div>`;
  $('tableContent').querySelectorAll('[data-go-tab]').forEach(el=>el.onclick=()=>{activeTab=el.dataset.goTab;renderTab();});
  $('tableContent').querySelectorAll('[data-overview-component]').forEach(el=>el.onclick=()=>showScoringComponent(scoredComponents()[Number(el.dataset.overviewComponent)]));
}
function filteredComparisonFields(fields,filter,query=''){
  const priority=f=>verdictKind(f)==='difference'?0:verdictKind(f)==='uncertain'?1:f.evidence_verified===false?2:3;
  const visible=fields.map((f,i)=>({f,i})).filter(({f})=>(filter==='all'||(filter==='agreement'?verdictKind(f)==='agreement':filter==='difference'?verdictKind(f)==='difference':needsAttention(f)))&&(fieldName(f.field)+' '+JSON.stringify(f)).toLowerCase().includes(query.toLowerCase()));
  return ['attention','difference'].includes(filter)?visible.sort((a,b)=>priority(a.f)-priority(b.f)):visible;
}
function sourceLinks(evidence,field,index,type){
  const links=(evidence||[]).map((e,k)=>`<button class="text-button" data-${type}-source="${index}" data-source-index="${k}" aria-label="${type==='model'?'Model':'Comparison'} evidence for ${escapeHtml(fieldName(field))}, PDF page ${escapeHtml(e.page)}, quote ${k+1}">${type==='comparison'?'Judge · ':''}PDF p. ${escapeHtml(e.page)} ↗</button>`);
  return links.length>2?links[0]+`<details class="more-source-quotes"><summary>${links.length-1} more quotes</summary><div>${links.slice(1).join('')}</div></details>`:links.join('');
}
function renderHumanComparison(){
  const r=currentResult,c=currentReportRow?.human_comparison,fields=factualFields(c),counts=comparisonCounts(c);
  const query=$('search').value;
  const visible=filteredComparisonFields(fields,comparisonFilter,query),attention=fields.filter(needsAttention).length;
  const filters=[['all','All fields',fields.length],['agreement','Agreements',counts.agreement],['difference','Differences',counts.difference],['attention','Review checks',attention]];
  $('tableContent').innerHTML=`<div class="comparison-content">
    <div class="comparison-intro"><div class="comparison-title"><h2>Model answers vs human labels</h2><span class="judge-label">${c?.mode==='llm_judge'?'LLM judge':'Saved comparison'}</span></div>
      <p class="hint">Read model answers alongside the saved human labels. <b>NR</b> = not reported; <b>NA</b> = not applicable.</p>
      <details class="comparison-summary"><summary>Comparison summary & context</summary>${c?.reference_id&&fields.length?`<p class="comparison-summary-counts">${counts.agreement} agree · ${counts.difference} differences · ${counts.uncertain} unresolved · ${counts.missing} not comparable</p>`:''}<p class="hint">${!c?.reference_id?'No human labels are matched to this PDF.':c.judge_status==='complete'?'Agreement is an automated judgment against the saved human labels.':'Limited comparison: no completed current-reference judge is available.'} Missing labels and unresolved evidence are separate from disagreements.</p>${c?.human_raw_rating?`<p><b>Human-reported condition</b> ${escapeHtml(c.human_raw_rating)}</p><p class="hint">Source wording from the human-labeled table; there is no reviewed numerical 1–4 human score.</p>`:''}</details>
    </div>
    ${fields.length?`<div class="comparison-controls"><div class="filter-buttons" role="group" aria-label="Comparison fields">${filters.map(([key,name,count])=>`<button data-comparison-filter="${key}" aria-pressed="${comparisonFilter===key}">${name} <span>${count}</span></button>`).join('')}</div></div><p class="field-result-count" role="status">Showing ${visible.length} of ${fields.length} fields${query?' matching your search':''}.</p>`:''}
    <div class="comparison-fields">${visible.map(({f,i})=>{
      const modelField=r.new_schema?.[f.field],kind=verdictKind(f);
      return `<article class="comparison-field field-${kind}"><header><h3>${escapeHtml(fieldName(f.field))}</h3><span class="verdict verdict-${kind}">${escapeHtml(verdictLabel(f))}</span></header>
        <div class="answer-pair"><div class="model-answer"><span class="answer-label">Model answer</span><p>${escapeHtml(displayValue(f.predicted))}</p><div class="field-sources">${sourceLinks(modelField?.evidence,f.field,i,'model')}</div></div><div class="human-answer"><span class="answer-label">Human label</span><p>${escapeHtml(displayValue(f.human))}</p></div></div>
        <details class="comparison-reason"><summary>Justification & evidence${f.evidence_verified===false?' · quote needs checking':''}</summary>${modelField?.reason?`<p><b>Model justification</b> ${escapeHtml(modelField.reason)}</p>`:''}<p><b>Comparison explanation</b> ${escapeHtml(f.explanation||'No saved explanation is available for this historical field.')}</p><div class="field-sources">${sourceLinks(f.evidence,f.field,i,'comparison')}</div>${f.confidence?`<p class="hint">Judge confidence: ${escapeHtml(f.confidence)} · Source support: ${escapeHtml(f.source_support||'unclear')}</p>`:''}</details>
      </article>`;
    }).join('')||`<div class="comparison-empty"><h3>${fields.length?(query?'No fields match your search.':comparisonFilter==='attention'?'No fields need attention.':'No fields match this filter.'):'No saved comparison is available.'}</h3><p>${fields.length?'Read every model answer and human label in All fields.':escapeHtml(r.judge?.error||'You can still inspect the model output, its justification and source evidence.')}</p><button class="secondary-button" id="comparisonEmptyAction">${fields.length?'Show all fields':'View model output'}</button></div>`}</div>
  </div>`;
  $('tableContent').querySelectorAll('[data-comparison-filter]').forEach(el=>el.onclick=()=>{comparisonFilter=el.dataset.comparisonFilter;renderHumanComparison();$('tableContent').querySelector(`[data-comparison-filter="${comparisonFilter}"]`).focus();});
  if($('comparisonEmptyAction'))$('comparisonEmptyAction').onclick=()=>{if(fields.length){comparisonFilter='all';$('search').value='';renderHumanComparison();$('tableContent').querySelector('[data-comparison-filter="all"]').focus();}else{activeTab=r.full_schema?'new_schema':'overview';renderTab();$('result-tab-'+activeTab).focus();}};
  $('tableContent').querySelectorAll('[data-comparison-source]').forEach(el=>el.onclick=()=>{const f=fields[Number(el.dataset.comparisonSource)];showScoringSource({...f.evidence[Number(el.dataset.sourceIndex)],name:fieldName(f.field),verified:f.evidence_verified});});
  $('tableContent').querySelectorAll('[data-model-source]').forEach(el=>el.onclick=()=>{const f=fields[Number(el.dataset.modelSource)];showScoringSource({...r.new_schema[f.field].evidence[Number(el.dataset.sourceIndex)],name:fieldName(f.field)});});
}
