/* Multiple source uploads and the persistent report ledger. No credentials enter storage. */
let bulkUploads=[], libraryRows=[], libraryVisible=false, libraryBatch='', libraryTimer=null;
const ledgerNumber=value=>value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:3});
const ledgerCost=value=>value==null?'—':`$${Number(value).toFixed(5)}`;

function clearBulkSelection(){bulkUploads=[];$('uploadQueue').hidden=true;$('batchSettings').hidden=true;}
function updateQueue(){
  const multiple=bulkUploads.length>1;
  $('uploadQueue').hidden=!multiple;$('batchSettings').hidden=!multiple;
  $('fileCard').hidden=multiple||!bulkUploads.length;
  $('dropzone').hidden=bulkUploads.length>0;
  uploadId=bulkUploads.find(x=>x.id)?.id||null;
  $('queueCount').textContent=String(bulkUploads.length);
  $('queueFiles').innerHTML=bulkUploads.map((x,i)=>`<div class="queue-file"><div><strong>${escapeHtml(x.filename)}</strong><small>${escapeHtml(x.error||`${x.pages} pages · ready`)}</small></div><button class="icon-button" aria-label="Remove ${escapeHtml(x.filename)}" data-remove-upload="${i}" ${busy?'disabled':''}>×</button></div>`).join('');
  $('queueFiles').querySelectorAll('[data-remove-upload]').forEach(el=>el.onclick=()=>{bulkUploads.splice(Number(el.dataset.removeUpload),1);if(!bulkUploads.length)return clear();updateQueue();setBusy(false);});
  if(bulkUploads.length===1){$('filename').textContent=bulkUploads[0].filename;$('fileMeta').textContent=bulkUploads[0].error||`${bulkUploads[0].pages} PDF pages · ready`;}
  updateBulkButton();
}
function updateBulkButton(){
  if(!settings)return;
  const count=bulkUploads.length;
  $('extract').innerHTML=count>1?`Score ${count} reports <span>↗</span>`:selectedWorkflow==='scoring'?'Score report <span>↗</span>':'Extract report <span>↗</span>';
  $('preview').textContent=count>1?'Preview all · no API cost':'Preview source · no API cost';
}
async function uploadFiles(files){
  const selected=Array.from(files||[]);
  if(!selected.length||busy)return;
  if(selected.length>1&&!settings.bulk_available)return error('Bulk scoring is unavailable on this host.');
  if(selected.length>200)return error('Choose up to 200 PDFs in one batch.');
  if(selected.some(f=>!f.name.toLowerCase().endsWith('.pdf')))return error('Every selected file must be a PDF.');
  hideLibrary();clearResults();clearBulkSelection();setWorkspaceView('new');error('');setBusy(true);
  $('uploadQueue').hidden=false;$('queueHeading').textContent='Uploading reports';
  try{
    for(let i=0;i<selected.length;i++){
      const file=selected[i];$('queueProgress').textContent=`Uploading ${i+1} of ${selected.length} · ${file.name}`;
      try{
        const form=new FormData();form.append('file',file);
        const data=await api('/api/uploads',{method:'POST',body:form});bulkUploads.push(data);
      }catch(e){bulkUploads.push({filename:file.name,error:e.message});}
      $('queueFiles').innerHTML=bulkUploads.map(x=>`<div class="queue-file"><div><strong>${escapeHtml(x.filename)}</strong><small>${escapeHtml(x.error||`${x.pages} pages · uploaded`)}</small></div></div>`).join('');
    }
    runId=null;history.replaceState(null,'',location.pathname);$('workspaceTitle').textContent='Reports ready for assessment.';
  }finally{
    setBusy(false);$('queueHeading').textContent='Reports to score';$('queueProgress').textContent='The same model, system prompt and page selection apply to every report.';updateQueue();setBusy(false);
    if(bulkUploads.some(x=>x.error))error('Some PDFs could not be uploaded. Remove those files before starting the batch.');
  }
}
async function startBatch(preview){
  if(busy)return;
  if(selectedWorkflow!=='scoring')return error('Choose Method A · Rubric scoring to process multiple reports.');
  if(bulkUploads.some(x=>!x.id))return error('Remove files that could not be uploaded before starting.');
  if(!preview&&!settings.has_server_key&&!$('apiKey').value.trim())return error('Configure the workspace OpenRouter key before scoring.');
  error('');setBusy(true);
  const form=new FormData();
  form.append('upload_ids',JSON.stringify(bulkUploads.map(x=>x.id)));form.append('config',JSON.stringify(config()));
  form.append('max_batch_usd',$('batchMaxUsd').value);form.append('name',$('batchName').value||'Report batch');
  form.append('request_key',crypto.randomUUID());form.append('preview',String(preview));form.append('api_key',$('apiKey').value);
  try{const batch=await api('/api/batches',{method:'POST',body:form});libraryBatch=batch.id;history.replaceState(null,'',`?batch=${encodeURIComponent(batch.id)}`);await showLibrary();}
  catch(e){error(e.message);}finally{setBusy(false);updateQueue();}
}
function hideLibrary(){
  libraryVisible=false;$('libraryPanel').hidden=true;
  $('results').hidden=!currentResult;$('emptyState').hidden=Boolean(currentResult);
  $('workspaceTitle').textContent=currentResult?$('filename').textContent:'Ready when you are.';
  $('contractBadge').textContent=selectedWorkflow==='scoring'?'Team assessment format':'Provisional extraction schema';
}
async function showLibrary(){
  savedOpenSequence++;
  setWorkspaceView('reports');
  libraryVisible=true;$('libraryPanel').hidden=false;
  ['results','emptyState','progressPanel','failurePanel'].forEach(id=>$(id).hidden=true);
  $('workspaceTitle').textContent='Explore processed reports';$('contractBadge').textContent='Saved assessments';
  await refreshLibrary();
  window.scrollTo(0,0);
}
async function refreshLibrary(){
  clearTimeout(libraryTimer);$('libraryError').hidden=true;
  try{
    const index=await ensureReportIndex(true);
    if(!libraryBatch&&!location.search){const preferred=index.batches.find(b=>/human-labeled reports/i.test(b.name))||index.batches[0];if(preferred){libraryBatch=preferred.id;history.replaceState(null,'','?batch='+encodeURIComponent(libraryBatch));}}
    const data=libraryBatch?await api('/api/library?batch_id='+encodeURIComponent(libraryBatch)):index;
    if(libraryBatch&&data.batch?.id&&data.batch.id!==libraryBatch){libraryBatch=data.batch.id;history.replaceState(null,'','?batch='+encodeURIComponent(libraryBatch));}
    libraryRows=data.rows;$('libraryCount').textContent=String(groupReports(allLibraryRows).length);
    $('batchFilter').innerHTML='<option value="">All reports</option>'+data.batches.map(b=>`<option value="${escapeHtml(b.id)}">${escapeHtml(b.name)}</option>`).join('');
    $('batchFilter').value=libraryBatch;
    const t=data.totals,groups=groupReports(libraryRows),selected=groups.map(g=>g.row);
    $('libraryTotals').innerHTML=[['Reports',groups.length],['Model outputs ready',selected.filter(r=>r.status==='complete'&&!r.preview_only&&!r.has_failures).length],['Extracted fields',selected.reduce((n,r)=>n+Object.keys(r.new_schema_values||{}).length,0)]].map(([name,value])=>`<div><strong>${value}</strong><span>${name}</span></div>`).join('');
    $('libraryNote').textContent=`One current assessment per PDF. Open a report for its overview, model output and source evidence.${t.active?' '+t.active+' jobs in progress.':''}`;
    const query=libraryBatch?'?batch_id='+encodeURIComponent(libraryBatch):'';
    [['summaryCsv','summary.csv'],['comparisonCsv','comparison.csv'],['newSchemaCsv','new_schema.csv'],['judgeCsv','judge.csv'],['summaryJson','summary.json'],['allOutputs','outputs.zip']].forEach(([id,file])=>$(id).href=`/api/library/download/${file}${query}`);
    renderLibrary();
    if(libraryVisible&&t.active)libraryTimer=setTimeout(refreshLibrary,2000);
  }catch(e){$('libraryError').textContent=e.message;$('libraryError').hidden=false;}
}
function renderLibrary(){
  const query=$('librarySearch').value.toLowerCase();
  const allGroups=groupReports(libraryRows);
  const groups=allGroups.filter(g=>(libraryFilter==='all'||(libraryFilter==='attention'?reportHasAttention(g.row):!g.row.human_comparison?.reference_id))&&g.versions.map(r=>[r.filename,reportTitle(r),r.new_schema_values?.['Inspection date'],r.new_schema_values?.['Structure ID/name'],assessmentName(r)].join(' ')).join(' ').toLowerCase().includes(query));
  $('libraryResultCount').textContent=`Showing ${groups.length} of ${allGroups.length} reports.`;
  $('libraryFilters').querySelectorAll('button').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.libraryFilter===libraryFilter)));
  $('libraryFilterSummary').textContent=libraryFilter==='all'?'':libraryFilter==='attention'?'Review checks':'No human labels';
  $('libraryTable').innerHTML=groups.length?`<table class="report-table"><caption class="sr-only">Saved reports, available model outputs, and provisional model severity</caption><thead><tr><th scope="col">Report</th><th scope="col">Saved output</th><th scope="col">Model severity</th><th scope="col"><span class="sr-only">Open report</span></th></tr></thead><tbody>${groups.map(({row:r})=>`<tr><td data-column="Report"><a class="report-name" href="${reportUrl(r.id)}" data-open-run="${escapeHtml(r.id)}">${escapeHtml(reportTitle(r))}</a><small>${escapeHtml(assessmentName(r))}</small>${r.status!=='complete'?`<span class="run-status">${escapeHtml(r.status)}</span>`:''}</td><td data-column="Saved output"><span class="output-caption">${escapeHtml(savedOutputLabel(r))}</span><small>${r.preview_only?'Source pages available':r.human_comparison?.reference_id?'Source evidence · Human labels available':'Source evidence'}</small></td><td data-column="Model severity">${severityBadge(r.score)}<small>${r.preview_only?'Source preview':'Provisional assessment'}</small></td><td class="report-open-cell"><a class="report-open" href="${reportUrl(r.id)}" data-open-run="${escapeHtml(r.id)}" aria-label="Open ${escapeHtml(reportTitle(r))}">Open report <span aria-hidden="true">→</span></a></td></tr>`).join('')}</tbody></table><p class="library-scale">Model severity: <b>1 Good → 4 Serious / critical</b> · Higher means worse. Assessments are provisional; source evidence and human comparisons are available within each report.</p>`:'<div class="comparison-empty"><h3>No reports match this view.</h3><p>Try another collection, search or filter. New assessment lets you upload a PDF.</p><button id="resetLibraryFilters" class="secondary-button">Clear search and filters</button></div>';
  $('libraryTable').querySelectorAll('[data-open-run]').forEach(el=>el.onclick=e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();openSavedRun(el.dataset.openRun);});
  if($('resetLibraryFilters'))$('resetLibraryFilters').onclick=()=>{libraryFilter='all';$('librarySearch').value='';renderLibrary();$('librarySearch').focus();};
}
async function initializeLibrary(){
  if(!settings.bulk_available){$('openLibrary').hidden=true;$('fileInput').multiple=false;setWorkspaceView(currentResult?'assessment':'new');return;}
  $('openLibrary').onclick=()=>{history.replaceState(null,'',libraryBatch?'?batch='+encodeURIComponent(libraryBatch):'?library=1');showLibrary();};
  $('refreshLibrary').onclick=refreshLibrary;$('backToReport').onclick=()=>{if(runId)return openSavedRun(runId);hideLibrary();history.replaceState(null,'',location.pathname);};
  $('batchFilter').onchange=()=>{libraryBatch=$('batchFilter').value;history.replaceState(null,'',libraryBatch?'?batch='+encodeURIComponent(libraryBatch):'?library=1');refreshLibrary();};
  $('librarySearch').oninput=renderLibrary;
  $('libraryFilters').querySelectorAll('button').forEach(el=>el.onclick=()=>{libraryFilter=el.dataset.libraryFilter;renderLibrary();});
  const params=new URLSearchParams(location.search);libraryBatch=params.get('batch')||'';
  if(params.has('new')){await ensureReportIndex();$('workspaceTitle').textContent='New assessment';}
  if(!params.has('run')&&!params.has('new'))await showLibrary();
}
