/* Method A assessment views. Shared upload, job, model and source routes live in app.js. */
let evidenceTrigger=null;
function revealSourceEvidence(){
  if(!$('evidencePanel').contains(document.activeElement))evidenceTrigger=document.activeElement;
  $('evidencePanel').classList.add('open');
  $('closeEvidence').focus({preventScroll:true});
  $('evidencePanel').scrollIntoView({block:'nearest'});
}
function closeSourceEvidence(){
  $('evidencePanel').classList.remove('open');
  if(evidenceTrigger?.isConnected)evidenceTrigger.focus({preventScroll:true});
}
function updateWorkflow(saveDraft=true) {
  if(saveDraft) workflowDrafts[selectedWorkflow]={prompt:$('systemPrompt').value,tokens:$('maxTokens').value,usd:$('maxUsd').value,requests:$('maxRequests').value,chars:$('chunkChars').value};
  selectedWorkflow=$('workflow').value;
  const scoring=selectedWorkflow==='scoring', d=workflowDrafts[selectedWorkflow];
  $('fullSchemaControl').hidden=!scoring;
  const defaults=scoring&&$('fullSchema').checked?settings.full_schema_defaults:settings.scoring_defaults;
  $('methodControls').hidden=scoring; $('repairControl').hidden=scoring; $('ocrControl').hidden=scoring;
  $('scoringChunkControl').hidden=!scoring; $('rubricDetails').hidden=!scoring;
  $('maxTokens').max=scoring?'64000':'16000';
  $('maxTokens').value=d?.tokens||(scoring?defaults.max_output_tokens:4000);
  $('maxUsd').value=d?.usd||(scoring?defaults.max_usd:1);
  $('maxRequests').value=d?.requests||(scoring?defaults.max_requests:16);
  $('chunkChars').value=d?.chars||(scoring?defaults.chunk_chars||250000:250000);
  $('systemPrompt').value=d?.prompt??(scoring?settings.scoring_prompt:settings.default_prompt);
  $('promptBadge').textContent=$('systemPrompt').value===(scoring?settings.scoring_prompt:settings.default_prompt)?'Default':'Edited';
  $('extract').innerHTML=scoring?'Score report <span>↗</span>':'Extract report <span>↗</span>';
  $('workflowHint').textContent=scoring?($('fullSchema').checked?'Complete New_Schema extraction and rubric scoring, followed by an LLM comparison to a matched human review.':'One model response per native-text chunk, using the team’s scoring rubric.'):'Extract source facts with comparable A, B, and B+ methods.';
  if(!currentResult) $('contractBadge').textContent=scoring?'Team assessment format':'Provisional extraction schema';
  $('emptyDescription').textContent=scoring?'Upload a report to get its rubric score, component findings, source evidence, and review flags.':'Upload a report to extract source facts and review the evidence behind each value.';
  const items=scoring?[['Score','What the rubric indicates'],['Evidence','What the report states'],['Review','What needs a closer look']]:[['Structures','What was inspected'],['Reports','How it was inspected'],['Observations','What was found']];
  $('outputPreview').innerHTML=items.map(x=>`<div><span class="output-line"></span><b>${x[0]}</b><small>${x[1]}</small></div>`).join('');
}
function setResultTabs(tabs) {
  $('resultTabs').innerHTML=tabs.map(([key,name,count])=>`<button id="result-tab-${key}" role="tab" aria-controls="tableContent" aria-selected="false" tabindex="-1" data-tab="${key}">${escapeHtml(name)}${count==null?'':` <span>${count}</span>`}</button>`).join('');
  const buttons=[...$('resultTabs').querySelectorAll('[data-tab]')];
  buttons.forEach((el,i)=>{
    el.onclick=()=>{activeTab=el.dataset.tab;renderTab();};
    el.onkeydown=e=>{
      const index=e.key==='Home'?0:e.key==='End'?buttons.length-1:e.key==='ArrowRight'?(i+1)%buttons.length:e.key==='ArrowLeft'?(i+buttons.length-1)%buttons.length:null;
      if(index==null)return;e.preventDefault();activeTab=buttons[index].dataset.tab;renderTab();buttons[index].focus();
    };
  });
}
function syncResultTabs(){
  document.querySelectorAll('[data-tab]').forEach(el=>{const selected=el.dataset.tab===activeTab;el.setAttribute('aria-selected',String(selected));el.tabIndex=selected?0:-1;});
  $('tableContent').setAttribute('aria-labelledby','result-tab-'+activeTab);
}
function scoredChunks() { return currentResult.chunks.filter(x=>x.validation?.schema_valid); }
function scoredComponents() { return scoredChunks().flatMap(x=>x.assessment.components.map(c=>({chunk:x.chunk,component:c}))); }
function scoreSources() {
  return scoredChunks().flatMap(entry=>{
    const a=entry.assessment, out=[];
    function add(field,name,value,source) {
      if(value==='NR') return;
      out.push({chunk:entry.chunk,name,field,...source,verified:!entry.validation.unverified_citations.some(x=>x.field===field)});
    }
    a.evidence.forEach((e,i)=>add(`evidence[${i}]`,e.rubric_criterion,e.quote,e));
    a.components.forEach((c,i)=>{add(`components[${i}].material`,`${c.component} · material`,c.material,c.material_source);add(`components[${i}].observed_condition`,`${c.component} · condition`,c.observed_condition,c.condition_source);});
    ['construction_year','inspection_date','exposure_environment'].forEach(k=>add(k,label(k),a[k].value,a[k]));
    return out;
  });
}
function metricNumber(value) { return value==null?'Unavailable':Number(value).toLocaleString(); }
function metricCost(value) { return value==null?'Unavailable':`$${Number(value).toFixed(5)}`; }
function renderScoringResult(job) {
  const r=currentResult,m=r.metrics,a=r.assessment,preview=r.preview_only;
  setWorkspaceView('assessment');renderReportNavigation();
  $('results').hidden=false;$('workspaceTitle').textContent=reportTitle(job);
  $('contractBadge').textContent=preview?'Source preview':currentReportRow?assessmentName(currentReportRow):'Saved assessment';
  $('resultBanner').className='result-banner'+(!preview&&r.needs_review?' warning':'');
  $('resultBanner').hidden=!preview;
  $('resultBanner').textContent=preview?'Source preview · no model call or scoring cost.':'AI assessment · provisional · review the evidence and flagged findings before using the score.';
  $('assessmentSummary').hidden=false;
  $('assessmentSummary').innerHTML=preview?'<p class="preview-message">No score was generated. Select a source page below to inspect the PDF.</p>':`<div class="model-score"><span class="eyebrow">MODEL SEVERITY · PROVISIONAL</span>${severityBadge(a?.score)}<p class="scale-direction">1 Good → 4 Serious / critical · Higher is worse</p></div><div class="assessment-context"><details class="assessment-explanation"><summary>Model justification <span>${escapeHtml(a?.confidence||'Unknown')} confidence</span></summary><p>${escapeHtml(a?.rationale||'No overall rationale is available. Inspect the scoped findings and review flags.')}</p></details></div>`;
  $('usageSummary').hidden=true;
  $('metrics').innerHTML=`<span>${m.pages} of ${r.coverage.pdf_pages} PDF pages</span><span>${r.coverage.partial_report?'Selected pages only':'Native-text assessment'}</span>`;
  const path=`/api/runs/${runId}/download/`;
  $('downloadZip').href=path+'outputs.zip';$('downloadJson').href=path+'result.json';
  $('runId').textContent=`RUN ${runId.slice(0,8)} · ${r.schema_version}`;
  $('search').value='';$('evidencePanel').classList.remove('open');
  comparisonFilter='all';
  setResultTabs(preview?[['source_pages','Source pages',m.pages],['usage','Run details']]:[['overview','Overview'],['new_schema','Model output',Object.keys(r.new_schema||{}).length||undefined],['components','Findings & evidence',scoredComponents().length],['judge','Human comparison'],['diagnostics','Review flags'],['usage','Run details']]);
  activeTab=preview?'source_pages':'overview';renderTab();
}
function renderScoringTab() {
  syncResultTabs();
  const r=currentResult,m=r.metrics,content=$('tableContent'),q=$('search').value.toLowerCase();
  history.replaceState(null,'',reportUrl(runId,activeTab));
  document.querySelector('.table-actions').hidden=activeTab==='overview';
  const matches=x=>JSON.stringify(x).toLowerCase().includes(q);
  const downloads={new_schema:'new_schema.csv',judge:'judge.csv',components:'components.csv',score_evidence:'citations.csv',assessments:'assessments.csv',diagnostics:'review_flags.json',usage:'metrics.csv'};
  $('downloadTable').hidden=!downloads[activeTab];
  $('downloadTable').textContent=activeTab==='diagnostics'?'Flags JSON ↓':'CSV ↓';
  $('downloadTable').href=`/api/runs/${runId}/download/${downloads[activeTab]||'metrics.csv'}`;
  if(activeTab==='overview') {
    renderOverview();
  } else if(activeTab==='new_schema') {
    const fields=Object.entries(r.new_schema||{});
    if(!r.full_schema){content.innerHTML='<div class="empty-table">This assessment contains rubric findings only. Open Findings & evidence to inspect its saved output.</div>';return;}
    content.innerHTML=`<p class="field-intro">All extracted information, with source evidence. <b>NR</b> means not reported; <b>NA</b> means not applicable.</p><div class="record-list">${fields.filter(matches).map(([name,f])=>`<article class="record-card"><div class="record-number">${escapeHtml(f.status.replaceAll('_',' '))}</div><h3>${escapeHtml(fieldName(name))}</h3><p>${escapeHtml(f.value)}</p>${f.reason?`<p class="hint">${escapeHtml(f.reason)}</p>`:''}${(f.evidence||[]).map((e,i)=>`<button class="text-button" data-full-field="${escapeHtml(name)}" data-evidence="${i}">PDF ${e.page} ↗</button>`).join(' ')}</article>`).join('')}</div>`;
    content.querySelectorAll('[data-full-field]').forEach(el=>el.onclick=()=>showScoringSource({...r.new_schema[el.dataset.fullField].evidence[Number(el.dataset.evidence)],name:el.dataset.fullField}));
  } else if(activeTab==='judge') {
    renderHumanComparison();
  } else if(activeTab==='components') {
    const items=scoredComponents();
    content.innerHTML=`<div class="record-list">${items.map((x,i)=>({x,i})).filter(({x})=>matches(x)).map(({x,i})=>{const c=x.component;return `<article class="record-card" tabindex="0" data-component="${i}"><div class="record-number">CHUNK ${x.chunk} · ${escapeHtml(c.primary_or_secondary)}${c.segment?' · '+escapeHtml(c.segment):''}</div><h3>${escapeHtml(c.component)}</h3><p>${escapeHtml(c.observed_condition==='NR'?'Not reported or not inspected':c.observed_condition)}</p><div class="record-tags"><span>Score: ${c.component_score??'NR'}</span><span>${escapeHtml(label(c.damage_grade))}</span><span>${escapeHtml(c.extent)}</span><span>${escapeHtml(c.material)}</span></div></article>`;}).join('')}</div>`;
    content.querySelectorAll('[data-component]').forEach(el=>bindSelect(el,()=>showScoringComponent(items[Number(el.dataset.component)])));
    const sources=scoreSources();
    content.insertAdjacentHTML('beforeend',`<details class="all-citations"><summary>All rubric quotations (${sources.length})</summary>${sources.map((s,i)=>({s,i})).filter(({s})=>matches(s)).map(({s,i})=>`<article><b>${escapeHtml(s.name)}</b><blockquote>${escapeHtml(s.quote||'No quotation supplied')}</blockquote><button class="text-button" data-all-citation="${i}">PDF page ${escapeHtml(s.page??'unknown')} ↗${s.verified?'':' · quote needs checking'}</button></article>`).join('')}</details>`);
    content.querySelectorAll('[data-all-citation]').forEach(el=>el.onclick=()=>showScoringSource(sources[Number(el.dataset.allCitation)]));
  } else if(activeTab==='score_evidence') {
    const sources=scoreSources();
    content.innerHTML=`<div class="record-list">${sources.map((x,i)=>({x,i})).filter(({x})=>matches(x)).map(({x,i})=>`<article class="record-card" tabindex="0" data-citation="${i}"><div class="record-number">CHUNK ${x.chunk} · PDF PAGE ${escapeHtml(x.page??'MISSING')} · ${x.verified?'QUOTE MATCHED':'QUOTE NEEDS REVIEW'}</div><h3>${escapeHtml(x.name)}</h3><p class="source-quote">${escapeHtml(x.quote||'Source quote missing')}</p></article>`).join('')}</div>`;
    content.querySelectorAll('[data-citation]').forEach(el=>bindSelect(el,()=>showScoringSource(sources[Number(el.dataset.citation)])));
  } else if(activeTab==='assessments') {
    const chunks=r.chunks;
    content.innerHTML=`<div class="record-list">${chunks.filter(matches).map(x=>`<article class="record-card" tabindex="0" data-chunk="${x.chunk}"><div class="record-number">CHUNK ${x.chunk} · PDF ${escapeHtml(x.pages.join(', '))}</div><h3>${x.validation?.schema_valid?`AI severity ${x.assessment.score} — ${severityNames[x.assessment.score]}`:'No validated assessment'}</h3><p>${escapeHtml(x.validation?.schema_valid?x.assessment.rationale:x.failure||'The response did not match the assessment schema. Inspect the downloaded raw output.')}</p>${r.coverage.partial_report||r.metrics.chunks_total>1?'<p class="hint">This score applies to this input chunk. Report-level score is withheld.</p>':''}</article>`).join('')}</div>`;
    content.querySelectorAll('[data-chunk]').forEach(el=>bindSelect(el,()=>showScoringAssessment(chunks.find(x=>x.chunk===Number(el.dataset.chunk)))));
  } else if(activeTab==='diagnostics') {
    const notes=[...(r.review_flags||[]).map(x=>({title:`${label(x.code)}${x.path?' · '+x.path:''}${x.chunk?' · chunk '+x.chunk:''}`,message:x.message})),...scoredChunks().flatMap(x=>x.assessment.missing_information.map(message=>({title:`Missing information · chunk ${x.chunk}`,message}))),...r.warnings.map(message=>({title:'Review guidance',message}))];
    content.innerHTML=notes.filter(matches).map(x=>`<div class="note-card"><strong>${escapeHtml(x.title)}</strong>${escapeHtml(x.message)}</div>`).join('');
  } else if(activeTab==='source_pages') {
    content.innerHTML=`<div class="record-list">${r.coverage.selected_pages.filter(p=>matches({page:p})).map(p=>`<article class="record-card" tabindex="0" data-page="${p}"><div class="record-number">ABSOLUTE PDF PAGE ${p}</div><h3>${m.low_text_pages.includes(p)?'Limited native text — inspect the page image':'View source page'}</h3><p>Method A uses native text only. Photos and scanned marks are not interpreted.</p></article>`).join('')}</div>`;
    content.querySelectorAll('[data-page]').forEach(el=>bindSelect(el,()=>showScoringSource({page:Number(el.dataset.page),name:'Source preview',quote:'No model was called. This is the source PDF page.'})));
  } else if(activeTab==='usage') {
    const rows=[['Model requested',r.config.model],['Model returned',m.model_returned.join(', ')||'Not called'],['Provider returned',m.provider_returned.join(', ')||'Not called'],['Input tokens',r.preview_only?0:metricNumber(m.prompt_tokens)],['Output tokens',r.preview_only?0:metricNumber(m.completion_tokens)],['Total tokens',r.preview_only?0:metricNumber(m.total_tokens)],['Provider-reported cost',metricCost(m.billed_usd)],['Reserved budget',metricCost(m.budget_reserved_usd)],['Model runtime',`${m.model_runtime_seconds}s`],['Pipeline runtime',`${m.runtime_seconds}s`],['Requests',m.requests],['Generated',r.started_utc||'Not recorded'],['Input chunks',m.chunks_total],['Schema-valid outputs',`${m.schema_valid_chunks}/${m.chunks_attempted}`],['Quote matches',`${m.citations_verified}/${m.citations_total}`],['Native text characters',metricNumber(m.native_characters)],['Low-text pages',m.low_text_pages.join(', ')||'None'],['Selected pages',`${m.pages} / ${r.coverage.pdf_pages}`]];
    if(r.full_schema)rows.push(['Extraction tokens',metricNumber(m.extraction_total_tokens)],['Extraction cost',metricCost(m.extraction_billed_usd)],['Extraction runtime',m.extraction_runtime_seconds==null?'Unavailable':m.extraction_runtime_seconds+'s'],['New_Schema quote matches',`${m.new_schema_citations_verified??'—'} / ${m.new_schema_citations_total??'—'}`],['Judge status',r.judge?.status||'pending'],['Judge tokens',metricNumber(m.judge_total_tokens)],['Judge cost',metricCost(m.judge_billed_usd)],['Judge runtime',m.judge_runtime_seconds==null?'Unavailable':m.judge_runtime_seconds+'s']);
    content.innerHTML=`<div class="usage-detail">${rows.filter(matches).map(([name,value])=>`<div><b>${escapeHtml(name)}</b><span>${escapeHtml(value)}</span></div>`).join('')}<p class="hint">Unknown billing/usage remains unavailable. Quote checks establish text presence. Semantic accuracy and recall require reviewed reference answers.</p></div>`;
  }
  if(!content.textContent.trim()) content.innerHTML='<div class="empty-table">No matching results. Check review flags or adjust your search.</div>';
}
function showScoringSource(source) {
  const p=source.page,valid=Number.isInteger(p)&&p>=1&&p<=currentResult.coverage.pdf_pages;
  $('evidencePanel').classList.add('open');
  $('evidenceContent').innerHTML=`<div class="record-number">${valid?'ABSOLUTE PDF PAGE '+p:'SOURCE PAGE MISSING'}</div><h4>${escapeHtml(source.name||'Report evidence')}</h4><blockquote>${escapeHtml(source.quote||'No source quote was supplied.')}</blockquote>${source.verified===false?'<p class="error">This quote did not match the supplied native text. Check the page and raw response.</p>':''}${valid?`<img src="/api/runs/${runId}/page/${p}" alt="Source PDF page ${p}" loading="lazy">`:''}<p class="hint">Page numbers refer to the PDF file. The source image can include details the native-text baseline did not interpret.</p>`;
  revealSourceEvidence();
}
function showScoringComponent(item) {
  const c=item.component;
  $('evidencePanel').classList.add('open');
  const fields=[['Component',c.component],['Segment',c.segment||'None stated'],['Role',c.primary_or_secondary],['Material',c.material],['Observed condition',c.observed_condition],['Damage grade',c.damage_grade],['Extent',c.extent],['Component score',c.component_score??'NR']];
  $('evidenceContent').innerHTML=`<h4>${escapeHtml(c.component)}</h4>${fields.map(([key,value])=>`<div class="evidence-fact"><b>${escapeHtml(key)}</b>${escapeHtml(value)}</div>`).join('')}<div class="source-actions"><button class="text-button" data-part="material_source">Material source ↗</button><button class="text-button" data-part="condition_source">Condition source ↗</button></div>`;
  $('evidenceContent').querySelectorAll('[data-part]').forEach(el=>el.onclick=()=>showScoringSource({...c[el.dataset.part],name:c.component}));
  revealSourceEvidence();
}
function showScoringAssessment(chunk) {
  $('evidencePanel').classList.add('open');
  if(!chunk.validation?.schema_valid){$('evidenceContent').innerHTML='<h4>No validated assessment</h4><p class="muted">Download the full JSON or ZIP to inspect the raw response and validation errors.</p>';return;}
  const a=chunk.assessment;
  $('evidenceContent').innerHTML=`<h4>Chunk ${chunk.chunk} · severity ${a.score} — ${severityNames[a.score]}</h4><p class="muted">${escapeHtml(a.rationale)}</p>${['construction_year','inspection_date','exposure_environment'].map(k=>`<div class="evidence-fact"><b>${escapeHtml(label(k))}</b>${escapeHtml(a[k].value)}${a[k].page?`<br><button data-context="${k}">PDF ${a[k].page} ↗</button>`:''}</div>`).join('')}<h4>Segment scores</h4>${a.segment_scores.length?a.segment_scores.map(s=>`<div class="evidence-fact"><b>${escapeHtml(s.segment)}</b>${s.score} — ${escapeHtml(severityNames[s.score])}${s.secondary_only?' · Secondary only':''}</div>`).join(''):'<p class="muted">No separately rated segments returned.</p>'}`;
  $('evidenceContent').querySelectorAll('[data-context]').forEach(el=>el.onclick=()=>showScoringSource({...a[el.dataset.context],name:label(el.dataset.context)}));
  revealSourceEvidence();
}


