'use strict';
const $ = s => document.querySelector(s);
const state = {runs: [], tasks: [], run: null, task: null, selected: {A: null, B: null, C: null}, path: [], tab: 'dag'};
const svgNS = 'http://www.w3.org/2000/svg';
function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) n.setAttribute(key, val);
  if (text !== undefined) n.textContent = text;
  return n;
}
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(svgNS, tag);
  for (const [key, val] of Object.entries(attrs)) n.setAttribute(key, val);
  if (text !== undefined) n.textContent = text;
  return n;
}
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const value = await res.json();
  if (!res.ok) throw Error(value.error || `HTTP ${res.status}`);
  return value;
}
function error(e) { $('#verdict').replaceChildren(el('div', {class: 'banner'}, String(e.message || e))); }
function option(select, value, text) { select.append(el('option', {value}, text)); }
function name(id) { return state.run?.names?.[id] || String(id || '—').slice(0, 12); }
function syncSelections() {
  for (const k of ['A', 'B', 'C']) $('#sel' + k).textContent = state.selected[k] ? name(state.selected[k]) : '—';
  $('#verifybtn').disabled = !state.selected.A || !state.selected.B;
}
function draw() {
  const run = state.run;
  if (!run) return;
  const events = [...run.events].sort((a,b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  const lanes = [...new Set(events.map(e => e.service))];
  const width = Math.max(900, events.length * 56 + 170), height = Math.max(370, lanes.length * 115 + 70);
  const chart = svg('svg', {class: 'dag', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Recorded events and their dependency edges'});
  const defs = svg('defs'); const marker = svg('marker', {id: 'arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto'});
  marker.append(svg('path', {d:'M0 0L10 5L0 10z', fill:'currentColor'})); defs.append(marker); chart.append(defs);
  lanes.forEach((lane, i) => { const y = 35 + i * 115; chart.append(svg('rect', {x:0,y:y-20,width,height:110,fill:i%2?'var(--lane-b)':'var(--lane-a)'})); chart.append(svg('text',{x:12,y:y+5,class:'lane-label'},lane)); });
  const positions = new Map(events.map((e,i) => [e.event_id, {x:130 + i * 56, y:55 + lanes.indexOf(e.service) * 115}]));
  const hidden = new Set((state.task?.hidden_edges || []).map(e => e.parent_event_id + '/' + e.child_event_id));
  const hiddenNodes = new Set(state.task?.hidden_event_ids || []);
  const highlighted = new Set(state.path.slice(1).map((v,i) => state.path[i] + '/' + v));
  for (const d of run.dependencies) {
    const a = positions.get(d.parent_event_id), b = positions.get(d.child_event_id); if (!a || !b) continue;
    const key = d.parent_event_id + '/' + d.child_event_id;
    const line = svg('path', {d:`M ${a.x} ${a.y} C ${a.x+25} ${a.y}, ${b.x-25} ${b.y}, ${b.x} ${b.y}`,class:`edge t-${d.dependency_type}${hidden.has(key)?' hidden-ai':''}${highlighted.has(key)?' on':''}`,'marker-end':'url(#arrow)'});
    line.append(svg('title',{}, `${d.dependency_type}\n${d.evidence_reference || ''}`)); chart.append(line);
  }
  events.forEach(e => {
    const p=positions.get(e.event_id); const sel=Object.entries(state.selected).find(([,v])=>v===e.event_id)?.[0]?.toLowerCase();
    const g=svg('g',{class:`node s-${e.status || 'new'}${hiddenNodes.has(e.event_id)?' hidden-ai':''}${sel?' sel-'+sel:''}`,tabindex:0,role:'button','aria-label':`${name(e.event_id)} ${e.service} ${e.event_type}`});
    g.append(svg('circle',{cx:p.x,cy:p.y,r:7}));
    g.append(svg('text',{x:p.x+5,y:p.y+20,class:'lbl',transform:`rotate(35 ${p.x+5} ${p.y+20})`},(run.names?.[e.event_id] || e.event_type).slice(0,32)));
    g.append(svg('title',{},`${name(e.event_id)}\n${e.service}: ${e.event_type} (${e.status})\n${e.timestamp}\n${e.metadata?.message || ''}`));
    const select = ev => { if (ev.shiftKey) state.selected.C=e.event_id; else if (!state.selected.A || state.selected.B) {state.selected.A=e.event_id; state.selected.B=null;} else state.selected.B=e.event_id; state.path=[]; syncSelections(); draw(); };
    g.addEventListener('click',select); g.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();select(ev);}}); chart.append(g);
  });
  $('#dagwrap').replaceChildren(chart);
  $('#lg-hidden').classList.toggle('hidden', !state.task);
  const integrity=run.integrity_check || run.integrity || {};
  $('#integrity').textContent=`${events.length} events · ${run.dependencies.length} dependencies · ${run.coverage?.level || 'unknown'} coverage · integrity ${integrity.valid ? 'valid' : 'review required'}`;
}
async function loadRun(id) {
  state.run=await api('/api/dag/run/'+encodeURIComponent(id)); state.selected={A:null,B:null,C:null}; state.path=[]; syncSelections(); draw();
  $('#runinfo').textContent=state.run.run_id || id;
}
async function loadTask(id) {
  state.task=await api('/api/dag/task/'+encodeURIComponent(id));
  const t=state.task.task; $('#runsel').value=t.scenario_id; await loadRun(t.scenario_id);
  $('#tq').textContent=t.question; $('#tmeta').textContent=`${t.coverage} evidence · ${t.difficulty || ''}`; $('#tlogs').textContent=t.ai_visible_logs;
  const select=$('#attsel'); select.replaceChildren();
  for (const key of Object.keys(state.task.attempts)) option(select,key,key.replaceAll('_',' '));
  showAttempt();
}
function showAttempt() {
  const entry=state.task?.attempts[$('#attsel').value];
  $('#tgrade').replaceChildren(); $('#tclaims').replaceChildren();
  if (!entry) { $('#tgrade').textContent='No saved attempt for this task.'; return; }
  const grade=entry.graded;
  $('#tgrade').append(el('p',{class:grade.task_correct?'ok':'warn'},grade.format_failure ? 'Answer could not be parsed' : grade.task_correct ? 'Task correct' : 'Task not fully correct'));
  if(grade.mismatch) $('#tgrade').append(el('p',{},grade.mismatch));
  for (const c of grade.claims || []) {
    const card=el('div',{class:'card click'}); const r=c.result;
    card.append(el('span',{class:'badge v-'+r.verdict},r.verdict),el('p',{},`${name(c.raw.source_event_id)} → ${name(c.raw.target_event_id)}: ${c.raw.relationship}`),el('div',{class:'dim'},r.explanation));
    card.onclick=()=>{state.selected.A=c.raw.source_event_id; state.selected.B=c.raw.target_event_id; state.path=r.dependency_path || []; syncSelections(); draw();};
    $('#tclaims').append(card);
  }
}
function switchTab(tab) {
  state.tab=tab; document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));
  for(const t of ['dag','tasks','results']) $('#side-'+t).classList.toggle('hidden',t!==tab);
  $('#runfld').classList.toggle('hidden',tab==='tasks'); $('#taskfld').classList.toggle('hidden',tab!=='tasks');
  if(tab!=='tasks') {state.task=null; draw();} else if($('#tasksel').value) loadTask($('#tasksel').value).catch(error);
  if(tab==='results') loadResults().catch(error);
}
async function loadResults() {
  const data=await api('/api/dag/results'); const root=$('#side-results'); root.replaceChildren();
  const summary=data.event_tasks;
  root.append(el('h3',{},'Event-graph first attempts'),el('p',{},`${summary.model}: ${summary.correct}/${summary.tasks} tasks correct; ${summary.format_failures} format failures.`));
  for(const bin of summary.confidence_bins) root.append(el('div',{class:'dim'},`Confidence ${bin.range}: ${bin.correct}/${bin.tasks} correct`));
  root.append(el('h3',{},'Real-trace benchmark'),el('p',{class:'dim'},'Fault labels and recorded traces are evidence from RCAEval. Comparison models are identified separately.'));
  const comparison=data.real_traces?.comparison;
  if(comparison) for (const [metric, scores] of Object.entries(comparison)) { const card=el('div',{class:'card'}); card.append(el('b',{},metric.replaceAll('_',' '))); for(const [model,value] of Object.entries(scores)) card.append(el('div',{},`${model}: ${value}`)); root.append(card); }
  else root.append(el('p',{},'No real-trace results available.'));
  root.append(el('h3',{},'Captured execution tasks'));
  for (const [set, metrics] of Object.entries(data.sets)) { const details=el('details',{class:'card'}); details.append(el('summary',{},set),el('pre',{class:'logs'},JSON.stringify(metrics,null,2))); root.append(details); }
}
function stream(job, onEvent) { return new Promise((resolve,reject)=>{const es=new EventSource('/api/stream/'+job); es.onmessage=e=>{const x=JSON.parse(e.data); onEvent(x); if(x.type==='done'){es.close(); resolve();}}; es.onerror=()=>{es.close();reject(Error('Event stream disconnected'));};}); }
async function verify() {
  const claim={source_event_id:state.selected.A,target_event_id:state.selected.B,relationship:$('#rel').value,ancestor_event_id:state.selected.C};
  const body={run:$('#runsel').value,claims:[claim]};
  if(state.task) body.task=state.task.task.task_id;
  else if($('#cov').value!=='run') body.coverage={closed_world:$('#cov').value==='closed',level:$('#cov').value==='closed'?'complete':'partial',source:'user selection'};
  const result=await api('/api/dag/verify',body), v=result.verdicts[0]; state.path=v.dependency_path || []; draw();
  const contents=[el('span',{class:'badge v-'+v.verdict},v.verdict),el('p',{},v.explanation)];
  if(v.evidence?.length) contents.push(el('pre',{class:'logs'},JSON.stringify(v.evidence,null,2)));
  $('#verdict').replaceChildren(...contents);
}
async function ask() {
  $('#asksec').classList.remove('hidden'); $('#asklog').textContent='Starting model request…'; $('#askout').textContent=''; $('#askbtn').disabled=true;
  try { const res=await api('/api/dag/ask',{task_id:$('#tasksel').value}); await stream(res.job,x=>{if(x.type==='answer') $('#askout').textContent=x.text || ''; if(x.type==='error') $('#asklog').textContent=x.msg; if(x.type==='dag_grade') $('#asklog').textContent=x.task_correct?'Task correct':x.mismatch || 'Task not fully correct';}); await loadTask($('#tasksel').value); }
  finally {$('#askbtn').disabled=false;}
}
async function runLive() {
  const selected=$('#runsel').value; const sid=state.run?.scenario_id || selected;
  if(state.tab!=='dag') switchTab('dag');
  $('#run-live').disabled=true; $('#verdict').replaceChildren(el('p',{},'Running scenario…'));
  let saved=null;
  try { const res=await api('/api/dag/run-live',{scenario:sid}); await stream(res.job,ev=>{
    if(ev.type==='run_started') {state.run={scenario_id:sid,run_id:ev.run_id,events:[],dependencies:[],coverage:{level:'capture in progress'},names:{}}; $('#runinfo').textContent=ev.run_id; draw();}
    if(ev.type==='dag_event') {const {job,seq,ts,type,...event}=ev; state.run.events.push(event); draw();}
    if(ev.type==='dag_edge') {const {job,seq,ts,type,...edge}=ev; state.run.dependencies.push(edge); draw();}
    if(ev.type==='log'||ev.type==='error') $('#verdict').textContent=ev.msg;
    if(ev.type==='run_saved') {saved='live_'+ev.run_id; $('#verdict').textContent=`Captured ${ev.events} events and ${ev.edges} edges.`;}
  });
    state.runs=await api('/api/dag/runs'); const runs=$('#runsel'); runs.replaceChildren(); for(const r of state.runs) option(runs,r.scenario_id.startsWith('live_') ? r.scenario_id : (r.run_id && r.scenario_id),`${r.scenario_id} · ${r.n_events} events`);
    if(saved){runs.value=saved; await loadRun(saved);} else runs.value=selected;
  } finally {$('#run-live').disabled=false;}
}
async function init() {
  state.runs=await api('/api/dag/runs'); state.tasks=await api('/api/dag/tasks');
  for(const r of state.runs) option($('#runsel'),r.scenario_id,`${r.scenario_id} · ${r.n_events} events`);
  for(const t of state.tasks) option($('#tasksel'),t.task_id,`${t.task_id} · ${t.coverage}`);
  document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  document.querySelectorAll('[data-clear]').forEach(b=>b.onclick=()=>{state.selected[b.dataset.clear]=null; state.path=[]; syncSelections();draw();});
  $('#runsel').onchange=()=>loadRun($('#runsel').value).catch(error); $('#tasksel').onchange=()=>loadTask($('#tasksel').value).catch(error);
  $('#attsel').onchange=showAttempt; $('#verifybtn').onclick=()=>verify().catch(error); $('#askbtn').onclick=()=>ask().catch(error); $('#run-live').onclick=()=>runLive().catch(error);
  $('#swapbtn').onclick=()=>{[state.selected.A,state.selected.B]=[state.selected.B,state.selected.A];syncSelections();draw();};
  $('#theme').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='light'?'dark':'light';};
  if(state.runs.length) await loadRun(state.runs[0].scenario_id);
}
init().catch(error);
