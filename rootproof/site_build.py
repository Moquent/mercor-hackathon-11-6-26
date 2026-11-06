"""Builds results/report.html: a single self-contained page (data embedded, vanilla JS + SVG, works offline)."""
import json

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RootProof Results</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f7f6f2;--surface:#ffffff;--surface2:#f0eee8;--fg:#17171a;--muted:#6c6b66;--line:#e3e0d7;
  --accent:#5b4bdb;--accent-soft:#ece9ff;
  --ok:#16794c;--ok-soft:#e3f4ea;--bad:#c2261d;--bad-soft:#fde8e6;--warn:#a26200;--warn-soft:#fdf1dc;
  --s-noop:#a3a19a;--s-ai:#e0611b;--s-fix:#2563eb;--true:#d6372c;--load:#2563eb;--claim:#e0611b;--wrong:#8b3fd9;
  --node:#ffffff;--node-line:#cbc7bc;--shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#101013;--surface:#18181c;--surface2:#212127;--fg:#ececf1;--muted:#9c9ba6;--line:#2c2c34;
  --accent:#9b8cff;--accent-soft:#25213f;--ok:#43c486;--ok-soft:#14301f;--bad:#ff6b61;--bad-soft:#3a1715;--warn:#f0b03c;--warn-soft:#3a2a0d;
  --s-noop:#6e6d76;--s-ai:#ff8a45;--s-fix:#5f9bff;--true:#ff6b61;--load:#5f9bff;--claim:#ff8a45;--wrong:#c38bff;
  --node:#1f1f25;--node-line:#3a3a44;--shadow:none}}
:root[data-theme="dark"]{
  --bg:#101013;--surface:#18181c;--surface2:#212127;--fg:#ececf1;--muted:#9c9ba6;--line:#2c2c34;
  --accent:#9b8cff;--accent-soft:#25213f;--ok:#43c486;--ok-soft:#14301f;--bad:#ff6b61;--bad-soft:#3a1715;--warn:#f0b03c;--warn-soft:#3a2a0d;
  --s-noop:#6e6d76;--s-ai:#ff8a45;--s-fix:#5f9bff;--true:#ff6b61;--load:#5f9bff;--claim:#ff8a45;--wrong:#c38bff;
  --node:#1f1f25;--node-line:#3a3a44;--shadow:none}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
code,pre,.mono{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace}
header{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar{max-width:1240px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.logo{font-weight:800;letter-spacing:-.02em;font-size:18px;display:flex;align-items:center;gap:8px}
.logo i{width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,var(--accent),var(--s-ai));display:inline-block}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav button{border:0;background:none;color:var(--muted);font:inherit;font-weight:600;padding:7px 12px;border-radius:8px;cursor:pointer}
nav button.on{background:var(--surface);color:var(--fg);box-shadow:var(--shadow)}
nav button:hover{color:var(--fg)}
.theme{margin-left:auto;border:1px solid var(--line);background:var(--surface);color:var(--fg);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit;font-size:13px}
main{max-width:1240px;margin:0 auto;padding:24px 16px 80px}
section.page{display:none}section.page.on{display:block;animation:fade .25s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
h1{font-size:clamp(28px,4vw,44px);letter-spacing:-.03em;line-height:1.08;margin:8px 0 10px}
h2{font-size:22px;letter-spacing:-.01em;margin:36px 0 12px}
h3{font-size:16px;margin:0 0 8px}
.lede{font-size:18px;color:var(--muted);max-width:820px;margin:0 0 24px}
.lede b{color:var(--fg)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:var(--shadow)}
.kpi .v{font-size:30px;font-weight:800;letter-spacing:-.02em}
.kpi .l{color:var(--muted);font-size:13px}
.kpi .s{font-size:12px;color:var(--muted);margin-top:4px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.find{border-left:4px solid var(--accent)}
.find p{margin:6px 0 0;color:var(--muted)}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.02em}
.ACCEPT,.PASS{background:var(--ok-soft);color:var(--ok)}.REJECT{background:var(--bad-soft);color:var(--bad)}.PARTIAL,.FLAG{background:var(--warn-soft);color:var(--warn)}
.chip{display:inline-block;background:var(--surface2);border-radius:6px;padding:1px 7px;margin:2px 4px 2px 0;font:12px "JetBrains Mono",monospace}
.muted{color:var(--muted)}
.heat{display:grid;grid-template-columns:minmax(180px,1.5fr) repeat(3,minmax(70px,1fr));gap:4px;font-size:13px}
.heat div{padding:6px 8px;border-radius:6px}
.heat .h{color:var(--muted);font-weight:600}
.heat .c{text-align:center;font-weight:700;font-size:12px}
.layout{display:grid;grid-template-columns:270px 1fr;gap:18px;align-items:start}
@media (max-width:860px){.layout{grid-template-columns:1fr}}
.side{position:sticky;top:70px;display:flex;flex-direction:column;gap:4px;max-height:calc(100vh - 90px);overflow:auto}
@media (max-width:860px){.side{position:static;max-height:none;flex-direction:row;overflow-x:auto}}
.side button{display:flex;justify-content:space-between;align-items:center;gap:8px;text-align:left;border:1px solid transparent;background:none;color:var(--fg);font:inherit;font-size:13.5px;padding:8px 10px;border-radius:10px;cursor:pointer;min-width:200px}
.side button:hover{background:var(--surface)}
.side button.on{background:var(--surface);border-color:var(--line);box-shadow:var(--shadow)}
.side .dots{display:flex;gap:3px}.side .dots span{width:8px;height:8px;border-radius:50%}
.d-ACCEPT{background:var(--ok)}.d-PARTIAL{background:var(--warn)}.d-REJECT{background:var(--bad)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:14px 0 14px;flex-wrap:wrap}
.tabs button{border:0;background:none;color:var(--muted);font:inherit;font-weight:600;padding:8px 12px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs button.on{color:var(--fg);border-color:var(--accent)}
.cert{border-left:3px solid var(--bad);background:var(--bad-soft);padding:8px 12px;border-radius:0 8px 8px 0;margin:8px 0;font-size:14px}
.okline{border-left:3px solid var(--ok);background:var(--ok-soft);padding:8px 12px;border-radius:0 8px 8px 0;margin:8px 0;font-size:14px}
.checks{list-style:none;padding:0;margin:8px 0}.checks li{padding:5px 0;border-bottom:1px dashed var(--line);font-size:14px}
.checks .x{color:var(--bad);font-weight:700}.checks .y{color:var(--ok);font-weight:700}
pre{white-space:pre-wrap;background:var(--surface2);padding:12px;border-radius:10px;font-size:12.5px;max-height:460px;overflow:auto;margin:0}
svg text{font-family:Inter,ui-sans-serif,system-ui,sans-serif}
.chartwrap{position:relative}
.tip{position:absolute;pointer-events:none;background:var(--fg);color:var(--bg);font-size:12px;padding:6px 8px;border-radius:8px;white-space:nowrap;opacity:0;transition:opacity .1s}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin:6px 0}
.legend i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px;border-radius:2px}
.legend i.dash{background:repeating-linear-gradient(90deg,currentColor 0 5px,transparent 5px 9px)!important;height:2px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{border:0;background:var(--surface);color:var(--muted);font:inherit;font-size:12.5px;font-weight:600;padding:5px 10px;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff}
.dag .node rect{fill:var(--node);stroke:var(--node-line);stroke-width:1.2}
.dag .node text{font-size:11.5px;fill:var(--fg);font-weight:600}
.dag .node .sub{font-size:10px;fill:var(--muted);font-weight:500}
.dag .topo{stroke:var(--line);stroke-width:1.4;fill:none}
.dag .e{fill:none;stroke-width:2.6}
.flow{stroke-dasharray:7 6;animation:flow 1s linear infinite}
@keyframes flow{to{stroke-dashoffset:-13}}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:.03em}
tr.click{cursor:pointer}tr.click:hover td{background:var(--surface2)}
.tablewrap{overflow-x:auto}
.bars .row{display:grid;grid-template-columns:110px 1fr 54px;gap:10px;align-items:center;margin:7px 0;font-size:13.5px}
.bars .track{height:12px;background:var(--surface2);border-radius:999px;overflow:hidden;display:flex}
.bars .fill{height:100%;border-radius:999px}
.cm{display:grid;grid-template-columns:auto 1fr 1fr;gap:6px;max-width:440px;font-size:13.5px}
.cm div{padding:12px;border-radius:10px;text-align:center}
.cm .h{background:none;color:var(--muted);font-weight:600;padding:4px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;counter-reset:s}
.step{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;position:relative}
.step:before{counter-increment:s;content:counter(s);position:absolute;top:12px;right:14px;font-weight:800;color:var(--accent);opacity:.5;font-size:22px}
.step p{margin:4px 0 0;color:var(--muted);font-size:13.5px}
.footer{color:var(--muted);font-size:12.5px;margin-top:40px}
details summary{cursor:pointer;font-weight:600;color:var(--muted);margin:6px 0}
</style>
</head>
<body>
<header><div class="bar">
  <div class="logo"><i></i>RootProof</div>
  <nav id="nav">
    <button data-p="overview" class="on">Overview</button>
    <button data-p="incidents">Incidents</button>
    <button data-p="real">Real data (RCAEval)</button>
    <button data-p="method">Method</button>
  </nav>
  <button class="theme" id="theme">◐ Theme</button>
</div></header>
<main>
<section class="page on" id="p-overview"></section>
<section class="page" id="p-incidents"></section>
<section class="page" id="p-real"></section>
<section class="page" id="p-method"></section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = (s, el=document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = x => x == null ? '—' : Math.round(x * 100) + '%';
const pill = v => `<span class="pill ${v}">${v}</span>`;
const S = D.stats, R = D.rcaeval;

// ---------- nav + theme ----------
document.querySelectorAll('#nav button').forEach(b => b.onclick = () => go(b.dataset.p));
function go(p){
  document.querySelectorAll('#nav button').forEach(x => x.classList.toggle('on', x.dataset.p === p));
  document.querySelectorAll('section.page').forEach(x => x.classList.toggle('on', x.id === 'p-' + p));
  try { localStorage.setItem('rp-page', p) } catch(e) {}
  window.scrollTo({top:0});
}
$('#theme').onclick = () => {
  const r = document.documentElement, cur = r.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  r.dataset.theme = cur === 'dark' ? 'light' : 'dark';
};

// ---------- overview ----------
function overview(){
  const t = D.tasks;
  const heat = t.map(x => `<div>${esc(x.id)} <span class="muted">· ${esc(x.title.replace(/^(COMPLEX|CONTROL): /,''))}</span></div>` +
    x.verdicts.map(v => `<div class="c ${v}">${v ?? '—'}</div>`).join('')).join('');
  $('#p-overview').innerHTML = `
  <div class="mono muted" style="font-size:12.5px">Mercor Hack Day · verifying AI output beyond math and code</div>
  <h1>Don't read the AI's incident diagnosis.<br>Replay the outage with its fix.</h1>
  <p class="lede">RootProof checks AI root-cause analyses. It <b>replays a seeded incident with the AI's proposed fix applied</b>, checks the AI's
  <b>causal chain</b> against the dependency graph, and runs the claim checks on <b>real fault-injection data (RCAEval)</b>.
  Every rejection comes with evidence. Subject AI: <code>${esc(D.model)}</code>.</p>
  <div class="kpis">
    <div class="kpi"><div class="v">${S.first_accept}/${S.tasks}</div><div class="l">simulated incidents: AI first attempt accepted</div><div class="s">${S.tasks - S.first_accept} caught · ${S.runs_accept}/${S.runs} across 3 runs</div></div>
    <div class="kpi"><div class="v">${S.selftest_ok}/${S.selftest_n}</div><div class="l">verifier self-test</div><div class="s">correct answers accepted + trap answers caught</div></div>
    <div class="kpi"><div class="v">${pct(R.test.verifier_accuracy)}</div><div class="l">verifier accuracy on real data</div><div class="s">RCAEval RE2-OB held-out split, n=${R.test.cases}</div></div>
    <div class="kpi"><div class="v">${pct(R.test.ai_service_and_fault_acc)}</div><div class="l">AI right on service <i>and</i> fault type</div><div class="s">service alone: ${pct(R.test.ai_service_acc)} · z-score baseline ${pct(R.test.baseline_ac1)}</div></div>
    <div class="kpi"><div class="v">${pct(S.chain_precision)} / ${pct(S.chain_recall)}</div><div class="l">causal-chain precision / recall</div><div class="s">AI's claimed propagation edges vs. the true graph</div></div>
  </div>
  <h2>What we found</h2>
  <div class="grid3">
    <div class="card find"><h3>It fixes the trigger, not the loop</h3><p>When a config change is visibly the trigger (CPU limit, connection pool, cache TTL), the model reverts it and stops.
      Layered retries are already sending about 8x the load, so in replay errors stay at 100%. Tasks 02 and 13 failed in every run.</p></div>
    <div class="card find" style="border-color:var(--warn)"><h3>Right fix, wrong story</h3><p>In 15 (a loud storm hiding a quiet credential fault) and 05, the actions work in replay, but the named root
      cause is contradicted: users saw errors before the blamed service degraded, or the blamed service is a downstream victim. Grading only the outcome would miss this.</p></div>
    <div class="card find" style="border-color:var(--s-fix)"><h3>On real data, the fault type is the weak spot</h3><p>On RCAEval the AI always names the right service, but it labels memory, socket and loss faults as "delay".
      Reference-free checks flag ${R.test.flagged} answers, and ${pct(R.test.verifier_flag_precision)} of those flags are real errors.</p></div>
  </div>
  <h2>Verdicts: 15 incidents × 3 fresh runs</h2>
  <div class="card"><div class="heat"><div class="h">task</div><div class="h c">run 1</div><div class="h c">run 2</div><div class="h c">run 3</div>${heat}</div></div>
  <h2>How it works</h2>
  ${pipeline()}
  <p class="footer">Everything on this page is computed by <code>python3 rootproof/evaluate.py</code> from the saved, unedited AI answers.</p>`;
}
function pipeline(){
  const box = (x, t, s, c) => `<g transform="translate(${x},20)"><rect width="168" height="92" rx="14" fill="var(--surface)" stroke="${c}" stroke-width="1.5"/>
    <text x="14" y="30" font-weight="700" font-size="14" fill="var(--fg)">${t}</text>${s.map((l,i)=>`<text x="14" y="${52+i*16}" font-size="11.5" fill="var(--muted)">${l}</text>`).join('')}</g>`;
  const arr = x => `<path d="M${x} 66 h22" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#ah)"/>`;
  return `<div class="card" style="overflow-x:auto"><svg viewBox="0 0 1010 130" style="min-width:820px;width:100%">
  <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--muted)"/></marker></defs>
  ${box(0,'Seeded incident',['simulated platform or','real RCAEval telemetry'],'var(--line)')}${arr(170)}
  ${box(200,'Subject AI',[D.model+', 1 fresh','request, first answer kept'],'var(--line)')}${arr(370)}
  ${box(400,'Typed extraction',['Jev: actions, roots,','causal edges + probabilities'],'var(--accent)')}${arr(570)}
  ${box(600,'Replay (decides)',['apply fix at page time,','did errors fall below 5%?'],'var(--s-fix)')}${arr(770)}
  ${box(800,'Claim checks',['timing · reachability ·','origin · baseline · path'],'var(--true)')}
  </svg></div>`;
}

// ---------- charts ----------
function lineChart(el, series, opts={}){
  const W = 640, H = 220, pl = 40, pr = 10, pt = 14, pb = 26, xs = series[0].pts.map(p => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const X = t => pl + (t - x0) / (x1 - x0) * (W - pl - pr), Y = e => pt + (1 - e) * (H - pt - pb);
  const fmt = t => { const s = 14*3600 + t; return `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor(s%3600/60)).padStart(2,'0')}` };
  let g = '';
  [0, .25, .5, .75, 1].forEach(e => g += `<line x1="${pl}" x2="${W-pr}" y1="${Y(e)}" y2="${Y(e)}" stroke="var(--line)"/><text x="${pl-6}" y="${Y(e)+4}" font-size="10" fill="var(--muted)" text-anchor="end">${e*100}%</text>`);
  g += `<rect x="${X(450)}" y="${pt}" width="${X(600)-X(450)}" height="${H-pt-pb}" fill="var(--surface2)" opacity=".7"/>`;
  g += `<text x="${(X(450)+X(600))/2}" y="${pt+12}" font-size="10" fill="var(--muted)" text-anchor="middle">judged window</text>`;
  g += `<line x1="${X(300)}" x2="${X(300)}" y1="${pt}" y2="${H-pb}" stroke="var(--muted)" stroke-dasharray="3 3"/><text x="${X(300)-4}" y="${pt+12}" font-size="10" fill="var(--muted)" text-anchor="end">paged · actions applied</text>`;
  [60, 180, 300, 420, 540].forEach(t => g += `<text x="${X(t)}" y="${H-8}" font-size="10" fill="var(--muted)" text-anchor="middle">${fmt(t)}</text>`);
  series.forEach(s => g += `<path d="${s.pts.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join(' ')}" fill="none" stroke="${s.color}" stroke-width="${s.w||2.4}" ${s.dash?'stroke-dasharray="6 4"':''} stroke-linejoin="round"/>`);
  el.innerHTML = `<div class="chartwrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%">${g}<line class="cross" y1="${pt}" y2="${H-pb}" stroke="var(--fg)" opacity="0"/><rect x="${pl}" y="${pt}" width="${W-pl-pr}" height="${H-pt-pb}" fill="transparent" class="hit"/></svg><div class="tip"></div></div>`;
  const svg = el.querySelector('svg'), tip = el.querySelector('.tip'), cross = el.querySelector('.cross');
  el.querySelector('.hit').addEventListener('mousemove', ev => {
    const r = svg.getBoundingClientRect(), vx = (ev.clientX - r.left) / r.width * W;
    const t = x0 + (vx - pl) / (W - pl - pr) * (x1 - x0);
    const i = series[0].pts.reduce((b, p, j) => Math.abs(p[0]-t) < Math.abs(series[0].pts[b][0]-t) ? j : b, 0);
    const tt = series[0].pts[i][0];
    cross.setAttribute('x1', X(tt)); cross.setAttribute('x2', X(tt)); cross.setAttribute('opacity', .25);
    tip.innerHTML = `<b>${fmt(tt)}</b><br>` + series.map(s => `<span style="color:${s.color}">●</span> ${s.label}: ${Math.round(s.pts[i][1]*100)}%`).join('<br>');
    tip.style.opacity = 1; tip.style.left = Math.min(r.width - 170, (X(tt) / W) * r.width + 12) + 'px'; tip.style.top = '10px';
  });
  el.querySelector('.hit').addEventListener('mouseleave', () => { tip.style.opacity = 0; cross.setAttribute('opacity', 0) });
}

// ---------- DAG (Larkwave) ----------
const POS = {'edge-gw':[460,40],'session-api':[460,130],'docstore':[110,230],'meta-index':[290,230],'notify-relay':[470,230],'player-core':[650,230],
  'sub-poller':[110,330],'msg-cleanup':[470,330],'media-cache':[650,330],'vela-origin':[530,430],'korra-origin':[650,430],'brio-origin':[770,430],
  'clip-preview':[860,130],'clipstream-origin':[860,230]};
function edgePath(a, b, bend=0){
  const [x1,y1] = POS[a], [x2,y2] = POS[b];
  const up = y2 < y1, sy = y1 + (up ? -17 : 17), ey = y2 + (up ? 19 : -19);
  const mx = (x1+x2)/2 + bend, my = (sy+ey)/2;
  return `M${x1+bend/3},${sy} Q${mx},${my} ${x2+bend/3},${ey}`;
}
function dag(el, task, mode){
  const C = task.chain, tru = new Set(C.true_edges.map(e => e.join('>'))), cl = new Set(C.claimed_edges.map(e => e.join('>')));
  const origins = new Set(C.origins.map(o => o));
  let g = `<defs>${['true','load','claim','wrong','muted'].map(k => `<marker id="m-${k}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--${k==='muted'?'line':k})"/></marker>`).join('')}</defs>`;
  g += `<rect x="800" y="95" width="120" height="175" rx="12" fill="none" stroke="var(--line)" stroke-dasharray="4 4"/><text x="860" y="88" font-size="10" fill="var(--muted)" text-anchor="middle">background, off request path</text>`;
  D.topology.edges.forEach(([c,p]) => g += `<path class="topo" d="${edgePath(p,c)}"/>`);
  D.topology.jobs.forEach(([j,s]) => g += `<path class="topo" stroke-dasharray="3 3" d="${edgePath(j,s)}"/>`);
  const isUp = (a,b) => POS[b][1] < POS[a][1];
  if (mode !== 'claim') C.true_edges.forEach(([a,b]) => {
    const load = !isUp(a,b) || a === 'sub-poller' || a === 'msg-cleanup';
    const k = load ? 'load' : 'true';
    g += `<path class="e flow" d="${edgePath(a,b, load? 18 : -18)}" stroke="var(--${k})" marker-end="url(#m-${k})" opacity="${mode==='both'?.55:.95}"><title>${a} → ${b} (${load?'load amplification':'failure propagation'})</title></path>`;
  });
  if (mode !== 'truth') C.claimed_edges.forEach(([a,b]) => {
    const ok = tru.has(a+'>'+b), k = ok ? 'claim' : 'wrong', load = !isUp(a,b);
    g += `<path class="e" d="${edgePath(a,b, load? 30 : -30)}" stroke="var(--${k})" stroke-dasharray="2 5" stroke-linecap="round" stroke-width="3.2" marker-end="url(#m-${k})"><title>AI claims ${a} → ${b}${ok?'':' (not supported by telemetry)'}</title></path>`;
  });
  Object.entries(POS).forEach(([n,[x,y]]) => {
    const st = task.nodes[n] || {}, err = st.err ?? 0, lx = st.load_x, ring = origins.has(n);
    const col = err > .3 ? 'var(--bad)' : err > .05 ? 'var(--warn)' : (lx && lx > 1.5) ? 'var(--load)' : 'var(--node-line)';
    const sub = !(n in task.nodes) ? 'job' : st.idle ? 'idle (not called)' : `err ${Math.round(err*100)}%${lx && lx > 1.5 ? ' · load ×' + lx.toFixed(1) : ''}`;
    g += `<g class="node" transform="translate(${x-62},${y-17})">${ring?`<rect x="-4" y="-4" width="132" height="42" rx="12" fill="none" stroke="var(--true)" stroke-width="2" stroke-dasharray="3 2"/>`:''}
      <rect width="124" height="34" rx="9" style="stroke:${col};stroke-width:${err>.05||ring?2.2:1.2}"/>
      <text x="62" y="15" text-anchor="middle">${n}</text><text class="sub" x="62" y="28" text-anchor="middle">${sub}</text></g>`;
  });
  el.innerHTML = `<svg class="dag" viewBox="0 0 940 465" style="width:100%">${g}</svg>`;
}

// ---------- incidents ----------
let cur = 0, dagMode = 'both', subtab = 'replay';
function incidents(){
  const t = D.tasks;
  $('#p-incidents').innerHTML = `<div class="layout"><div class="side" id="side">${t.map((x,i) => `<button data-i="${i}" class="${i===cur?'on':''}">
    <span><b>${esc(x.id.slice(0,2))}</b> ${esc(x.id.slice(3))}</span><span class="dots">${x.verdicts.map(v=>`<span class="d-${v}" title="${v}"></span>`).join('')}</span></button>`).join('')}</div>
    <div id="detail"></div></div>`;
  document.querySelectorAll('#side button').forEach(b => b.onclick = () => { cur = +b.dataset.i; incidents(); });
  detail();
}
function detail(){
  const x = D.tasks[cur], f = x.first, el = $('#detail');
  const acts = (f.json && f.json.actions || []).map(a => `<li>${esc(a)}</li>`).join('') || '<li class="muted">no JSON block</li>';
  const comp = Object.entries(f.actions).map(([a,p]) => `<span class="chip">${a} · p=${p.toFixed(2)}</span>`).join('') || '<span class="chip">no production action</span>';
  const certs = f.certificates.map(c => `<div class="cert">${esc(c)}</div>`).join('') || `<div class="okline">${x.no_outage ? 'No production change proposed for an incident with no user impact.' : `Replay with the AI's actions: entry error rate ${pct(f.err_after)} in the judged window.`}</div>`;
  const checks = f.invariants.map(i => `<li><span class="${i.pass?'y':'x'}">${i.pass?'✓':'✗'}</span> <b>${esc(i.check)}</b> ${esc(i.detail)}</li>`).join('');
  const C = x.chain;
  el.innerHTML = `<div class="card">
    <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center"><div class="mono muted">${esc(x.id)}</div>
      <div>first attempt ${pill(f.verdict)} &nbsp;<span class="muted" style="font-size:12.5px">runs: ${x.verdicts.map(pill).join(' ')}</span></div></div>
    <h2 style="margin:6px 0 10px">${esc(x.title)}</h2>
    <div class="grid2">
      <div><div class="muted" style="font-size:12.5px;font-weight:600">AI NAMED ROOT CAUSE</div><div style="margin:4px 0 10px"><code>${esc(f.json && f.json.root_cause_service)}</code></div>
        <div class="muted" style="font-size:12.5px;font-weight:600">AI'S ACTIONS (VERBATIM)</div><ul style="margin:6px 0;padding-left:18px">${acts}</ul></div>
      <div><div class="muted" style="font-size:12.5px;font-weight:600">COMPILED BY JEV TO</div><div style="margin:6px 0 10px">${comp}</div>
        <div class="muted" style="font-size:12.5px;font-weight:600">COMPUTED MINIMAL FIXES</div><div style="margin:6px 0">${x.fixes.length ? x.fixes.map(s => s.map(a=>`<span class="chip">${a}</span>`).join('+ ')).join('<br>') : '<span class="chip">no action needed</span>'}</div></div>
    </div>
    ${certs}
    <div class="tabs" id="sub">${['replay','graph','answer','packet','truth'].map(k => `<button data-k="${k}" class="${k===subtab?'on':''}">${({replay:'Replay',graph:'Causal graph',answer:'AI answer',packet:'What the AI saw',truth:'Ground truth'})[k]}</button>`).join('')}</div>
    <div id="subbody"></div></div>`;
  document.querySelectorAll('#sub button').forEach(b => b.onclick = () => { subtab = b.dataset.k; detail(); });
  const body = $('#subbody');
  if (subtab === 'replay') {
    body.innerHTML = `<div class="legend"><span><i style="background:var(--s-noop)"></i>no action</span><span><i style="background:var(--s-ai)"></i>AI's actions replayed</span><span><i style="background:var(--s-fix)"></i>computed minimal fix</span></div><div id="chart"></div>
      <h3 style="margin-top:14px">Claim checks</h3><ul class="checks">${checks}</ul>`;
    lineChart($('#chart'), [{label:'no action',color:'var(--s-noop)',pts:x.curves.noop,w:2},{label:'computed fix',color:'var(--s-fix)',pts:x.curves.fix},{label:"AI's actions",color:'var(--s-ai)',pts:x.curves.ai,dash:true,w:2.6}]);
  } else if (subtab === 'graph') {
    const miss = C.true_edges.filter(e => !C.claimed_edges.some(c => c[0]===e[0] && c[1]===e[1]));
    body.innerHTML = `<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:center">
      <div class="legend"><span><i style="background:var(--true)"></i>failure propagation (true)</span><span><i style="background:var(--load)"></i>load / retry amplification (true)</span>
      <span style="color:var(--claim)"><i class="dash"></i>AI-claimed edge</span><span style="color:var(--wrong)"><i class="dash"></i>AI edge not supported</span><span>dashed ring = injected fault</span></div>
      <div class="seg" id="seg">${['truth','claim','both'].map(m=>`<button data-m="${m}" class="${m===dagMode?'on':''}">${({truth:'Truth',claim:'AI claim',both:'Overlay'})[m]}</button>`).join('')}</div></div>
      <div id="dag"></div>
      <div class="grid2" style="margin-top:8px"><div><b>Edge precision</b> ${pct(C.precision)} · <b>recall</b> ${pct(C.recall)}</div>
      <div class="muted" style="font-size:13.5px">${miss.length ? 'Missed by the AI: ' + miss.map(e=>`<span class="chip">${e[0]}→${e[1]}</span>`).join('') : 'The AI described every true propagation edge.'}
      ${C.unsupported_edges.length ? '<br>Unsupported: ' + C.unsupported_edges.map(e=>`<span class="chip">${e[0]}→${e[1]}</span>`).join('') : ''}</div></div>`;
    document.querySelectorAll('#seg button').forEach(b => b.onclick = () => { dagMode = b.dataset.m; detail(); });
    dag($('#dag'), x, dagMode);
  } else if (subtab === 'answer') {
    body.innerHTML = `<div class="muted" style="font-size:12.5px;margin-bottom:6px">${esc(f.model)} · asked ${esc(f.asked.slice(0,19))}Z · first attempt, unedited</div><pre>${esc(f.answer)}</pre>`;
  } else if (subtab === 'packet') {
    body.innerHTML = `<div class="muted" style="font-size:12.5px;margin-bottom:6px">Exact prompt (tasks/${esc(x.id)}/prompt.txt)</div><pre>${esc(x.prompt)}</pre>`;
  } else {
    body.innerHTML = `<p>${esc(x.truth)}</p><h3>Self-test on this task</h3><p>Reference answer ${pill(x.reference)} · trap answer <code>${esc(x.trap_actions.join('; '))}</code> ${pill(x.trap)}</p>${x.trap_cert ? `<div class="cert">${esc(x.trap_cert)}</div>` : ''}
      <h3>Change / event log</h3><table>${x.events.map(e=>`<tr><td class="mono">${esc(e[0])}</td><td>${esc(e[1])}</td><td>${esc(e[2])}</td><td>${esc(e[3])}</td></tr>`).join('')}</table>`;
  }
}

// ---------- real data ----------
const OB = {'frontend':[460,40],'adservice':[60,150],'cartservice':[190,150],'checkoutservice':[330,150],'currencyservice':[470,150],'productcatalogservice':[620,150],'recommendationservice':[780,150],'shippingservice':[910,150],
  'emailservice':[330,260],'paymentservice':[470,260],'redis':[190,260]};
function obDag(row){
  const path = (row.ai_path||[]).map(s => String(s).toLowerCase().replace(/[^a-z]/g,'')).map(n => Object.keys(OB).find(k => k===n || k.replace('service','')===n) || n);
  const hops = new Set(path.slice(0,-1).map((a,i)=>a+'>'+path[i+1]));
  let g = `<defs><marker id="ob-a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--claim)"/></marker></defs>`;
  Object.entries(R.deps).forEach(([p,cs]) => cs.forEach(c => { if(OB[p]&&OB[c]) g += `<line x1="${OB[p][0]}" y1="${OB[p][1]+15}" x2="${OB[c][0]}" y2="${OB[c][1]-15}" stroke="var(--line)" stroke-width="1.3"/>`; }));
  path.slice(0,-1).forEach((a,i) => { const b = path[i+1]; if (OB[a] && OB[b]) g += `<line x1="${OB[a][0]}" y1="${OB[a][1]+(OB[b][1]<OB[a][1]?-16:16)}" x2="${OB[b][0]}" y2="${OB[b][1]+(OB[b][1]<OB[a][1]?18:-18)}" stroke="var(--claim)" stroke-width="3" stroke-dasharray="2 5" stroke-linecap="round" marker-end="url(#ob-a)" class="flow"/>`; });
  Object.entries(OB).forEach(([n,[x,y]]) => {
    const truth = n === row.truth_service, ai = n === row.ai_service;
    const col = truth ? 'var(--true)' : ai ? 'var(--claim)' : 'var(--node-line)';
    g += `<g class="node" transform="translate(${x-58},${y-15})"><rect width="116" height="30" rx="8" style="stroke:${col};stroke-width:${truth||ai?2.4:1.2}"/><text x="58" y="19" text-anchor="middle">${n.replace('service','')}</text></g>`;
  });
  return `<svg class="dag" viewBox="0 0 980 290" style="width:100%">${g}</svg><div class="legend"><span><i style="background:var(--true)"></i>true root (label)</span><span style="color:var(--claim)"><i class="dash"></i>AI's root + claimed path</span></div>`;
}
let openRow = null;
function real(){
  const t = R.test, rows = R.rows;
  const byFault = {};
  rows.forEach(r => { (byFault[r.truth_fault] ||= {n:0,ok:0}); byFault[r.truth_fault].n++; byFault[r.truth_fault].ok += r.ai_fault_correct ? 1 : 0; });
  const tp = rows.filter(r => r.verifier==='FLAG' && !r.ai_fault_correct).length, fp = rows.filter(r => r.verifier==='FLAG' && r.ai_fault_correct).length;
  const fn = rows.filter(r => r.verifier==='PASS' && !r.ai_fault_correct).length, tn = rows.filter(r => r.verifier==='PASS' && r.ai_fault_correct).length;
  const bar = (label, v, color) => `<div class="row"><div>${label}</div><div class="track"><div class="fill" style="width:${v*100}%;background:${color}"></div></div><div class="mono">${pct(v)}</div></div>`;
  $('#p-real').innerHTML = `
  <h1 style="font-size:32px">Real telemetry: RCAEval RE2 · Online Boutique</h1>
  <p class="lede">These are 60 real fault-injection cases (5 services × 6 fault types × 2 instances) from the
  <a href="https://github.com/phamquiluan/RCAEval" style="color:var(--accent)">RCAEval benchmark</a>. Recorded data can't be replayed, so RootProof checks the AI's <b>claim</b>
  against the metrics and the call graph, without seeing the label. Instance 2 (dev) was used to design the rules. <b>Instance 1 (test) is held out</b> and shown here.</p>
  <div class="kpis">
    <div class="kpi"><div class="v">${pct(t.ai_service_acc)}</div><div class="l">AI: correct root-cause service</div><div class="s">z-score baseline AC@1 ${pct(t.baseline_ac1)}, AC@3 ${pct(t.baseline_ac3)}</div></div>
    <div class="kpi"><div class="v">${pct(t.ai_service_and_fault_acc)}</div><div class="l">AI: service and fault type both correct</div><div class="s">${t.ai_wrong} of ${t.cases} answers wrong</div></div>
    <div class="kpi"><div class="v">${pct(t.verifier_accuracy)}</div><div class="l">verifier agrees with the label</div><div class="s">dev split: ${pct(R.dev.verifier_accuracy)}</div></div>
    <div class="kpi"><div class="v">${pct(t.verifier_flag_precision)}</div><div class="l">of flags are real errors</div><div class="s">recall ${pct(t.verifier_flag_recall)} (${tp} of ${t.ai_wrong} caught)</div></div>
    <div class="kpi"><div class="v">${pct(t.pass_precision)}</div><div class="l">of passed answers are correct</div><div class="s">vs. ${pct(t.ai_service_and_fault_acc)} with no verifier</div></div>
  </div>
  <div class="grid2" style="margin-top:14px">
    <div class="card"><h3>Who finds the root cause?</h3><div class="bars">
      ${bar('AI service', t.ai_service_acc, 'var(--accent)')}${bar('AI svc+fault', t.ai_service_and_fault_acc, 'var(--s-ai)')}${bar('z-score AC@1', t.baseline_ac1, 'var(--s-noop)')}${bar('z-score AC@3', t.baseline_ac3, 'var(--s-noop)')}</div>
      <h3 style="margin-top:16px">AI fault-type accuracy, by injected fault</h3><div class="bars">${Object.entries(byFault).map(([k,v]) => bar(k, v.ok/v.n, v.ok/v.n < .6 ? 'var(--bad)' : 'var(--ok)')).join('')}</div></div>
    <div class="card"><h3>Verifier vs. label (held-out)</h3>
      <div class="cm"><div class="h"></div><div class="h">AI answer wrong</div><div class="h">AI answer right</div>
        <div class="h">verifier FLAG</div><div style="background:var(--ok-soft);color:var(--ok)"><b>${tp}</b><br>caught</div><div style="background:var(--bad-soft);color:var(--bad)"><b>${fp}</b><br>false alarm</div>
        <div class="h">verifier PASS</div><div style="background:var(--bad-soft);color:var(--bad)"><b>${fn}</b><br>missed</div><div style="background:var(--ok-soft);color:var(--ok)"><b>${tn}</b><br>accepted</div></div>
      <p class="muted" style="font-size:13.5px;margin-top:12px">The checks are <b>evidence</b> (the blamed service is anomalous), <b>signature</b> (the claimed fault type matches the service's own metrics;
      network faults don't exhaust CPU or memory, and pure delay produces no errors), <b>temporal</b> (nothing on the claimed path degraded first) and <b>path</b> (every hop is a real call edge ending at frontend).</p></div>
  </div>
  <h2>Every held-out case</h2>
  <div class="card tablewrap"><table><thead><tr><th>case</th><th>label</th><th>AI said</th><th>AI correct</th><th>verifier</th><th>why</th></tr></thead><tbody id="rc"></tbody></table></div>`;
  const tb = $('#rc');
  tb.innerHTML = rows.map((r,i) => {
    const bad = r.checks.filter(c => c.pass === false);
    return `<tr class="click" data-i="${i}"><td class="mono">${esc(r.case.replace('re2ob_',''))}</td><td>${esc(r.truth_service.replace('service',''))} / ${esc(r.truth_fault)}</td>
      <td>${esc((r.ai_service||'?').replace('service',''))} / ${esc(r.ai_fault)}</td><td>${r.ai_fault_correct ? '✓' : '<span style="color:var(--bad)">✗</span>'}</td><td>${pill(r.verifier)}</td>
      <td class="muted" style="font-size:12.5px">${bad.map(c => esc(c.detail)).join('<br>') || '—'}</td></tr>` + (openRow === i ? `<tr><td colspan="6">${obDag(r)}</td></tr>` : '');
  }).join('');
  tb.querySelectorAll('tr.click').forEach(tr => tr.onclick = () => { openRow = openRow === +tr.dataset.i ? null : +tr.dataset.i; real(); });
}

// ---------- method ----------
function method(){
  $('#p-method').innerHTML = `<h1 style="font-size:32px">Method</h1>
  <div class="steps">
    <div class="step"><h3>Seeded incidents</h3><p>A fluid-queue simulator of a fictional streaming backend with 14 components: retries, timeouts, fallback chains, rate limits, queues and background jobs. Faults are injected; the packet the AI sees is rendered from the run.</p></div>
    <div class="step"><h3>Subject AI</h3><p>${esc(D.model)}, one stateless request per task, 3 runs. The first answer is kept unedited. The AI never sees the checker or the answer key.</p></div>
    <div class="step"><h3>Typed extraction</h3><p>TypeSafe Jev maps prose to a fixed catalog of 21 actions and to causal edges, with probabilities. It is audited against hand labels (${S.audit_ok}/${S.audit_n} decisions agree).</p></div>
    <div class="step"><h3>Replay decides</h3><p>The AI's actions are applied at page time in the same seeded run. Errors must stay under 5% over the judged window. Any fix that works is accepted, not just ours.</p></div>
    <div class="step"><h3>Claims explain</h3><p>Reachability, baseline noise, timing, origin vs victim, retry amplification, minimum scale factor, and causal-edge precision/recall against the true propagation graph.</p></div>
    <div class="step"><h3>Computed ground truth</h3><p>Every single action and pair of actions is replayed; the minimal sets that end the incident are the answer key. On RCAEval the label comes from the benchmark.</p></div>
  </div>
  <h2>Verdicts</h2>
  <div class="grid3"><div class="card">${pill('ACCEPT')}<p class="muted">The fix ends the incident in replay and a named root cause passes every claim check.</p></div>
  <div class="card">${pill('PARTIAL')}<p class="muted">Right fix with a wrong story (victim, effect-before-cause), or the right story with a fix that fails in replay.</p></div>
  <div class="card">${pill('REJECT')}<p class="muted">The fix fails and the story is contradicted, or the answer changes production when there is no outage.</p></div></div>
  <h2>Limits</h2>
  <div class="card"><ul style="margin:0;padding-left:18px">
    <li>The simulated systems are a fluid model, not a cluster. The dynamics (queueing, timeouts, metastable retry storms) are standard, but the numbers are synthetic and the names fictional.</li>
    <li>Real data can't be replayed, so the RCAEval track checks claims, not fixes. The signature rules were designed on the dev split and scored on the held-out split.</li>
    <li>One subject model. Tasks 12–15 were written after the first results, to test the hypotheses (both confirmed and refuted cases are reported).</li>
    <li>Actions outside the 21-action catalog are ignored, which can under-credit an unusual but valid fix. The extraction audit is how we check this.</li></ul></div>`;
}

overview(); incidents(); real(); method();
try { const p = localStorage.getItem('rp-page'); if (p) go(p); } catch(e) {}
</script>
</body>
</html>
"""


def build(data, path):
    blob = json.dumps(data, default=list).replace("</", "<\\/")
    open(path, "w").write(HTML.replace("__DATA__", blob))
