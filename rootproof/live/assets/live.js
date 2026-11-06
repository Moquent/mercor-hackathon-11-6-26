/* RootProof Live: front end. Plain ES2020, no dependencies.
 * Sections: utils · state · api · topology · simulation playback · particles/cursor loop ·
 * agent stream · verification · benchmark · composer · event log · SSE dispatch · controls/init */
'use strict';

/* ---------- utils ---------- */
const $ = (s, r = document) => r.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}
function s(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) { if (k === 'text') e.textContent = v; else e.setAttribute(k, v); }
  if (parent) parent.appendChild(e);
  return e;
}
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const lerp = (a, b, k) => a + (b - a) * k;
const easeInOut = k => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
const pct = x => x == null || isNaN(x) ? '–' : (x * 100 < 10 ? (x * 100).toFixed(1) : Math.round(x * 100)) + '%';
const num = x => x == null ? '–' : x >= 1000 ? (x / 1000).toFixed(1) + 'k' : String(Math.round(x));
const clock = t => { t = Math.max(0, Math.floor(t)); return `14:${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`; };
const sid = st => st == null ? '' : (typeof st === 'number' ? 's' + st : String(st));
const short = (v, n = 80) => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t && t.length > n ? t.slice(0, n - 1) + '…' : (t || ''); };
function argsText(args) {
  if (!args || typeof args !== 'object') return '';
  return Object.entries(args).filter(([k]) => k !== 'reason').map(([, v]) => typeof v === 'string' ? v : JSON.stringify(v)).join(', ');
}
function rgbMix(a, b, k) { return `rgb(${a.map((x, i) => Math.round(lerp(x, b[i], k))).join(',')})`; }
const C_OK = [60, 207, 142], C_WARN = [242, 184, 75], C_BAD = [255, 93, 108];
function errColor(e) {   // 0 -> green, 5% -> amber, >=40% -> red
  if (e == null) return 'rgb(110,120,135)';
  if (e <= 0.05) return rgbMix(C_OK, C_WARN, clamp(e / 0.05, 0, 1) ** 1.5);
  return rgbMix(C_WARN, C_BAD, clamp((e - 0.05) / 0.35, 0, 1));
}
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = 'toast on' + (err ? ' err' : ''); clearTimeout(toast._t); toast._t = setTimeout(() => t.className = 'toast', 3500); }

/* ---------- state ---------- */
const POS = {
  'edge-gw': [400, 40], 'session-api': [400, 140], 'docstore': [110, 250], 'meta-index': [300, 250], 'notify-relay': [490, 250],
  'player-core': [680, 250], 'sub-poller': [110, 345], 'msg-cleanup': [490, 345], 'media-cache': [680, 345], 'vela-origin': [455, 450],
  'korra-origin': [610, 450], 'brio-origin': [765, 450], 'clip-preview': [840, 140], 'clipstream-origin': [840, 250],
};
const GLOBAL_SPOT = [215, 95];
const S = {
  catalog: null, incident: null, run: 1, sim: null, errScale: 1, maxRps: 1,
  t: 0, playing: true, speed: 30, lastFrame: 0,
  nodes: {}, edges: {}, edgeList: [], callers: {},
  job: null, es: null, jobKind: null, jobStart: 0, status: 'idle',
  steps: {}, inspected: {}, pendingReasoning: [], tokens: { in: 0, out: 0 }, latency: 0, nSteps: 0,
  cursor: { x: GLOBAL_SPOT[0], y: GLOBAL_SPOT[1], fx: 0, fy: 0, tx: GLOBAL_SPOT[0], ty: GLOBAL_SPOT[1], t0: 0, dur: 1, visible: false },
  particles: [], replay: { base: null, truth: null, seeds: [] }, logCount: 0, bench: {}, lastCase: null,
};

/* ---------- api ---------- */
async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.json(); }
async function postJSON(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let j = null; try { j = await r.json(); } catch (e) { /* non-JSON */ }
  if (!r.ok) throw new Error((j && (j.error || j.msg)) || `${url}: HTTP ${r.status}`);
  return j;
}

/* ---------- topology ---------- */
const BOX = { w: 116, h: 30 }, JOBBOX = { w: 92, h: 24 };
function nodeBox(id) { return S.nodes[id] && S.nodes[id].job ? JOBBOX : BOX; }
function pos(id) { return (S.nodes[id] && S.nodes[id].p) || POS[id] || GLOBAL_SPOT; }
function clipToBox(c, d, box) {   // point on box border of centre c in direction d
  const tx = d[0] ? (box.w / 2 + 3) / Math.abs(d[0]) : Infinity, ty = d[1] ? (box.h / 2 + 3) / Math.abs(d[1]) : Infinity;
  const k = Math.min(tx, ty); return [c[0] + d[0] * k, c[1] + d[1] * k];
}
function edgeGeom(a, b) {
  const pa = pos(a), pb = pos(b), dx = pb[0] - pa[0], dy = pb[1] - pa[1], L = Math.hypot(dx, dy) || 1, d = [dx / L, dy / L];
  const p0 = clipToBox(pa, d, nodeBox(a)), p1 = clipToBox(pb, [-d[0], -d[1]], nodeBox(b));
  return { p0, p1, mid: [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2], len: Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) };
}
function buildTopology(topo) {
  const gN = $('#g-nodes'), gE = $('#g-edges');
  gN.innerHTML = ''; gE.innerHTML = ''; S.nodes = {}; S.edges = {}; S.edgeList = []; S.callers = {};
  const list = (topo && topo.nodes) || [];
  const jobs = (topo && topo.jobs) || {};
  const ids = list.map(n => n.id).concat(Object.keys(jobs).filter(j => !list.some(n => n.id === j)));
  let extra = 0;
  for (const id of ids) {
    const p = POS[id] || [80 + (extra++ % 8) * 105, 485];
    S.nodes[id] = { id, p, job: id in jobs, deps: (list.find(n => n.id === id) || {}).deps || [] };
  }
  const addEdge = (a, b, job) => {
    if (!S.nodes[a] || !S.nodes[b]) return;
    const key = a + '>' + b, g = edgeGeom(a, b);
    const el = s('line', { x1: g.p0[0], y1: g.p0[1], x2: g.p1[0], y2: g.p1[1], class: 'edge' + (job ? ' job' : ''), 'marker-end': 'url(#arrow)' }, gE);
    const e = { key, src: a, dst: b, job, g, el, badge: null, acc: Math.random() };
    S.edges[key] = e; S.edgeList.push(e); (S.callers[b] = S.callers[b] || []).push(a);
  };
  for (const n of list) for (const d of n.deps || []) addEdge(n.id, d, false);
  for (const [j, tgt] of Object.entries(jobs)) addEdge(j, tgt, true);
  // virtual entry edge: listeners -> entry
  const entry = (topo && topo.entry) || 'edge-gw';
  if (S.nodes[entry]) {
    const p = pos(entry);
    S.entryEdge = { key: 'users>' + entry, src: null, dst: entry, g: { p0: [p[0], -30], p1: [p[0], p[1] - BOX.h / 2 - 3], len: 50 }, acc: 0 };
    s('text', { x: p[0] + 8, y: 8, class: 'axt', fill: 'var(--faint)', 'font-size': 9, text: 'listeners' }, gE);
  }
  for (const id of ids) {
    const n = S.nodes[id], b = nodeBox(id), [x, y] = n.p;
    const g = s('g', { class: 'node' + (n.job ? ' job' : ''), transform: `translate(${x},${y})` }, gN);
    n.halo = s('circle', { class: 'halo', r: 0, fill: 'transparent', opacity: .22 }, g);
    s('rect', { class: 'box', x: -b.w / 2, y: -b.h / 2, width: b.w, height: b.h, rx: 6 }, g);
    n.dot = s('circle', { cx: -b.w / 2 + 9, cy: 0, r: 3.5, fill: 'var(--faint)' }, g);
    s('text', { class: 'name', x: 4, y: 4, text: id }, g);
    n.met = s('text', { class: 'met', x: 0, y: b.h / 2 + 12, text: '' }, g);
    s('title', { text: id }, g);
    n.el = g;
  }
  renderBadges();
}
function nodeEl(id) { return S.nodes[id] && S.nodes[id].el; }
function setNodeClass(id, cls, on) { const e = nodeEl(id); if (e) e.classList.toggle(cls, on); }
function edgeFor(a, b) { return S.edges[a + '>' + b] || S.edges[b + '>' + a]; }

/* ---------- simulation playback ---------- */
async function loadSim(inc) {
  S.sim = null; $('#spark').innerHTML = '';
  try { S.sim = await getJSON('/api/sim/' + encodeURIComponent(inc)); }
  catch (e) { logLocal('warn', 'sim unavailable for ' + inc + ': ' + e.message); return; }
  const svcs = S.sim.services || {};
  let mx = 0, mr = 1;
  for (const v of Object.values(svcs)) { for (const x of v.err || []) mx = Math.max(mx, x); for (const r of v.rps || []) mr = Math.max(mr, r); }
  S.errScale = mx > 1.0001 ? 0.01 : 1; S.maxRps = mr;
  drawSpark(); renderTruthPanel(); setT(S.t);
}
function svcAt(svc, t) {
  const v = S.sim && S.sim.services && S.sim.services[svc]; if (!v) return null;
  const i = clamp(Math.floor(t), 0, (v.rps || v.err || []).length - 1);
  return { rps: v.rps ? v.rps[i] : null, lat: v.lat_ms ? v.lat_ms[i] : null, err: v.err ? v.err[i] * S.errScale : null };
}
function activeLinks(t) { return ((S.sim && S.sim.links) || []).filter(l => t >= (l.start ?? 0) && t < (l.end ?? 1e9)); }
function setT(t) {
  S.t = clamp(t, 0, 599);
  $('#scrub').value = Math.floor(S.t); $('#clock').textContent = clock(S.t);
  const cur = $('#spark .tcur'); if (cur) { cur.setAttribute('x1', S.t); cur.setAttribute('x2', S.t); }
  $('#tinfo').textContent = S.t < 300 ? `page in ${Math.ceil(300 - S.t)}s` : 'after page';
  if (!S.sim) return;
  for (const n of Object.values(S.nodes)) {
    const m = svcAt(n.id, S.t);
    if (!m) { n.met.textContent = n.job ? 'job' : ''; continue; }
    const col = errColor(m.err);
    n.halo.setAttribute('fill', col); n.halo.setAttribute('r', 30 + 26 * Math.log10(1 + (m.rps || 0)) / Math.log10(1 + S.maxRps));
    n.halo.setAttribute('opacity', m.err > 0.01 ? .32 : .14);
    n.dot.setAttribute('fill', col);
    n.met.textContent = `${pct(m.err)} · ${num(m.rps)}/s · ${m.lat == null ? '–' : Math.round(m.lat) + 'ms'}`;
  }
  const act = activeLinks(S.t);
  for (const e of S.edgeList) {
    const l = act.find(l => l.src === e.src && l.dst === e.dst);
    e.el.classList.toggle('fault', !!l); e.fault = l || null;
    if (l && !e.badge) {
      const g = s('g', { class: 'mode-badge', transform: `translate(${e.g.mid[0]},${e.g.mid[1]})` }, $('#g-badges'));
      const txt = String(l.mode || 'link').replace('link:', '') + (l.value != null && l.value <= 1 ? ' ' + Math.round(l.value * 100) + '%' : '');
      s('rect', { x: -txt.length * 3.3 - 5, y: -7, width: txt.length * 6.6 + 10, height: 14, rx: 3 }, g);
      s('text', { x: 0, y: 3, 'text-anchor': 'middle', text: txt }, g);
      e.badge = g;
    } else if (!l && e.badge) { e.badge.remove(); e.badge = null; }
  }
  // flash nodes on scenario events crossing the cursor
  for (const ev of (S.sim.events || [])) if (Math.floor(ev[0]) === Math.floor(S.t) && S.playing) {
    const el = nodeEl(ev[1]); if (el) { el.classList.remove('flash'); void el.getBBox(); el.classList.add('flash'); }
  }
}
function drawSpark() {
  const sv = $('#spark'); sv.innerHTML = '';
  s('rect', { class: 'bgr', x: 0, y: 0, width: 600, height: 34 }, sv);
  const entry = (S.catalog && S.catalog.topology && S.catalog.topology.entry) || 'edge-gw';
  const v = S.sim && S.sim.services && S.sim.services[entry];
  if (v && v.err) {
    let d = 'M0 34';
    v.err.forEach((e, i) => { d += ` L${i} ${34 - clamp(e * S.errScale, 0, 1) * 30}`; });
    s('path', { class: 'area', d: d + ' L599 34 Z' }, sv);
  }
  const pt = (S.sim && S.sim.page_t) || 300;
  s('line', { class: 'page', x1: pt, x2: pt, y1: 0, y2: 34 }, sv);
  for (const ev of (S.sim && S.sim.events) || []) {
    const l = s('line', { class: 'tick', x1: ev[0], x2: ev[0], y1: 0, y2: 6 }, sv);
    s('title', { text: `${clock(ev[0])} ${ev[1]} ${ev[2]}: ${ev[3]}` }, l);
  }
  s('line', { class: 'tcur', x1: S.t, x2: S.t, y1: 0, y2: 34 }, sv);
}
function renderTruthPanel() {
  const side = $('#topo-side'); side.innerHTML = '';
  const inc = S.catalog && (S.catalog.incidents || []).find(i => i.id === S.incident);
  $('#topo-title').textContent = inc ? inc.title || inc.id : (S.incident || '');
  const evs = (S.sim && S.sim.events) || [];
  if (evs.length) side.append(h('div', { class: 'card' }, h('div', { class: 'k' }, 'change / event feed'),
    ...evs.slice(0, 6).map(e => h('div', { class: 'mono ell', title: e[3] }, h('span', { class: 'dim' }, clock(e[0]) + ' '), `${e[1]} · ${short(e[3], 46)}`))));
  S.truthCard = h('div', { class: 'card', style: 'display:none' }); side.append(S.truthCard);
}
function revealTruth() {   // during verification: show where the faults really are
  if (!S.sim || S.truthShown) return; S.truthShown = true;
  const gB = $('#g-badges');
  for (const f of S.sim.faults || []) {
    const svc = Array.isArray(f) ? f[1] : (f.svc || f.service || f.target); if (!S.nodes[svc]) continue;
    const link = !Array.isArray(f) && f.link && edgeFor(svc, f.link);
    if (link) {   // a network fault lives on the link, not on either service
      link.el.classList.add('origin', 'hl');
      const [x1, y1] = pos(svc), [x2, y2] = pos(f.link);
      const g = s('g', { class: 'origin-mark', transform: `translate(${(x1 + x2) / 2},${(y1 + y2) / 2})` }, gB);
      s('path', { d: 'M0 -9 L8 4 L-8 4 Z' }, g); s('text', { x: 12, y: 4, text: `TRUE FAULT · ${f.mode || 'link'}` }, g);
      continue;
    }
    const [x, y] = pos(svc), b = nodeBox(svc);
    const g = s('g', { class: 'origin-mark', transform: `translate(${x - b.w / 2 - 4},${y - b.h / 2 - 4})` }, gB);
    s('path', { d: 'M0 -9 L8 4 L-8 4 Z' }, g); s('text', { x: -18, y: -12, text: 'TRUE FAULT' }, g);
    setNodeClass(svc, 'origin', true);
  }
  if (S.truthCard && S.sim.truth) { S.truthCard.style.display = ''; S.truthCard.replaceChildren(h('div', { class: 'k' }, 'ground truth (revealed to verifier)'), h('div', {}, S.sim.truth)); }
}
function clearOverlays() {
  $('#g-claims').innerHTML = ''; $('#g-badges').innerHTML = '';
  for (const e of S.edgeList) { e.badge = null; e.el.classList.remove('missed', 'focus', 'hl'); }
  for (const id in S.nodes) S.nodes[id].el.classList.remove('missed', 'origin', 'focus', 'hl');
  S.truthShown = false; if (S.truthCard) S.truthCard.style.display = 'none';
  setT(S.t);
}

/* ---------- particles + agent cursor (one rAF loop) ---------- */
const MAXP = 520;
let pool = [];
function initPool() {
  const g = $('#g-particles'); g.innerHTML = ''; pool = [];
  for (let i = 0; i < MAXP; i++) pool.push(s('circle', { r: 2, cx: -50, cy: -50, opacity: 0 }, g));
}
function spawnFor(e, dt) {
  const src = e.src ? svcAt(e.src, S.t) : svcAt(e.dst, S.t), dst = svcAt(e.dst, S.t);
  if (!src || !dst) return;
  const callers = (S.callers[e.dst] || []).length || 1;
  let rate = clamp(Math.sqrt(Math.max(0, e.src ? (dst.rps || 0) / callers : src.rps || 0)) / 2.2, 0.15, 12);
  if (e.fault) rate = clamp(Math.sqrt(src.rps || 0) / 2.2, 0.3, 12);
  e.acc += rate * dt;
  while (e.acc >= 1 && S.particles.length < MAXP) {
    e.acc -= 1;
    let red = Math.random() < (dst.err || 0), die = false, speed = 0.9;
    if (e.fault) {
      const m = String(e.fault.mode || ''); const share = e.fault.value != null && e.fault.value <= 1 ? e.fault.value : 1;
      if (/latency/.test(m)) speed = 0.3; else if (Math.random() < share) { red = true; die = true; }
    }
    S.particles.push({ e, k: 0, v: speed * 90 / Math.max(40, e.g.len), red, die });
  }
}
function frame(now) {
  const dt = Math.min(0.05, (now - (S.lastFrame || now)) / 1000); S.lastFrame = now;
  if (S.playing && S.sim) { let t = S.t + dt * S.speed; if (t >= 599) t = 0; if (Math.floor(t) !== Math.floor(S.t)) setT(t); else S.t = t; }
  // particles
  if (S.sim) { for (const e of S.edgeList) spawnFor(e, dt); if (S.entryEdge) spawnFor(S.entryEdge, dt); }
  let j = 0;
  for (const p of S.particles) {
    p.k += p.v * dt;
    const lim = p.die ? 0.55 : 1;
    if (p.k >= lim + (p.die ? 0.12 : 0)) continue;
    const c = pool[j++]; if (!c) break;
    const kk = Math.min(p.k, lim), g = p.e.g;
    c.setAttribute('cx', lerp(g.p0[0], g.p1[0], kk)); c.setAttribute('cy', lerp(g.p0[1], g.p1[1], kk));
    const fading = p.k > lim ? 1 - (p.k - lim) / 0.12 : 1;
    c.setAttribute('r', p.k > lim ? 2 + (1 - fading) * 5 : 2);
    c.setAttribute('fill', p.red ? '#ff5d6c' : (p.e.job ? '#b48cff' : '#5aa9ff'));
    c.setAttribute('opacity', (p.red ? 0.95 : 0.7) * fading);
  }
  S.particles = S.particles.filter(p => p.k < (p.die ? 0.67 : 1));
  for (let i = j; i < pool.length; i++) if (pool[i].getAttribute('opacity') !== '0') pool[i].setAttribute('opacity', 0);
  // cursor tween
  const cu = S.cursor;
  if (cu.visible && S.cursorEl) {
    const k = easeInOut(clamp((now - cu.t0) / cu.dur, 0, 1));
    cu.x = lerp(cu.fx, cu.tx, k); cu.y = lerp(cu.fy, cu.ty, k);
    S.cursorEl.setAttribute('transform', `translate(${cu.x.toFixed(1)},${cu.y.toFixed(1)})`);
  }
  requestAnimationFrame(frame);
}
function buildCursor() {
  const g = $('#g-cursor'); g.innerHTML = '';
  S.trailEl = s('g', {}, g);
  const c = s('g', { class: 'cursor', opacity: 0 }, g);
  s('circle', { class: 'ring', r: 20 }, c); s('circle', { class: 'ring2', r: 27 }, c);
  for (const [x1, y1, x2, y2] of [[-34, 0, -22, 0], [22, 0, 34, 0], [0, -34, 0, -22], [0, 22, 0, 34]]) s('line', { x1, y1, x2, y2 }, c);
  const lab = s('g', { class: 'clabel', transform: 'translate(30,-40)' }, c);
  S.cursorRect = s('rect', { x: 0, y: -10, height: 17, rx: 3, width: 60 }, lab);
  S.cursorText = s('text', { x: 6, y: 2, text: '' }, lab);
  S.cursorEl = c;
}
function focusPoint(focus) {
  const f = focus || {};
  if (Array.isArray(f.edge) && f.edge.length === 2 && S.nodes[f.edge[0]] && S.nodes[f.edge[1]]) {
    const e = edgeFor(f.edge[0], f.edge[1]); if (e) return e.g.mid;
    const a = pos(f.edge[0]), b = pos(f.edge[1]); return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  }
  const ns = (f.nodes || []).filter(n => S.nodes[n]);
  if (ns.length === 1) return pos(ns[0]);
  if (ns.length > 1 && ns.length <= 3) { const ps = ns.map(pos); return [ps.reduce((a, p) => a + p[0], 0) / ps.length, ps.reduce((a, p) => a + p[1], 0) / ps.length]; }
  return GLOBAL_SPOT;
}
function moveCursor(focus, label) {
  const cu = S.cursor, [tx, ty] = focusPoint(focus);
  if (cu.visible && S.trailEl) {
    const tr = s('line', { class: 'trail', x1: cu.tx, y1: cu.ty, x2: tx, y2: ty }, S.trailEl);
    setTimeout(() => tr.remove(), 4000);
  }
  cu.fx = cu.visible ? cu.x : tx; cu.fy = cu.visible ? cu.y : ty; cu.tx = tx; cu.ty = ty; cu.t0 = performance.now(); cu.dur = 650; cu.visible = true;
  S.cursorEl.setAttribute('opacity', 1);
  S.cursorText.textContent = label; S.cursorRect.setAttribute('width', label.length * 6.1 + 12);
  // label flips left near the right edge
  S.cursorEl.querySelector('.clabel').setAttribute('transform', tx > 640 ? `translate(${-36 - label.length * 6.1},-40)` : 'translate(30,-40)');
  // focus highlight
  document.querySelectorAll('#topo .focus').forEach(e => e.classList.remove('focus'));
  const f = focus || {};
  for (const n of f.nodes || []) setNodeClass(n, 'focus', true);
  if (Array.isArray(f.edge)) { const e = edgeFor(f.edge[0], f.edge[1]); if (e) e.el.classList.add('focus'); }
}
function renderBadges() {
  let g = $('#g-eyes'); if (!g) g = s('g', { id: 'g-eyes' }, $('#g-cursor').parentNode.insertBefore(document.createElementNS(SVGNS, 'g'), $('#g-cursor')));
  g.innerHTML = '';
  for (const [id, n] of Object.entries(S.inspected)) {
    if (!S.nodes[id]) continue;
    const [x, y] = pos(id), b = nodeBox(id);
    const e = s('g', { class: 'eye', transform: `translate(${x + b.w / 2 - 2},${y - b.h / 2 - 2})` }, g);
    s('circle', { r: 8 }, e); s('text', { y: 3, text: '👁' === '' ? '' : String(n) }, e);
    s('title', { text: `agent inspected ${id} ${n}×` }, e);
  }
}

/* ---------- agent stream ---------- */
function streamEl() { return $('#stream'); }
function streamAdd(card) {
  const st = streamEl(); const em = st.querySelector('.empty'); if (em) em.remove();
  st.append(card); if ($('#stream-follow').checked) st.scrollTop = st.scrollHeight;
}
function updateStats() {
  $('#stream-stats').textContent = `${S.nSteps} steps · ${num(S.tokens.in)} in / ${num(S.tokens.out)} out tok · ${(S.latency / 1000).toFixed(1)} s`;
}
function onToolCall(ev) {
  const id = sid(ev.step) || ('s' + (S.nSteps + 1));
  S.nSteps++;
  const focus = ev.focus || {};
  const label = `${id} ${ev.tool || '?'}(${short(argsText(ev.args), 34)})`;
  moveCursor(focus, label);
  for (const n of focus.nodes || []) S.inspected[n] = (S.inspected[n] || 0) + 1;
  renderBadges();
  const card = h('div', { class: 'card pending', id: 'card-' + id },
    h('div', { class: 'ch' }, h('span', { class: 'sid' }, id), h('span', { class: 'tool' }, ev.tool || '?'),
      h('span', { class: 'args' }, '(' + argsText(ev.args) + ')'), h('span', { class: 'meta' }, h('span', { class: 'spinner' }))),
    ev.reason ? h('div', { class: 'why' }, '“' + ev.reason + '”') : null);
  if (S.pendingReasoning.length) {
    card.append(h('details', {}, h('summary', {}, 'reasoning summary'), h('div', { class: 'reasoning' }, S.pendingReasoning.join('\n\n'))));
    S.pendingReasoning = [];
  }
  S.steps[id] = { card, focus, t0: performance.now(), tool: ev.tool };
  streamAdd(card); updateStats();
}
function onToolResult(ev) {
  const id = sid(ev.step), st = S.steps[id];
  if (!st) { onToolCall({ step: ev.step, tool: ev.tool, args: {} }); return onToolResult(ev); }
  st.card.classList.remove('pending');
  const meta = st.card.querySelector('.meta'); meta.textContent = `${((performance.now() - st.t0) / 1000).toFixed(2)} s`;
  const out = String(ev.output ?? '');
  const lines = out.split('\n').length;
  const pre = h('pre', { class: 'out' }, out);
  st.card.append(lines > 6 || out.length > 400 ? h('details', {}, h('summary', {}, `output · ${lines} lines`), pre) : pre);
  if ($('#stream-follow').checked) streamEl().scrollTop = streamEl().scrollHeight;
}
function onAnswer(ev) {
  const card = h('div', { class: 'card answer' }, h('div', { class: 'ch' }, h('span', { class: 'sid' }, '★'), h('b', {}, 'Final answer')));
  if (ev.json) {
    const j = ev.json;
    if (j.root_cause_service) card.append(h('div', { style: 'margin:4px 0 0 32px' }, 'root cause: ', h('b', { class: 'mono' }, j.root_cause_service), j.mechanism ? h('div', { class: 'dim' }, j.mechanism) : null));
    if (Array.isArray(j.actions)) card.append(h('ol', { style: 'margin:4px 0 0 14px;padding-left:32px' }, ...j.actions.map(a => h('li', {}, a))));
    card.append(h('details', {}, h('summary', {}, 'parsed JSON'), h('pre', { class: 'out' }, JSON.stringify(j, null, 2))));
  }
  if (ev.text) card.append(h('details', {}, h('summary', {}, 'full answer text'), h('pre', { class: 'out' }, ev.text)));
  streamAdd(card);
  moveCursor({}, 'answer');
}
function highlightStep(id) {
  const st = S.steps[id];
  document.querySelectorAll('.card.hl').forEach(c => c.classList.remove('hl'));
  document.querySelectorAll('#topo .hl').forEach(c => c.classList.remove('hl'));
  if (!st) { toast('step ' + id + ' not in this stream'); return; }
  st.card.classList.add('hl'); st.card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  for (const n of st.focus.nodes || []) setNodeClass(n, 'hl', true);
  if (Array.isArray(st.focus.edge)) { const e = edgeFor(st.focus.edge[0], st.focus.edge[1]); if (e) e.el.classList.add('hl'); }
  setTimeout(() => { st.card.classList.remove('hl'); document.querySelectorAll('#topo .hl').forEach(c => c.classList.remove('hl')); }, 3000);
}
const stepChip = id => h('span', { class: 'chip step', onclick: ev => { ev.stopPropagation(); highlightStep(sid(id)); } }, sid(id));

/* ---------- verification ---------- */
const STAGES = [['extract', 'Extract'], ['replay', 'Replay ×5'], ['story', 'Story'], ['claims', 'Claims'], ['missed', 'Missed'], ['verdict', 'Verdict']];
function resetVerify() {
  const st = $('#stages'); st.innerHTML = '';
  STAGES.forEach(([k, l], i) => { if (i) st.append(h('span', { class: 'arrowsep' }, '→')); st.append(h('span', { class: 'stage', id: 'stage-' + k }, l)); });
  $('#extract').innerHTML = '<div class="empty sm">waiting</div>'; $('#story').innerHTML = '<div class="empty sm">waiting</div>';
  $('#claims').innerHTML = ''; $('#missed').innerHTML = ''; $('#firstwrong').innerHTML = ''; $('#verdict').innerHTML = '';
  $('#verdict-mini').textContent = ''; $('#verdict-mini').className = 'chip';
  S.replay = { base: null, truth: null, seeds: [] }; drawReplay();
}
function onStage(ev) {
  revealTruth();
  const e = $('#stage-' + ev.name); if (!e) return;
  e.classList.remove('start', 'done'); e.classList.add(ev.status === 'done' ? 'done' : 'start');
  if (ev.status === 'start') e.scrollIntoView({ block: 'nearest' });
}
function onExtract(ev) {
  const box = $('#extract'); box.innerHTML = '';
  const claimed = new Set(ev.claimed || []);
  const acts = Object.entries(ev.actions || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
  if (!acts.length) box.append(h('div', { class: 'empty sm' }, 'no actions extracted'));
  for (const [k, p] of acts) {
    const fill = h('div', { class: 'fill' });
    box.append(h('div', { class: 'bar' + (claimed.has(k) ? ' claimed' : ''), title: k }, h('span', { class: 'lab' }, k), h('div', { class: 'track' }, fill), h('span', {}, pct(p))));
    requestAnimationFrame(() => requestAnimationFrame(() => fill.style.width = clamp(p, 0, 1) * 100 + '%'));
  }
  box.append(h('div', { class: 'roots' }, 'roots: ', h('b', { class: 'mono' }, (ev.roots || []).join(', ') || '–'),
    ev.blamed ? ['  · blamed: ', h('b', { class: 'mono' }, typeof ev.blamed === 'string' ? ev.blamed : JSON.stringify(ev.blamed))] : null));
}
function drawReplay() {
  const sv = $('#replay'); sv.innerHTML = '';
  const X0 = 30, X1 = 352, Y0 = 8, Y1 = 132, xs = t => X0 + (t - 60) / 540 * (X1 - X0), ys = e => Y1 - clamp(e, 0, 1) * (Y1 - Y0);
  s('line', { class: 'ax', x1: X0, x2: X1, y1: Y1, y2: Y1 }, sv); s('line', { class: 'ax', x1: X0, x2: X0, y1: Y0, y2: Y1 }, sv);
  for (const e of [0, .5, 1]) s('text', { class: 'axt', x: X0 - 4, y: ys(e) + 3, 'text-anchor': 'end', text: e * 100 + '%' }, sv);
  for (const t of [60, 180, 300, 420, 540]) s('text', { class: 'axt', x: xs(t), y: Y1 + 11, 'text-anchor': 'middle', text: clock(t).slice(3) }, sv);
  s('line', { class: 'slo', x1: X0, x2: X1, y1: ys(.05), y2: ys(.05) }, sv); s('text', { class: 'axt', x: X1, y: ys(.05) - 3, 'text-anchor': 'end', text: 'SLO 5%' }, sv);
  s('line', { class: 'pg', x1: xs(300), x2: xs(300), y1: Y0, y2: Y1 }, sv); s('text', { class: 'axt', x: xs(300) + 3, y: Y0 + 7, text: 'page · actions applied' }, sv);
  const path = (curve, cls) => { if (!Array.isArray(curve) || !curve.length) return; s('path', { class: cls, d: curve.map((p, i) => (i ? 'L' : 'M') + xs(p[0]).toFixed(1) + ' ' + ys(p[1] * S.errScaleReplay()).toFixed(1)).join(' ') }, sv); };
  path(S.replay.base, 'base'); path(S.replay.truth, 'truth');
  for (const sd of S.replay.seeds) path(sd.curve, 'seed' + (sd.fixed ? ' fixed' : ''));
  const fixed = S.replay.seeds.filter(x => x.fixed).length;
  $('#replay-legend').innerHTML = `<span style="color:var(--bad)">— no action</span> <span style="color:var(--ok)">- - true fix</span> <span style="color:var(--accent)">— AI actions</span>` + (S.replay.seeds.length ? ` · <b>${fixed}/${S.replay.seeds.length}</b> seeds fixed` : '');
}
S.errScaleReplay = () => S._replayScale || 1;
function noteReplayScale(curve) { if (Array.isArray(curve) && curve.some(p => p[1] > 1.0001)) S._replayScale = 0.01; }
function onReplaySeed(ev) { noteReplayScale(ev.curve); S.replay.seeds.push(ev); drawReplay(); }
function onBaseline(ev) {
  if (ev.curve) { noteReplayScale(ev.curve); S.replay.base = ev.curve; }
  if (ev.truth_curve) { noteReplayScale(ev.truth_curve); S.replay.truth = ev.truth_curve; }
  drawReplay();
}
function onInvariant(ev) {
  const box = $('#story'); const em = box.querySelector('.empty'); if (em) em.remove();
  box.append(h('div', { class: 'inv ' + (ev.pass ? 'pass' : 'fail') }, h('span', { class: 'ic' }, ev.pass ? '✓' : '✗'), h('span', { class: 'ck' }, ev.check || '?'), h('span', { class: 'dt' }, ev.detail || '')));
}
function claimTarget(c) {
  c = c || {};
  if (c.src && c.dst) return { edge: [c.src, c.dst] };
  if (c.service) return { nodes: [c.service] };
  return {};
}
function claimSummary(c) {
  if (!c || typeof c !== 'object') return '';
  if (c.kind === 'edge') return `${c.src} → ${c.dst} : ${c.type || ''}`;
  if (c.kind === 'state') return `${c.service} is ${c.state || '?'}`;
  if (c.kind === 'cause') return `root cause = ${c.service}`;
  return short(c, 60);
}
const yn = v => v == null ? h('span', { class: 'na' }, '–') : v ? h('span', { class: 'yes' }, '✓') : h('span', { class: 'no' }, '✗');
function onClaim(ev) {
  const label = ev.label || '?';
  const tgt = claimTarget(ev.claim);
  const tr = h('tr', { onclick: () => { const ev0 = (ev.evidence || [])[0]; if (ev0 != null) highlightStep(sid(ev0)); flashTarget(tgt); } },
    h('td', { class: 'mono dim' }, ev.index ?? ''), h('td', { class: 'ct', title: ev.text || '' }, claimSummary(ev.claim) || short(ev.text, 70)),
    h('td', {}, yn(ev.grounded)), h('td', {}, yn(ev.supported)), h('td', {}, yn(ev.true)),
    h('td', {}, h('span', { class: 'chip lbl-' + label, title: ev.decided_by || '' }, label)), h('td', {}, ...(ev.evidence || []).map(stepChip)));
  $('#claims').append(tr); tr.scrollIntoView({ block: 'nearest' });
  drawClaimOverlay(tgt, label, ev.claim);
}
function drawClaimOverlay(tgt, label, c) {
  const g = $('#g-claims');
  if (tgt.edge) {
    const [a, b] = tgt.edge; if (!S.nodes[a] || !S.nodes[b]) return;
    const e = edgeFor(a, b), geo = e ? e.g : edgeGeom(a, b);
    s('line', { class: 'claim-ov ' + label, x1: geo.p0[0], y1: geo.p0[1], x2: geo.p1[0], y2: geo.p1[1] }, g);
    s('text', { class: 'claim-lbl ' + label, x: geo.mid[0] + 8, y: geo.mid[1] + 14, text: `${label}${c && c.type ? ' · ' + c.type : ''}` }, g);
  } else if (tgt.nodes) {
    const id = tgt.nodes[0]; if (!S.nodes[id]) return;
    const [x, y] = pos(id), b = nodeBox(id);
    s('rect', { class: 'claim-ring ' + label, x: x - b.w / 2 - 5, y: y - b.h / 2 - 5, width: b.w + 10, height: b.h + 10, rx: 9 }, g);
    s('text', { class: 'claim-lbl ' + label, x: x - b.w / 2, y: y - b.h / 2 - 8, text: `${c && c.kind === 'cause' ? 'CAUSE ' : ''}${label}${c && c.state ? ' · ' + c.state : ''}` }, g);
  }
}
function flashTarget(tgt) {
  for (const n of tgt.nodes || []) { setNodeClass(n, 'hl', true); setTimeout(() => setNodeClass(n, 'hl', false), 2500); }
  if (tgt.edge) { const e = edgeFor(tgt.edge[0], tgt.edge[1]); if (e) { e.el.classList.add('hl'); setTimeout(() => e.el.classList.remove('hl'), 2500); } }
}
function onMissed(ev) {
  const a = ev.args || {};
  const tgt = a.src && a.dst ? { edge: [a.src, a.dst] } : a.service ? { nodes: [a.service] } : {};
  $('#missed').append(h('div', { class: 'mc' }, h('b', {}, `never checked: ${ev.tool}(${argsText(a)})`), ' — would show: ', ev.would_show == null ? '–' : short(ev.would_show, 220)));
  const g = $('#g-badges');
  let p = null;
  if (tgt.edge) { const e = edgeFor(tgt.edge[0], tgt.edge[1]); if (e) { e.el.classList.add('missed'); p = e.g.mid; } }
  for (const n of tgt.nodes || []) { setNodeClass(n, 'missed', true); const q = pos(n); p = [q[0] - 40, q[1] + nodeBox(n).h / 2 + 18]; }
  if (p) {
    const t = s('g', { class: 'missed-tag', transform: `translate(${p[0]},${p[1]})` }, g);
    s('rect', { x: 0, y: -8, width: 84, height: 13, rx: 3 }, t); s('text', { x: 4, y: 2, text: 'NEVER CHECKED' }, t);
  }
}
function onFirstWrong(ev) {
  const id = sid(ev.step);
  $('#firstwrong').replaceChildren(h('div', { class: 'callout' }, h('div', { class: 'k' }, 'first wrong step'),
    h('div', {}, id ? stepChip(id) : null, ' ', h('span', { class: 'chip lbl-' + (ev.label || '') }, ev.label || ''), ' ', typeof ev.claim === 'object' ? claimSummary(ev.claim) : (ev.claim || '')),
    ev.would_have_caught_it ? h('div', { class: 'dim', style: 'margin-top:3px' }, 'would have caught it: ', h('span', { class: 'mono' }, typeof ev.would_have_caught_it === 'string' ? ev.would_have_caught_it : JSON.stringify(ev.would_have_caught_it))) : null));
  if (S.steps[id]) S.steps[id].card.classList.add('wrong');
}
function verdictClass(v) { v = String(v || '').toUpperCase(); return v === 'ACCEPT' || v === 'PASS' ? 'ok' : v === 'PARTIAL' ? 'warn' : 'bad'; }
function onVerdict(ev) {
  const cls = verdictClass(ev.verdict), c = ev.counts || {};
  $('#verdict').replaceChildren(h('div', { class: 'big ' + cls }, ev.verdict || '?'),
    h('div', { class: 'facts' },
      'fixed in replay', h('b', {}, ev.fixed_in_replay == null ? '–' : ev.fixed_in_replay ? 'yes' : 'no'),
      'seeds fixed', h('b', {}, Array.isArray(ev.fixed_by_seed) ? `${ev.fixed_by_seed.filter(Boolean).length}/${ev.fixed_by_seed.length}` : '–'),
      'entry err after', h('b', {}, pct(ev.ai_error_rate_after)),
      'clean trace', h('b', {}, ev.clean_trace == null ? '–' : ev.clean_trace ? 'yes' : 'no')),
    h('div', { class: 'facts' }, ...Object.entries(c).flatMap(([k, v]) => [h('span', { class: 'chip lbl-' + k }, k), h('b', {}, v)])));
  $('#verdict-mini').textContent = ev.verdict || ''; $('#verdict-mini').className = 'chip ' + cls;
  $('#verdict').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/* ---------- benchmark ---------- */
function resetBench() {
  S.bench = {}; $('#bench-grid').innerHTML = ''; $('#bench-totals').innerHTML = '';
  const incs = (S.catalog && S.catalog.incidents || []).filter(i => (S.catalog.runs || []).some(r => r.task === i.id));
  for (const i of incs) {
    const runs = (S.catalog.runs || []).filter(r => r.task === i.id).map(r => r.run).sort((a, b) => a - b).slice(0, 3);
    const cells = runs.map(r => (S.bench[i.id + '#' + r] = h('div', { class: 'bcell', title: `${i.id} run ${r}` }, 'run ' + r)));
    while (cells.length < 3) cells.push(h('div', { class: 'bcell', style: 'opacity:.3' }, ''));
    $('#bench-grid').append(h('div', { class: 'brow' }, h('span', { class: 'bn', title: i.title || i.id }, i.id), ...cells));
  }
}
function onBenchRow(ev) {
  const key = ev.task + '#' + ev.run;
  let c = S.bench[key];
  if (!c) { c = S.bench[key] = h('div', { class: 'bcell' }); $('#bench-grid').append(h('div', { class: 'brow' }, h('span', { class: 'bn' }, ev.task), c)); }
  const n = ev.counts || {};
  c.className = 'bcell filled ' + verdictClass(ev.verdict);
  c.replaceChildren(h('b', {}, String(ev.verdict || '?').slice(0, 6)), ` ✓${n.VERIFIED ?? 0} ✗${n.FALSE ?? 0}`, ev.clean ? ' ·c' : '');
  c.title = `${ev.task} run ${ev.run}: ${ev.verdict} ${JSON.stringify(n)}${ev.clean ? ' (clean trace)' : ''}`;
  c.scrollIntoView({ block: 'nearest' });
}
function onBenchTotals(ev) {
  const T = (n, l, cls) => h('div', { class: 'tot ' + (cls || '') }, h('div', { class: 'n' }, n ?? '–'), h('div', { class: 'l' }, l));
  $('#bench-totals').replaceChildren(T(`${ev.done ?? 0}/${ev.total ?? '?'}`, 'runs verified'), T(ev.accept, 'accepted', 'ok'), T(ev.claims, 'claims'),
    T(ev.verified, 'verified', 'ok'), T(ev.false, 'false', 'bad'), T(ev.clean, 'clean traces'));
}

/* ---------- case composer ---------- */
function onDraftText(ev) { $('#case-out').replaceChildren(h('div', { class: 'vh' }, 'AI draft (raw)'), h('pre', { class: 'out' }, ev.text || '')); }
function onCaseReady(ev) {
  const c = ev.case || {}; S.lastCase = c;
  const out = $('#case-out'); out.innerHTML = '';
  out.append(h('div', { class: 'crow' }, h('b', {}, c.title || c.id || 'case'), h('span', { class: 'chip info' }, c.id || ''),
    h('span', { class: 'chip warn' }, 'entry err before ' + pct(ev.entry_err_before)),
    h('button', { class: 'primary', onclick: () => investigateCase(c) }, 'Investigate this case')));
  out.append(h('div', { class: 'vh', style: 'margin-top:6px' }, 'faults'), h('pre', { class: 'out' }, (c.faults || []).map(f => JSON.stringify(f)).join('\n') || '–'));
  out.append(h('div', { class: 'vh' }, 'computed ground truth (actions that fix it)'), h('pre', { class: 'out' }, (ev.ground_truth || []).map(a => Array.isArray(a) ? a.join('  +  ') : JSON.stringify(a)).join('\n') || '–'));
  if (c.truth) out.append(h('div', { class: 'vh' }, 'truth'), h('div', {}, c.truth));
  out.append(h('details', {}, h('summary', {}, 'case JSON'), h('pre', { class: 'out' }, JSON.stringify(c, null, 2))));
  toast('Case ready: ' + (c.id || ''));
}
function onCaseError(ev) { S.jobFailed = true; $('#case-out').append(h('div', { class: 'case-err' }, 'case error: ' + (ev.error || ev.msg || 'unknown'))); }
async function investigateCase(c) {
  if (!c || !c.id) return;
  // a custom case: refresh catalog so it shows in the picker, then select it
  try { S.catalog = await getJSON('/api/catalog'); fillIncidents(); } catch (e) { /* keep old */ }
  if ([...$('#sel-incident').options].some(o => o.value === c.id)) { $('#sel-incident').value = c.id; await selectIncident(c.id); }
  startJob('/api/investigate', { task: c.id }, 'investigate');
}

/* ---------- event log ---------- */
const AGENT_T = new Set(['job_started', 'request_sent', 'reasoning', 'tool_call', 'tool_result', 'response', 'answer']);
const VERIF_T = new Set(['stage', 'extract', 'replay_seed', 'replay_baseline', 'invariant', 'claim', 'missed_check', 'first_wrong', 'verdict']);
function logClass(t, ev) {
  if (t === 'error' || (t === 'log' && ev.level === 'error') || t === 'case_error') return 'err';
  if (AGENT_T.has(t)) return 'agent'; if (VERIF_T.has(t)) return 'verif';
  if (t.startsWith('bench')) return 'bench'; if (t.startsWith('draft') || t.startsWith('case')) return 'draft'; return 'sys';
}
function logEvent(ev) {
  const t = String(ev.type || '?');
  const rest = Object.assign({}, ev); delete rest.type; delete rest.job; delete rest.seq;
  const line = h('div', { class: 'ev ' + logClass(t, ev) }, h('span', { class: 'sq' }, ev.seq ?? ''), h('span', { class: 'ty' }, t), ' ', JSON.stringify(rest));
  line.dataset.k = (t + ' ' + line.textContent).toLowerCase();
  const f = $('#log-filter').value.trim().toLowerCase(); if (f && !line.dataset.k.includes(f)) line.style.display = 'none';
  const box = $('#tab-log'); box.append(line);
  while (box.childElementCount > 3000) box.firstChild.remove();
  $('#log-count').textContent = ++S.logCount;
  if (!$('#log-pause').checked) box.scrollTop = box.scrollHeight;
}
function logLocal(level, msg) { logEvent({ type: 'log', seq: '·', level, msg, local: true }); }

/* ---------- SSE dispatch ---------- */
const HANDLERS = {
  job_started: ev => { S.jobKind = ev.kind || S.jobKind; },
  reasoning: ev => { if (ev.text) S.pendingReasoning.push(ev.text); },
  request_sent: ev => { const st = streamEl(); if (st.querySelector('.card')) streamAdd(h('div', { class: 'xchg' }, `exchange ${ev.exchange ?? ''} · ${ev.n_input_items ?? '?'} input items → model`)); },
  tool_call: onToolCall, tool_result: onToolResult,
  response: ev => { const u = ev.usage || {}; S.tokens.in += u.input_tokens || 0; S.tokens.out += u.output_tokens || 0; S.latency += ev.latency_ms || 0; updateStats(); },
  answer: onAnswer, stage: onStage, extract: onExtract, replay_seed: onReplaySeed, replay_baseline: onBaseline,
  invariant: onInvariant, claim: onClaim, missed_check: onMissed, first_wrong: onFirstWrong, verdict: onVerdict,
  bench_row: onBenchRow, bench_totals: onBenchTotals,
  draft_text: onDraftText, case_ready: onCaseReady, case_error: onCaseError,
  error: ev => { S.jobFailed = true; toast(ev.msg || 'error', true); },
  log: ev => { if (ev.level === 'error') toast(ev.msg, true); },
  done: () => finishJob(S.jobFailed ? 'error' : 'done'),
};
function dispatch(ev) {
  logEvent(ev);
  const fn = HANDLERS[ev.type];
  if (fn) { try { fn(ev); } catch (e) { console.warn('handler failed', ev.type, e); logLocal('error', `ui handler ${ev.type}: ${e.message}`); } }
}
function setStatus(st) { S.status = st; const p = $('#pill'); p.textContent = st; p.className = 'pill ' + st; setButtons(st === 'running'); }
function setButtons(busy) { for (const id of ['btn-replay', 'btn-live', 'btn-verify', 'btn-bench', 'btn-draft']) $('#' + id).disabled = busy; }
function finishJob(st) { if (S.es) { S.es.close(); S.es = null; } setStatus(st); if (S.cursorEl && st === 'done') S.cursorText && (S.cursorText.textContent = 'done'); }
function resetRun(kind) {
  S.jobFailed = false;
  S.steps = {}; S.inspected = {}; S.pendingReasoning = []; S.tokens = { in: 0, out: 0 }; S.latency = 0; S.nSteps = 0;
  if (kind !== 'bench' && kind !== 'draft') {
    streamEl().innerHTML = ''; updateStats(); resetVerify(); clearOverlays(); renderBadges();
    S.cursor.visible = false; if (S.cursorEl) S.cursorEl.setAttribute('opacity', 0); if (S.trailEl) S.trailEl.innerHTML = '';
  }
  if (kind === 'bench') { resetBench(); switchTab('bench'); }
  if (kind === 'draft') switchTab('compose');
}
async function startJob(url, body, kind) {
  if (S.status === 'running') return;
  resetRun(kind);
  if (kind === 'replay' || kind === 'investigate' || kind === 'verify') { setT(300); }
  setStatus('running'); S.jobStart = performance.now();
  let r;
  try { r = await postJSON(url, body); } catch (e) { toast(e.message, true); logLocal('error', e.message); setStatus('error'); return; }
  S.job = r && (r.job || r.id); $('#job-id').textContent = 'job ' + S.job;
  if (!S.job) { setStatus('error'); toast('server returned no job id', true); return; }
  const es = new EventSource('/api/stream/' + encodeURIComponent(S.job)); S.es = es;
  es.onmessage = m => { let ev; try { ev = JSON.parse(m.data); } catch (e) { logLocal('warn', 'unparsable SSE data: ' + short(m.data, 120)); return; } dispatch(ev); };
  es.onerror = () => { if (S.es === es && S.status === 'running') { es.close(); S.es = null; logLocal('error', 'stream disconnected before the job completed'); setStatus('error'); } };
}
setInterval(() => { if (S.status === 'running') $('#elapsed').textContent = ((performance.now() - S.jobStart) / 1000).toFixed(1) + 's'; }, 100);

/* ---------- controls / init ---------- */
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('on', p.id === 'tab-' + name));
  document.querySelectorAll('.tabtools').forEach(p => p.classList.toggle('on', p.dataset.for === name));
}
function fillIncidents() {
  const sel = $('#sel-incident'), cur = sel.value; sel.innerHTML = '';
  for (const i of (S.catalog.incidents || [])) sel.append(h('option', { value: i.id, title: i.title || '' }, `${i.id}${i.kind === 'custom' ? ' (custom)' : ''}`));
  if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
}
function fillRuns() {
  const sel = $('#sel-run'); sel.innerHTML = '';
  const runs = (S.catalog.runs || []).filter(r => r.task === S.incident).sort((a, b) => a.run - b.run);
  for (const r of runs) {
    const c = r.counts || {};
    sel.append(h('option', { value: r.run }, `run ${r.run} · ${r.verdict || '?'} · ${r.n_steps ?? '?'} steps · ✓${c.VERIFIED ?? 0} ✗${c.FALSE ?? 0}`));
  }
  if (!runs.length) sel.append(h('option', { value: '' }, 'no saved runs'));
  const has = runs.length > 0;
  $('#btn-replay').disabled = !has || S.status === 'running'; $('#btn-verify').disabled = !has || S.status === 'running';
}
async function selectIncident(id) {
  S.incident = id; fillRuns(); clearOverlays();
  try { localStorage.setItem('rp.incident', id); } catch (e) { /* no storage */ }
  await loadSim(id);
}
function wire() {
  $('#sel-incident').addEventListener('change', e => selectIncident(e.target.value));
  $('#btn-replay').onclick = () => startJob('/api/replay', { task: S.incident, run: +$('#sel-run').value || 1, speed: 1.0 }, 'replay');
  $('#btn-live').onclick = () => { if (confirm(`Start a NEW live agent run on ${S.incident}? This makes real model API calls.`)) startJob('/api/investigate', { task: S.incident }, 'investigate'); };
  $('#btn-verify').onclick = () => startJob('/api/verify', { task: S.incident, run: +$('#sel-run').value || 1 }, 'verify');
  $('#btn-bench').onclick = () => startJob('/api/bench', { track: 'investigation' }, 'bench');
  $('#btn-new').onclick = () => { switchTab('compose'); $('#case-text').focus(); };
  $('#btn-draft').onclick = () => { const d = $('#case-text').value.trim(); if (!d) { toast('Describe the incident first', true); return; } startJob('/api/draft', { description: d }, 'draft'); };
  $('#btn-play').onclick = () => { S.playing = !S.playing; $('#btn-play').textContent = S.playing ? '❚❚' : '▶'; };
  $('#scrub').addEventListener('input', e => { setT(+e.target.value); });
  $('#speed').onchange = e => { S.speed = +e.target.value; };
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));
  $('#log-filter').addEventListener('input', e => {
    const f = e.target.value.trim().toLowerCase();
    for (const l of $('#tab-log').children) l.style.display = !f || l.dataset.k.includes(f) ? '' : 'none';
  });
  $('#log-clear').onclick = () => { $('#tab-log').innerHTML = ''; S.logCount = 0; $('#log-count').textContent = 0; };
  $('#btn-theme').onclick = () => {
    const r = document.documentElement, nt = r.dataset.theme === 'light' ? 'dark' : 'light'; r.dataset.theme = nt;
    try { localStorage.setItem('rp.theme', nt); } catch (e) { /* no storage */ }
  };
  document.addEventListener('keydown', e => { if (e.target.matches('input,textarea,select')) return; if (e.code === 'Space') { e.preventDefault(); $('#btn-play').click(); } });
}
async function init() {
  try { const th = localStorage.getItem('rp.theme'); if (th) document.documentElement.dataset.theme = th; } catch (e) { /* no storage */ }
  wire(); resetVerify(); switchTab('log'); initPool(); buildCursor();
  $('#btn-play').textContent = '❚❚';
  try { S.catalog = await getJSON('/api/catalog'); }
  catch (e) { toast('Cannot reach the server: ' + e.message, true); logLocal('error', 'catalog: ' + e.message); setStatus('error'); requestAnimationFrame(frame); return; }
  buildTopology(S.catalog.topology); initPool(); buildCursor();
  fillIncidents();
  let want = null; try { want = localStorage.getItem('rp.incident'); } catch (e) { /* no storage */ }
  const ids = (S.catalog.incidents || []).map(i => i.id);
  const first = ids.includes(want) ? want : ids.includes('16-link-partition') ? '16-link-partition' : ids[0];
  if (first) { $('#sel-incident').value = first; await selectIncident(first); }
  resetBench();
  logLocal('info', `catalog: ${ids.length} incidents, ${(S.catalog.runs || []).length} saved runs, ${(S.catalog.tools || []).length} tools`);
  requestAnimationFrame(frame);
}
init();
