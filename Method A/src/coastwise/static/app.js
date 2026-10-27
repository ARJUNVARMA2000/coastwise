const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = value => value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
let settings, uploadId, runId, currentResult, activeTab = 'observations', busy = false, modelCatalog = [], expandedModels = false;
let selectedWorkflow = 'scoring';
const workflowDrafts = {};
const methodDescriptions = {
  a: 'One extraction per native-text chunk. A clear baseline.',
  b0: 'Tables, form fields, and optional OCR. One extraction per chunk.',
  b: 'The same layout input, with bounded source checks and revisions.'
};
async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {...options.headers, 'X-Coastwise-Request':'1'}});
  if (!response.ok) {
    let detail; try { detail = (await response.json()).detail; } catch { detail = `Request failed (${response.status})`; }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.json();
}
function error(message) { $('formError').textContent = message; $('formError').hidden = !message; }
function setBusy(value) { busy = value; $('workflow').disabled=value; $('preview').disabled = value || !uploadId; $('extract').disabled = value || !uploadId; $('newRun').disabled = value; $('newAssessment').disabled=value; $('removeFile').disabled = value; $('fileInput').disabled = value; }
function clearResults() { currentResult = null; $('results').hidden = true; $('reportNavigation').hidden=true; $('assessmentSummary').hidden=true; $('usageSummary').hidden=true; $('failurePanel').hidden = true; $('emptyState').hidden = false; $('progressPanel').hidden = true; }
async function upload(file) {
  if (!file || busy) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) return error('Choose a PDF file.');
  error(''); setBusy(true); $('fileMeta').textContent = 'Uploading…';
  try {
    const form = new FormData(); form.append('file', file);
    const data = await api('/api/uploads', {method:'POST',body:form});
    uploadId = data.id; runId=null; history.replaceState(null,'',location.pathname); clearResults();
    $('filename').textContent = data.filename; $('fileMeta').textContent = `${data.pages} PDF pages · ${(data.size/1024/1024).toFixed(1)} MB`;
    $('fileCard').hidden = false; $('dropzone').hidden = true;
    $('workspaceTitle').textContent = 'A report worth a closer look.';
  } catch (e) { error(e.message); } finally { setBusy(false); }
}
function config() {
  if(selectedWorkflow==='scoring') return {workflow:'scoring', full_schema:$('fullSchema').checked, model:$('model').value,
    provider:$('provider').value.trim(),system_prompt:$('systemPrompt').value,pages:$('pages').value,
    chunk_chars:Number($('chunkChars').value),max_usd:Number($('maxUsd').value),
    max_output_tokens:Number($('maxTokens').value),max_requests:Number($('maxRequests').value)};
  return {method: document.querySelector('input[name=method]:checked').value, model:$('model').value.trim(),
    provider:$('provider').value.trim(), system_prompt:$('systemPrompt').value, pages:$('pages').value,
    max_usd:Number($('maxUsd').value), max_output_tokens:Number($('maxTokens').value),
    max_requests:Number($('maxRequests').value), max_rounds:Number($('maxRounds').value), ocr:$('ocr').checked};
}
async function start(preview) {
  if(bulkUploads.length>1)return startBatch(preview);
  if (!uploadId || busy) return;
  error('');
  if (!preview && !settings.has_server_key && !$('apiKey').value.trim()) {
    if (settings.hosted) return error('The workspace API key has not been configured yet. Ask the workspace owner to add the OpenRouter key in the hosting settings.');
    document.querySelector('.settings-details').open = true; $('apiKey').focus();
    return error('Add your OpenRouter API key in Connection & run limits, or use the free source preview.');
  }
  if (!preview && !$('model').value.trim()) return error('Select or enter an OpenRouter model ID.');
  const form = new FormData(); form.append('config',JSON.stringify(config())); form.append('preview',preview);
  form.append('api_key',$('apiKey').value);
  setBusy(true); clearResults(); $('emptyState').hidden = true; $('progressPanel').hidden = false;
  $('progressText').textContent = preview ? 'Preparing source preview…' : selectedWorkflow==='scoring' ? 'Preparing rubric assessment…' : 'Preparing extraction…';
  $('progressCount').textContent = '';
  try {
    const job = await api(`/api/runs/${uploadId}`,{method:'POST',body:form}); runId = job.id;
    history.replaceState(null,'',`?run=${encodeURIComponent(runId)}`);
    await poll();
  } catch (e) { error(e.message); $('progressPanel').hidden = true; $('emptyState').hidden = false; setBusy(false); }
}
async function poll(expectedId=runId) {
  try {
    const job = await api(`/api/runs/${expectedId}`);
    if(expectedId!==runId)return;
    $('progressText').textContent = job.stage;
    $('progressCount').textContent = job.total ? `${job.done ?? 0}/${job.total}` : '';
    if (['queued','running'].includes(job.status)) return setTimeout(()=>poll(expectedId),1800);
    $('progressPanel').hidden = true; setBusy(false);
    if (['failed','skipped','unavailable'].includes(job.status)) {
      $('failurePanel').hidden = false; $('failureMessage').textContent = job.stage; return;
    }
    const loaded = await api(`/api/runs/${expectedId}/result`);
    if(expectedId!==runId)return;
    currentResult=loaded;
    if(settings.bulk_available&&!allLibraryRows.some(r=>r.id===expectedId))await ensureReportIndex(true);
    currentReportRow=allLibraryRows.find(r=>r.id===expectedId)||null;
    if(libraryVisible)await refreshLibrary();else renderResult(job);
  } catch(e) { error(e.message); setBusy(false); $('progressPanel').hidden = true; }
}
function renderResult(job) {
  setWorkspaceView('assessment');
  if(currentResult.workflow==='scoring') return renderScoringResult(job);
  renderReportNavigation();
  $('results').hidden = false;
  $('workspaceTitle').textContent = job.filename;
  const r = currentResult;
  $('resultBanner').hidden=false;
  const notes = r.issues.length + r.failures.length;
  const preview = r.preview_only;
  $('resultBanner').className = `result-banner${notes ? ' warning' : ''}`;
  $('resultBanner').textContent = preview ? 'Source preview only. No model was called and no extracted records were generated.' :
    notes ? `Extraction finished with ${notes} item${notes === 1 ? '' : 's'} to review. Check source evidence before using the records.` :
    'Extraction finished. Source checks passed; review the records for completeness and meaning.';
  const costs = r.requests.filter(x => x.usage?.cost != null).map(x => Number(x.usage.cost));
  const cost = r.requests.length && costs.length === r.requests.length ? `$${costs.reduce((a,b)=>a+b,0).toFixed(4)}` : preview ? '$0.00' : 'Cost unavailable';
  const methodName = {a:'A · Raw text',b0:'B · Layout',b:'B+ · Verify'}[r.config.method];
  $('metrics').innerHTML = `<span>${escapeHtml(methodName)}</span><span>${r.inventory.length} pages</span><span>${r.runtime_seconds}s</span><span>${cost}</span>`;
  $('assessmentSummary').hidden=true; $('usageSummary').hidden=true;
  $('contractBadge').textContent='Provisional extraction schema';
  $('downloadTable').textContent='CSV ↓';
  setResultTabs([['observations','Observations',r.tables.observations.length],['structures','Structures',r.tables.structures.length],['reports','Reports',r.tables.reports.length],['evidence','Source evidence'],['diagnostics','Review notes',notes]]);
  const path = `/api/runs/${runId}/download/`;
  $('downloadZip').href = path+'outputs.zip'; $('downloadJson').href = path+'result.json';
  $('runId').textContent = `RUN ${runId.slice(0,8)} · ${r.schema_version}`;
  $('search').value = ''; $('evidencePanel').classList.remove('open');
  activeTab = preview ? 'evidence' : 'observations'; renderTab();
}
function factValue(fact) { return fact?.status === 'reported' ? fact.value : ''; }
function renderTab() {
  if (!currentResult) return;
  if(currentResult.workflow==='scoring') return renderScoringTab();
  syncResultTabs();
  document.querySelector('.table-actions').hidden=false;
  const query = $('search').value.toLowerCase();
  const content = $('tableContent');
  $('downloadTable').hidden = ['evidence','diagnostics'].includes(activeTab);
  $('downloadTable').href = `/api/runs/${runId}/download/${activeTab}.csv`;
  if (activeTab === 'diagnostics') {
    const r = currentResult;
    const notes = [...r.failures.map(x=>({title:`Chunk ${x.chunk+1} failed`,message:x.message})),
      ...r.issues.map(x=>({title:label(x.code),message:`${x.path ? x.path+': ' : ''}${x.message}`})),
      ...r.warnings.map(message=>({title:'Review guidance',message})),
      ...r.inventory.filter(x=>x.needs_ocr).map(x=>({title:`PDF page ${x.page}`,message:'Limited native text or a substantial scanned image. Check OCR coverage.'}))];
    content.innerHTML = notes.filter(x=>JSON.stringify(x).toLowerCase().includes(query)).map(x=>`<div class="note-card"><strong>${escapeHtml(x.title)}</strong>${escapeHtml(x.message)}</div>`).join('') || '<div class="empty-table">No matching review notes.</div>';
    return;
  }
  if (activeTab === 'evidence') {
    const items = Object.values(currentResult.evidence).filter(x=>JSON.stringify(x).toLowerCase().includes(query));
    content.innerHTML = `<div class="record-list">${items.map((x,i)=>`<article class="record-card" tabindex="0" data-evidence="${escapeHtml(x.ref)}"><div class="record-number">PDF PAGE ${x.page} · ${escapeHtml(label(x.kind))}</div><h3>${escapeHtml(x.text.slice(0,100) || 'No text found on this page')}</h3><p>${escapeHtml(x.text.slice(100,260))}</p><div class="record-tags"><span>${escapeHtml(x.ref)}</span></div></article>`).join('')}</div>`;
    content.querySelectorAll('[data-evidence]').forEach(el=>bindSelect(el,()=>showEvidence(el.dataset.evidence)));
    return;
  }
  const rows = currentResult.tables[activeTab];
  const visible = rows.map((row,index)=>({row,index})).filter(x=>JSON.stringify(x.row).toLowerCase().includes(query));
  if (!visible.length) {
    content.innerHTML = `<div class="empty-table">${currentResult.preview_only ? 'This is a source preview. Run an extraction to create records.' : 'No matching records. Check the source evidence and review notes for missing coverage.'}</div>`; return;
  }
  content.innerHTML = `<div class="record-list">${visible.map(({row,index})=>{
    const f = row.fields;
    const title = factValue(f.finding) || factValue(f.structure_name) || factValue(f.report_title) || `${label(activeTab)} record`;
    const description = factValue(f.component) || factValue(f.inspection_type) || factValue(f.location) || '';
    const pages = [...new Set(Object.values(f).flatMap(fact=>fact.evidence.map(s=>currentResult.evidence[s.ref]?.page)).filter(Boolean))];
    const tags = [factValue(f.rating_raw)||factValue(f.overall_rating_raw), factValue(f.material), pages.length ? `PDF ${pages.join(', ')}`:'Evidence missing'].filter(Boolean);
    return `<article class="record-card" tabindex="0" data-index="${index}"><div class="record-number">${escapeHtml(row.record_id)}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p><div class="record-tags">${tags.map(x=>`<span>${escapeHtml(x)}</span>`).join('')}</div></article>`;
  }).join('')}</div>`;
  content.querySelectorAll('[data-index]').forEach(el=>bindSelect(el,()=>showRecord(rows[Number(el.dataset.index)])));
}
function bindSelect(element, callback) { element.addEventListener('click',callback); element.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();callback();}}); }
function showEvidence(ref, quote) {
  const source = currentResult.evidence[ref]; if (!source) return;
  $('evidencePanel').classList.add('open');
  $('evidenceContent').innerHTML = `<div class="record-number">PDF PAGE ${source.page} · ${escapeHtml(label(source.kind))}</div><h4>${escapeHtml(ref)}</h4><blockquote>${escapeHtml(quote || source.text.slice(0,3500) || 'This page has no native text.')}</blockquote><img src="/api/runs/${runId}/page/${source.page}" alt="Source PDF page ${source.page}"><p class="hint">${source.bbox ? 'Source bounds: '+source.bbox.map(x=>Number(x).toFixed(1)).join(', ')+' PDF points.' : ''}</p>`;
  revealSourceEvidence();
}
function showRecord(row) {
  $('evidencePanel').classList.add('open');
  $('evidenceContent').innerHTML = `<h4>${escapeHtml(row.record_id)}</h4>` + Object.entries(row.fields).map(([name,fact])=>
    `<div class="evidence-fact"><b>${escapeHtml(label(name))}</b>${escapeHtml(fact.status === 'reported' ? fact.value : fact.status)}${fact.evidence.map((s,index)=>`<br><button data-field="${escapeHtml(name)}" data-source="${index}">${escapeHtml(s.ref)} ↗</button>`).join('')}</div>`).join('');
  $('evidenceContent').querySelectorAll('[data-source]').forEach(el=>el.onclick=()=>{const citation=row.fields[el.dataset.field].evidence[Number(el.dataset.source)];showEvidence(citation.ref,citation.quote);});
  revealSourceEvidence();
}
async function refreshModels() {
  $('refreshModels').disabled = true;
  try {
    const previous = $('model').value || settings.default_model;
    modelCatalog = await api(`/api/models?expanded=${expandedModels}`);
    $('model').innerHTML = modelCatalog.map(m=>`<option value="${escapeHtml(m.id)}">${escapeHtml(m.vendor)} · ${escapeHtml(m.name)}${m.supports_tools?'':' (A/B only)'}</option>`).join('');
    if(modelCatalog.some(m=>m.id===previous)) $('model').value=previous;
    $('modelHint').textContent = expandedModels ? `${modelCatalog.length} supported models. The same output checks apply.` : 'Five featured models across OpenAI, Anthropic, Google, and DeepSeek.';
    $('moreModels').textContent=expandedModels?'Show featured models':'More models…';
  }
  catch(e){$('modelHint').textContent=e.message;}
  finally{$('refreshModels').disabled=false;}
}
$('fileInput').onchange = e=>uploadFiles(e.target.files);
['dragenter','dragover'].forEach(event=>$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('dragging');}));
['dragleave','drop'].forEach(event=>$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');if(event==='drop')uploadFiles(e.dataTransfer.files);}));
function clear(){if(busy)return;savedOpenSequence++;clearBulkSelection();hideLibrary();uploadId=null;runId=null;currentReportRow=null;history.replaceState(null,'','?new=1');$('fileInput').value='';$('fileCard').hidden=true;$('dropzone').hidden=false;clearResults();setWorkspaceView('new');setBusy(false);updateBulkButton();error('');$('workspaceTitle').textContent='New assessment';}
$('newRun').onclick=clear; $('removeFile').onclick=clear;
$('newAssessment').onclick=clear;
$('preview').onclick=()=>start(true); $('extract').onclick=()=>start(false);
$('refreshModels').onclick=refreshModels;
$('moreModels').onclick=()=>{expandedModels=!expandedModels;refreshModels();};
$('resetPrompt').onclick=()=>{$('systemPrompt').value=selectedWorkflow==='scoring'?settings.scoring_prompt:settings.default_prompt;$('promptBadge').textContent='Default';};
$('systemPrompt').oninput=()=>{$('promptBadge').textContent=$('systemPrompt').value===(selectedWorkflow==='scoring'?settings.scoring_prompt:settings.default_prompt)?'Default':'Edited';};
$('fullSchema').onchange=()=>{delete workflowDrafts.scoring;updateWorkflow(false);};
$('workflow').onchange=()=>{updateWorkflow();updateBulkButton();};
document.querySelectorAll('input[name=method]').forEach(el=>el.onchange=()=>{$('methodDescription').textContent=methodDescriptions[el.value];});
document.querySelectorAll('[data-tab]').forEach(el=>el.onclick=()=>{activeTab=el.dataset.tab;renderTab();});
$('search').oninput=renderTab; $('closeEvidence').onclick=closeSourceEvidence;
(async()=>{try{settings=await api('/api/settings');$('provider').value=settings.default_provider;$('rubricText').textContent=settings.scoring_rubric;updateWorkflow(false);if(settings.has_server_key)$('keyHint').textContent='A server API key is configured.';if(settings.hosted){$('browserKey').hidden=true;$('workspaceLocation').textContent='Hosted workspace';$('privacyNote').textContent='Files and outputs are saved in this password-protected workspace. Processing sends selected report text through OpenRouter.';$('dropzone').querySelector('span:last-of-type').textContent=`or drop PDFs here · up to ${settings.max_upload_mb} MB each`;}const catalogReady=refreshModels();const params=new URLSearchParams(location.search),saved=params.get('run');libraryBatch=params.get('batch')||'';if(saved)await openSavedRun(saved,params.get('tab'));else{setBusy(false);if(params.has('new'))setWorkspaceView('new');}await initializeLibrary();await catalogReady;}catch(e){setWorkspaceView('new');error(e.message);}})();

let savedOpenSequence=0;
async function openSavedRun(saved,tab=null){
  const sequence=++savedOpenSequence;
  hideLibrary();clearBulkSelection();clearResults();error('');
  runId=saved;currentReportRow=null;
  setWorkspaceView('assessment');
  $('emptyState').hidden=true;$('progressPanel').hidden=false;$('progressText').textContent='Opening saved report…';
  $('workspaceTitle').textContent='Opening saved report…';
  try{
  const job=await api(`/api/runs/${encodeURIComponent(saved)}`);
  if(sequence!==savedOpenSequence)return;
  if(job.id&&job.id!==saved)return openSavedRun(job.id,tab);
  await ensureReportIndex();
  if(libraryBatch&&!libraryRows.length){const data=await api('/api/library?batch_id='+encodeURIComponent(libraryBatch));libraryRows=data.rows;}
  if(sequence!==savedOpenSequence)return;
  currentReportRow=allLibraryRows.find(r=>r.id===saved)||libraryRows.find(r=>r.id===saved)||null;
  runId=saved;uploadId=saved;history.replaceState(null,'',reportUrl(saved));
  $('filename').textContent=job.filename;$('fileMeta').textContent=`${job.pages} PDF pages · saved run`;
  $('fileCard').hidden=false;$('dropzone').hidden=true;$('workflow').value=job.workflow||'extraction';updateWorkflow();
  clearResults();$('emptyState').hidden=true;$('progressPanel').hidden=!['running','queued'].includes(job.status);
  setBusy(['running','queued'].includes(job.status));await poll();
  if(sequence!==savedOpenSequence)return;
  if(tab&&currentResult&&[...$('resultTabs').querySelectorAll('[data-tab]')].some(el=>el.dataset.tab===tab)){activeTab=tab;renderTab();}
  window.scrollTo(0,0);
  }catch(e){if(sequence!==savedOpenSequence)return;clearResults();$('emptyState').hidden=true;$('failurePanel').hidden=false;$('failureMessage').textContent=e.message;setBusy(false);}
}
