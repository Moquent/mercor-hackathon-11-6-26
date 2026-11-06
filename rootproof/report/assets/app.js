'use strict';
(function () {
  const D = JSON.parse(document.getElementById('data').textContent);
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const pct = (x, d) => (x == null ? '–' : (100 * x).toFixed(d || 0) + '%');
  const num = (x, d) => (x == null ? '–' : Number(x).toFixed(d || 0));
  const PAGE_T = 300;
  const ONSET_ERR = 0.05;
  // Simulated second 0 is 14:00:00 on the packet clock, so the page at 300 s is 14:05:00.
  const clockAt = (t) => {
    const s = 14 * 3600 + Math.round(t);
    const p = (n) => String(n).padStart(2, '0');
    return p(Math.floor(s / 3600) % 24) + ':' + p(Math.floor(s / 60) % 60) + ':' + p(s % 60);
  };
  const vcls = (v) => (v ? 'v-' + String(v).toLowerCase() : 'v-none');
  const vword = (v) => '<span class="' + vcls(v) + '">' + esc(v || '–') + '</span>';

  /* ---------- theme ---------- */
  const root = document.documentElement;
  const isDark = () => {
    const set = root.getAttribute('data-theme');
    return set ? set === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  };
  const paintTheme = () => { $('#theme').textContent = isDark() ? 'Light' : 'Dark'; };
  $('#theme').addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('rp-theme', next); } catch (e) { /* private window */ }
    paintTheme();
  });
  try { const saved = localStorage.getItem('rp-theme'); if (saved) root.setAttribute('data-theme', saved); } catch (e) { /* ignore */ }
  try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paintTheme); } catch (e) { /* old browsers */ }
  paintTheme();

  /* ---------- small helpers ---------- */
  const valAt = (s, arr, t) => { const i = s.t.indexOf(t); return arr[i >= 0 ? i : arr.length - 1]; };
  // Onset = first time the entry service (first topology node) errors above the threshold.
  // Other services can carry baseline errors from t=0, so they are not used.
  const onsetOf = (t) => {
    const s = t.series[D.topology.nodes[0]];
    if (!s) return null;
    const i = s.err.findIndex((v) => v != null && v > ONSET_ERR);
    return i >= 0 ? s.t[i] : null;
  };
  const sr = (label, value, body) => '<div class="sr"><div class="srh"><span>' + label + '</span><b>' + value + '</b></div>' + body + '</div>';

  function spark(xs, ys, o) {
    o = o || {};
    const w = 300, h = 44;
    const vals = ys.filter((v) => v != null);
    if (!vals.length || !xs.length) return '<p class="nodata">No data.</p>';
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const lo = o.ymin != null ? o.ymin : Math.min.apply(null, vals);
    let hi = o.ymax != null ? o.ymax : Math.max.apply(null, vals);
    if (hi === lo) hi = lo + 1;
    const X = (x) => 2 + ((x - x0) / ((x1 - x0) || 1)) * (w - 4);
    const Y = (y) => h - 3 - ((y - lo) / (hi - lo)) * (h - 6);
    let d = '', pen = false;
    xs.forEach((x, i) => {
      const y = ys[i];
      if (y == null) { pen = false; return; }
      d += (pen ? 'L' : 'M') + X(x).toFixed(1) + ',' + Y(y).toFixed(1);
      pen = true;
    });
    const marks = (o.marks || []).map((m) => '<line class="mark ' + (m.cls || '') + '" x1="' + X(m.x).toFixed(1) + '" x2="' + X(m.x).toFixed(1) + '" y1="0" y2="' + h + '"/>').join('');
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="Sparkline">' + marks + '<path class="sp" d="' + d + '"/></svg>';
  }

  /* ---------- routing ---------- */
  const PAGES = ['overview', 'incidents', 'real', 'method'];
  function route() {
    const parts = location.hash.replace(/^#\/?/, '').split('/');
    const page = PAGES.indexOf(parts[0]) >= 0 ? parts[0] : 'overview';
    $$('section.page').forEach((s) => s.classList.toggle('on', s.id === page));
    $$('.nav a').forEach((a) => a.classList.toggle('on', a.dataset.nav === page));
    if (page === 'incidents') showIncident(parts[1] || D.tasks[0].id);
    window.scrollTo(0, 0);
  }

  /* ---------- overview ---------- */
  function renderOverview() {
    const s = D.stats, rc = D.rcaeval.fresh;
    const ci = rc.ci95 && rc.ci95.ai_service_and_fault_acc;
    const rows = D.tasks.map((t) =>
      '<tr><td><a href="#incidents/' + esc(t.id) + '">' + esc(t.title) + '</a></td>' +
      t.verdicts.map((v) => '<td>' + vword(v) + '</td>').join('') +
      '<td>' + vword(t.majority) + '</td></tr>').join('');
    $('#overview').innerHTML =
      '<h1>Results</h1>' +
      '<p class="lede">An AI proposes a fix for each incident. We run the fix in the simulator and check whether it works.</p>' +
      '<div class="kpis">' +
        '<div class="kpi"><b>' + s.first_accept + ' of ' + s.tasks + '</b><span>first answers accepted</span></div>' +
        '<div class="kpi"><b>' + s.runs_accept + ' of ' + s.runs + '</b><span>accepted across all runs</span></div>' +
        '<div class="kpi"><b>' + pct(s.chain_precision) + '</b><span>named causal steps that are real</span></div>' +
        '<div class="kpi"><b>' + pct(rc.ai_service_and_fault_acc) + '</b><span>real cases with service and fault right' +
          (ci ? ' (95% CI ' + pct(ci[0]) + ' to ' + pct(ci[1]) + ')' : '') + '</span></div>' +
      '</div>' +
      '<h2>Findings</h2>' +
      '<ul class="findings">' +
        '<li>Verdicts match across all five runs on ' + s.stable_tasks + ' of ' + s.tasks + ' incidents.</li>' +
        '<li>On real fresh cases the AI names the right service in ' + pct(rc.ai_service_acc) + ', but the right service and fault in ' + pct(rc.ai_service_and_fault_acc) + '.</li>' +
        '<li>The AI finds ' + pct(s.chain_recall) + ' of the real causal steps, and most of the steps it names are real.</li>' +
      '</ul>' +
      '<h2>Verdicts by run</h2>' +
      '<div class="tablewrap"><table><thead><tr><th>Incident</th>' +
        [1, 2, 3, 4, 5].map((r) => '<th>Run ' + r + '</th>').join('') + '<th>Majority</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }

  /* ---------- graph primitives (shared by both graphs) ---------- */
  const NODE_W = 150, NODE_H = 54, JOB_W = 124, JOB_H = 34;
  const LAYOUT = {
    'edge-gw': [400, 40], 'session-api': [400, 140],
    'docstore': [110, 250], 'meta-index': [300, 250], 'notify-relay': [490, 250], 'player-core': [680, 250],
    'sub-poller': [110, 345], 'msg-cleanup': [490, 345], 'media-cache': [680, 345],
    'vela-origin': [455, 450], 'korra-origin': [610, 450], 'brio-origin': [765, 450],
    'clip-preview': [840, 140], 'clipstream-origin': [840, 250]
  };
  const TYPE = {
    fail: { cls: 'e-fail', mk: 'fail', label: 'Failure propagation' },
    load: { cls: 'e-load', mk: 'load', label: 'Load amplification' },
    src: { cls: 'e-src', mk: 'src', label: 'Source job load' }
  };
  const ROLE_NOTE = {
    'injected fault': 'The injected origin of this incident.',
    victim: 'Errors here come from upstream.',
    'load target': 'Request rate here rose more than 1.5 times above baseline.',
    idle: 'No traffic at page time.',
    healthy: 'No errors and normal load.',
    job: 'A cron or cleanup job whose load lands on a service.'
  };

  // Endpoint on the node's rectangle, on the ray towards (tx, ty).
  function clip(N, tx, ty) {
    const dx = tx - N.x, dy = ty - N.y;
    if (!dx && !dy) return [N.x, N.y];
    const hw = N.w / 2, hh = N.h / 2;
    const s = Math.min(Math.abs(dx) > 1e-9 ? hw / Math.abs(dx) : Infinity, Math.abs(dy) > 1e-9 ? hh / Math.abs(dy) : Infinity);
    return [N.x + dx * s, N.y + dy * s];
  }
  // Quadratic curve from A to B, bent sideways by `bend` px. Reversed pairs bend to opposite sides.
  function geo(A, B, bend) {
    const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
    const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
    const cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
    const p1 = clip(A, cx, cy), p2 = clip(B, cx, cy);
    return 'M' + p1[0].toFixed(1) + ',' + p1[1].toFixed(1) + ' Q' + cx.toFixed(1) + ',' + cy.toFixed(1) + ' ' + p2[0].toFixed(1) + ',' + p2[1].toFixed(1);
  }

  function nodeSvg(N) {
    const x = N.x - N.w / 2, y = N.y - N.h / 2;
    const name = '<text class="nm" x="' + N.x + '" y="' + (N.job ? N.y + 4 : N.y - 3) + '" text-anchor="middle">' + esc(N.id) + '</text>';
    const label = N.job ? '' : '<text class="rl" x="' + N.x + '" y="' + (N.y + 14) + '" text-anchor="middle">' + esc(N.label || '') + '</text>';
    return '<g class="node ' + (N.job ? 'job ' : '') + esc(N.cls || '') + '" data-n="' + esc(N.id) + '" tabindex="0">' +
      '<rect x="' + x + '" y="' + y + '" width="' + N.w + '" height="' + N.h + '" rx="10"/>' + name + label + '</g>';
  }

  function edgeSvg(e, G, pre) {
    const g = geo(G[e.a], G[e.b], e.bend || 0);
    const vis = (e.vis || []).map((v) => '<path class="vis ' + v.cls + '" d="' + g + '"' + (v.mk ? ' marker-end="url(#' + pre + '-' + v.mk + ')"' : '') + '/>').join('');
    return '<g class="edge" data-k="' + esc(e.k) + '" data-a="' + esc(e.a) + '" data-b="' + esc(e.b) + '" tabindex="0"><path class="hit" d="' + g + '"/>' + vis + '</g>';
  }

  function graphSvg(pre, vb, G, deps, edges) {
    const defs = ['fail', 'load', 'src', 'claim', 'unsup', 'dep'].map((m) =>
      '<marker id="' + pre + '-' + m + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path class="mk mk-' + m + '" d="M0,0L10,5L0,10z"/></marker>').join('');
    const depS = deps.map((d) => '<path class="vis e-dep" d="' + geo(G[d.a], G[d.b], 0) + '" marker-end="url(#' + pre + '-dep)"/>').join('');
    return '<svg class="graph ' + pre + '" viewBox="0 0 ' + vb[0] + ' ' + vb[1] + '" role="img" aria-label="Causal graph"><defs>' + defs + '</defs>' +
      '<g>' + depS + '</g><g>' + edges.map((e) => edgeSvg(e, G, pre)).join('') + '</g>' +
      '<g>' + Object.keys(G).map((k) => nodeSvg(G[k])).join('') + '</g></svg>';
  }

  // Delegated click, keyboard and hover handling for one graph canvas. Returns mark(sel).
  function wireGraph(canvas, onSelect) {
    const find = (svg, kind, id) => $$(kind === 'node' ? '.node' : '.edge', svg).find((x) => (kind === 'node' ? x.dataset.n : x.dataset.k) === id);
    canvas.addEventListener('click', (ev) => {
      const el = ev.target.closest && ev.target.closest('.node, .edge');
      if (!el || !canvas.contains(el)) return;
      const kind = el.classList.contains('node') ? 'node' : 'edge';
      onSelect(kind, kind === 'node' ? el.dataset.n : el.dataset.k);
    });
    canvas.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      const el = ev.target.closest && ev.target.closest('.node, .edge');
      if (el) { ev.preventDefault(); el.dispatchEvent(new MouseEvent('click', { bubbles: true })); }
    });
    canvas.addEventListener('pointerover', (ev) => {
      const svg = canvas.querySelector('svg');
      const el = ev.target.closest && ev.target.closest('.node, .edge');
      highlight(svg, el && canvas.contains(el) ? el : null);
    });
    canvas.addEventListener('pointerleave', () => highlight(canvas.querySelector('svg'), null));
    function highlight(svg, el) {
      if (!svg) return;
      $$('.hl', svg).forEach((x) => x.classList.remove('hl'));
      svg.classList.toggle('dim', !!el);
      if (!el) return;
      el.classList.add('hl');
      let ends;
      if (el.classList.contains('node')) {
        const n = el.dataset.n;
        $$('.edge', svg).forEach((e) => { if (e.dataset.a === n || e.dataset.b === n) { e.classList.add('hl'); ends = (ends || []).concat([e.dataset.a, e.dataset.b]); } });
        ends = (ends || []).concat([n]);
      } else {
        ends = [el.dataset.a, el.dataset.b];
      }
      $$('.node', svg).forEach((x) => { if (ends.indexOf(x.dataset.n) >= 0) x.classList.add('hl'); });
    }
    return function mark(sel) {
      const svg = canvas.querySelector('svg');
      if (!svg) return;
      $$('.sel', svg).forEach((x) => x.classList.remove('sel'));
      if (!sel) return;
      const el = find(svg, sel.kind, sel.id);
      if (el) el.classList.add('sel');
    };
  }

  // Ties a graph canvas to an inspector panel. render(kind, id) returns the inspector HTML.
  function interactiveGraph(canvas, insp, render) {
    let sel = null;
    const mark = wireGraph(canvas, (kind, id) => pick(kind, id));
    function show() {
      insp.innerHTML = sel ? render(sel.kind, sel.id) : '<p class="muted">Click a service or an arrow to see its details.</p>';
      $$('[data-node]', insp).forEach((b) => b.addEventListener('click', () => pick('node', b.dataset.node)));
      $$('[data-edge]', insp).forEach((b) => b.addEventListener('click', () => pick('edge', b.dataset.edge)));
    }
    function pick(kind, id) { sel = { kind: kind, id: id }; mark(sel); show(); }
    return {
      sel: () => sel,
      redraw() { mark(sel); },
      reset() { sel = null; mark(null); show(); },
      pick: pick
    };
  }

  /* ---------- simulated incidents ---------- */
  function simModel(t) {
    const topo = D.topology, C = t.chain;
    const jobs = {};
    topo.jobs.forEach((jb) => { jobs[jb[0]] = jb[1]; });
    const calls = {};
    topo.edges.forEach((ed) => { (calls[ed[1]] = calls[ed[1]] || []).push(ed[0]); });
    const callsIt = (a, b) => (calls[a] || []).indexOf(b) >= 0;
    const origins = C.origins || [];
    const G = {};
    topo.nodes.forEach((n) => {
      const st = t.nodes[n] || {};
      let role = 'healthy';
      if (origins.indexOf(n) >= 0) role = 'injected fault';
      else if (st.idle) role = 'idle';
      else if (st.err != null && st.err > ONSET_ERR) role = 'victim';
      else if (st.load_x != null && st.load_x >= 1.5) role = 'load target';
      const pos = LAYOUT[n] || [470, 40];
      G[n] = { id: n, job: false, role: role, st: st, x: pos[0], y: pos[1], w: NODE_W, h: NODE_H,
        cls: 'r-' + role.replace(/ /g, '-'), label: role + (st.err != null ? ' · ' + pct(st.err) : '') };
    });
    Object.keys(jobs).forEach((j) => {
      const pos = LAYOUT[j] || [110, 345];
      G[j] = { id: j, job: true, role: 'job', st: {}, x: pos[0], y: pos[1], w: JOB_W, h: JOB_H, cls: 'r-job' };
    });
    const kind = (a, b) => (jobs[a] !== undefined ? 'src' : callsIt(a, b) ? 'load' : 'fail');
    const E = new Map();
    const get = (a, b) => {
      const k = a + '>' + b;
      if (!E.has(k)) E.set(k, { k: k, a: a, b: b, type: kind(a, b), truth: false, claim: false, unsup: false, p: null });
      return E.get(k);
    };
    (C.true_edges || []).forEach((p) => { get(p[0], p[1]).truth = true; });
    const unsupKeys = {};
    (C.unsupported_edges || []).forEach((p) => { unsupKeys[p[0] + '>' + p[1]] = true; });
    (C.claimed_edges || []).forEach((p) => { const e = get(p[0], p[1]); e.claim = true; e.p = (C.claimed_p || {})[p[0] + '->' + p[1]]; });
    E.forEach((e) => { e.unsup = e.claim && (!!unsupKeys[e.k] || !e.truth); });
    const deps = topo.edges.map((ed) => ({ a: ed[1], b: ed[0] })).concat(topo.jobs.map((jb) => ({ a: jb[0], b: jb[1] })));
    return { G: G, E: Array.from(E.values()), calls: calls, jobs: jobs, origins: origins, deps: deps };
  }

  function simVis(e, mode) {
    const vis = [];
    const showT = e.truth && mode !== 'claim';
    const showC = e.claim && mode !== 'truth';
    if (showT) vis.push({ cls: TYPE[e.type].cls, mk: TYPE[e.type].mk });
    if (showC) vis.push({ cls: e.unsup ? 'e-unsup' : 'e-claim', mk: showT ? null : (e.unsup ? 'unsup' : 'claim') });
    return vis;
  }

  function waterfall(tr, focus) {
    const total = Math.max.apply(null, tr.spans.map((s) => s.start_ms + s.duration_ms).concat([1]));
    const rows = tr.spans.map((s) =>
      '<div class="wf' + (s.status === 'error' ? ' err' : '') + (s.service === focus ? ' focus' : '') + '">' +
        '<div class="wf-name" style="padding-left:' + (s.depth * 12) + 'px" title="' + esc(s.service) + '">' + esc(s.service) + (s.attempts > 1 ? ' ×' + s.attempts : '') + '</div>' +
        '<div class="wf-track"><div class="wf-bar" style="left:' + (s.start_ms / total * 100).toFixed(2) + '%;width:' + Math.max(s.duration_ms / total * 100, 0.6).toFixed(2) + '%"></div></div>' +
        '<div class="wf-ms">' + Math.round(s.duration_ms) + ' ms</div></div>').join('');
    return '<figure class="trace" style="margin:0"><figcaption>Sampled at ' + clockAt(tr.second) + ', illustrative. ' +
      (tr.failed ? 'This request failed.' : 'This request succeeded.') + '</figcaption>' + rows + '</figure>';
  }

  function edgeTags(e) {
    const parts = [];
    if (e.truth) parts.push(e.claim ? 'true, claimed' : 'true');
    else if (e.claim) parts.push('claimed');
    if (e.unsup) parts.push('unsupported');
    return parts.map((p) => '<span class="tag">' + p + '</span>').join(' ');
  }

  function inspectSimNode(t, M, n) {
    const N = M.G[n], st = N.st, s = t.series[n];
    let h = '<span class="pill ' + N.cls + '">' + esc(N.role) + '</span><h3>' + esc(n) + '</h3>' +
      '<p class="muted">' + esc(ROLE_NOTE[N.role] || '') + '</p>';
    if (s) {
      const onset = onsetOf(t);
      const marks = [{ x: PAGE_T, cls: 'page' }];
      if (onset != null) marks.push({ x: onset, cls: 'onset' });
      h += '<h4>Over the run</h4>' +
        sr('Error rate', pct(valAt(s, s.err, PAGE_T)), spark(s.t, s.err, { ymin: 0, ymax: 1, marks: marks })) +
        sr('Latency, ms', num(valAt(s, s.lat_ms, PAGE_T)), spark(s.t, s.lat_ms, { ymin: 0, marks: marks })) +
        sr('Requests per second', num(valAt(s, s.rps, PAGE_T)), spark(s.t, s.rps, { ymin: 0, marks: marks })) +
        '<p class="muted">Dashed line: page at ' + clockAt(PAGE_T) + (onset != null ? '. Red line: first errors at ' + clockAt(onset) + '.' : '.') + '</p>';
    } else if (N.job) {
      h += '<p class="muted">Jobs have no service metrics.</p>';
    }
    h += '<h4>Log</h4><p class="log">' + (t.logs && t.logs[n] ? esc(t.logs[n]) : 'No error logs.') + '</p>';
    const ev = (t.events || []).filter((e) => e[1] === n);
    h += '<h4>Events</h4>' + (ev.length
      ? '<ul class="plain">' + ev.map((e) => '<li><span class="time">' + esc(e[0]) + '</span> ' + esc(e[2]) + ': ' + esc(e[3]) + '</li>').join('') + '</ul>'
      : '<p class="muted">No events.</p>');
    const trs = (t.traces || []).filter((tr) => tr.spans.some((sp) => sp.service === n));
    h += '<h4>Sampled requests through this service</h4>' + (trs.length
      ? trs.slice(0, 3).map((tr) => waterfall(tr, n)).join('')
      : '<p class="muted">No sampled request passes through this service.</p>');
    const down = M.calls[n] || [];
    const up = Object.keys(M.calls).filter((p) => M.calls[p].indexOf(n) >= 0);
    h += '<h4>Calls</h4><p>' + (down.length ? down.map(esc).join(', ') : 'Nothing downstream.') + '</p>';
    h += '<h4>Called by</h4><p>' + (up.length ? up.map(esc).join(', ') : 'Nothing upstream.') + '</p>';
    const ce = M.E.filter((e) => e.a === n || e.b === n);
    h += '<h4>Causal edges</h4>' + (ce.length
      ? '<ul class="plain">' + ce.map((e) => '<li><button type="button" class="link" data-edge="' + esc(e.k) + '">' + esc(e.a) + ' → ' + esc(e.b) + '</button> ' + edgeTags(e) + '</li>').join('') + '</ul>'
      : '<p class="muted">None.</p>');
    return h;
  }

  function inspectSimEdge(t, M, k) {
    const e = M.E.find((x) => x.k === k);
    const A = M.G[e.a], B = M.G[e.b];
    const T = TYPE[e.type];
    const desc = e.type === 'fail' ? e.a + "'s failures reached " + e.b + ', its caller.'
      : e.type === 'load' ? e.a + ' multiplied the request rate on ' + e.b + ' with retries or fallbacks.'
      : 'Job ' + e.a + "'s extra load lands on " + e.b + '.';
    const status = e.unsup ? 'Unsupported' : e.claim ? 'Supported' : 'Not claimed';
    const endpoint = (N) => '<div><button type="button" class="link" data-node="' + esc(N.id) + '">' + esc(N.id) + '</button> ' +
      '<span class="muted">' + (N.job ? '' : 'error ' + pct(N.st.err) + ', load ' + (N.st.load_x == null ? '–' : num(N.st.load_x, 2) + '×')) + '</span></div>';
    return '<span class="pill k-' + e.type + '">' + T.label + '</span><h3>' + esc(e.a) + ' → ' + esc(e.b) + '</h3>' +
      '<p>' + esc(desc) + '</p>' +
      '<dl class="kv"><dt>In the replay</dt><dd>' + (e.truth ? 'Yes' : 'No') + '</dd>' +
      '<dt>Named by the AI</dt><dd>' + (e.claim ? 'Yes' + (e.p != null ? ', probability ' + pct(e.p) : '') : 'No') + '</dd>' +
      '<dt>Support</dt><dd>' + status + '</dd></dl>' +
      '<h4>Ends</h4>' + endpoint(A) + endpoint(B);
  }

  function drawReplay(el, t) {
    const W = 760, H = 270, L = 46, R = 12, TP = 14, B = 28;
    const xs = t.curves.noop.map((p) => p[0]);
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
    const Y = (v) => TP + (1 - v) * (H - TP - B);
    let g = '';
    [0, 0.25, 0.5, 0.75, 1].forEach((v) => {
      g += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(v) + '" y2="' + Y(v) + '"/>' +
        '<text class="ax" x="' + (L - 8) + '" y="' + (Y(v) + 4) + '" text-anchor="end">' + Math.round(v * 100) + '%</text>';
    });
    g += '<rect class="shade" x="' + X(450) + '" y="' + TP + '" width="' + (X(600) - X(450)) + '" height="' + (H - TP - B) + '"/>';
    g += '<text class="ax" x="' + (X(450) + 6) + '" y="' + (TP + 14) + '">judged window</text>';
    for (let s = 60; s <= x1; s += 120) g += '<text class="ax" x="' + X(s) + '" y="' + (H - 8) + '" text-anchor="middle">' + clockAt(s) + '</text>';
    g += '<line class="pg" x1="' + X(PAGE_T) + '" x2="' + X(PAGE_T) + '" y1="' + TP + '" y2="' + (H - B) + '"/>' +
      '<text class="ax" x="' + (X(PAGE_T) + 6) + '" y="' + (H - B - 6) + '">paged</text>';
    const series = [['noop', 's-noop'], ['fix', 's-fix'], ['ai', 's-ai']];
    series.forEach((sv) => {
      const d = t.curves[sv[0]].map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join('');
      g += '<path class="ln ' + sv[1] + '" d="' + d + '"/>';
    });
    g += '<line class="cross" x1="0" x2="0" y1="' + TP + '" y2="' + (H - B) + '"/><rect class="hit" x="' + L + '" y="' + TP + '" width="' + (W - L - R) + '" height="' + (H - TP - B) + '"/>';
    el.innerHTML = '<div class="chart"><svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Entry error rate over time">' + g + '</svg><div class="tip" hidden></div></div>' +
      '<ul class="legend"><li><i class="sw sw-noop"></i>No action</li><li><i class="sw sw-fix"></i>Computed fix</li><li><i class="sw sw-ai"></i>AI\'s actions</li></ul>';
    const chart = el.querySelector('.chart'), svg = chart.querySelector('svg');
    const cross = svg.querySelector('.cross'), tip = chart.querySelector('.tip'), hit = svg.querySelector('.hit');
    hit.addEventListener('pointermove', (ev) => {
      const r = svg.getBoundingClientRect();
      const vx = (ev.clientX - r.left) / r.width * W;
      const v = x0 + (vx - L) / (W - L - R) * (x1 - x0);
      let best = 0;
      xs.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(xs[best] - v)) best = i; });
      const x = xs[best];
      cross.setAttribute('x1', X(x)); cross.setAttribute('x2', X(x)); cross.style.display = 'block';
      const vals = series.map((sv) => t.curves[sv[0]][best][1]);
      const px = X(x) / W;
      tip.hidden = false;
      tip.style.left = (px * 100) + '%';
      tip.style.transform = px > 0.7 ? 'translate(-105%,0)' : 'translate(8px,0)';
      tip.innerHTML = '<b>' + clockAt(x) + '</b><br>No action ' + pct(vals[0]) + '<br>Computed fix ' + pct(vals[1]) + '<br>AI\'s actions ' + pct(vals[2]);
    });
    hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.style.display = 'none'; });
  }

  function showIncident(id) {
    const t = D.tasks.find((x) => x.id === id) || D.tasks[0];
    $$('.ilist a').forEach((a) => a.classList.toggle('on', a.dataset.id === t.id));
    const flips = Math.round(t.flip_rate * t.verdicts.length);
    const checks = (t.first.invariants || []).map((c) =>
      '<li>' + (c.pass === true ? '<span class="v-accept">pass</span>' : c.pass === false ? '<span class="v-reject">fail</span>' : '<span class="v-none">n/a</span>') +
      ' <b>' + esc(c.check) + '</b>: ' + esc(c.detail) + '</li>').join('');
    const certs = (t.first.certificates || []).filter(Boolean).map((c) => '<li>' + esc(c) + '</li>').join('');
    const fixes = t.fixes.length ? t.fixes.map((f) => f.map((a) => '<code>' + esc(a) + '</code>').join(' + ')).join('<br>') : 'No action is needed.';
    $('#detail').innerHTML =
      '<h2 class="ititle">' + esc(t.title) + '</h2>' +
      '<p class="truth">' + esc(t.truth) + '</p>' +
      '<div class="badges">' +
        '<span class="badge">First run ' + vword(t.first.verdict) + '</span>' +
        '<span class="badge">Majority ' + vword(t.majority) + '</span>' +
        '<span class="badge">' + (flips ? 'Differs in ' + flips + ' of ' + t.verdicts.length + ' runs' : 'Same in all runs') + '</span>' +
        '<span class="badge">' + (t.fixed_all_seeds ? 'Fix holds on every seed' : 'Fix fails on some seeds') + '</span>' +
      '</div>' +
      '<h3>Replay</h3><div class="card replay"></div>' +
      '<h3>Causal graph</h3><div class="graphbox"></div>' +
      '<details><summary>What the AI answered</summary><pre class="answer">' + esc(t.first.answer) + '</pre></details>' +
      '<details><summary>Checks on the answer</summary><ul class="checks">' + (checks || '<li class="muted">No checks.</li>') + '</ul>' +
        (certs ? '<ul class="plain">' + certs + '</ul>' : '') + '</details>' +
      '<details><summary>Computed fixes and the tempting wrong fix</summary>' +
        '<p>' + fixes + '</p>' +
        '<p>Tempting wrong fix: <code>' + esc((t.trap_actions || []).join(', ')) + '</code>, verdict ' + vword(t.trap) + '.</p></details>' +
      '<details><summary>Prompt the AI saw</summary><pre>' + esc(t.prompt) + '</pre></details>';
    drawReplay($('#detail .replay'), t);
    mountSim($('#detail .graphbox'), t);
  }

  function mountSim(box, t) {
    const M = simModel(t);
    box.innerHTML =
      '<div class="gtools"><div class="seg" role="group" aria-label="Graph view">' +
        [['truth', 'Truth'], ['claim', 'AI claim'], ['overlay', 'Overlay']].map((m) =>
          '<button type="button" data-mode="' + m[0] + '" aria-pressed="' + (m[0] === 'overlay') + '">' + m[1] + '</button>').join('') +
      '</div></div>' +
      '<ul class="glegend"><li><i class="sw sw-fail"></i>Failure step</li><li><i class="sw sw-load"></i>Load step</li>' +
        '<li><i class="sw sw-src"></i>Job load</li><li><i class="sw sw-claim"></i>AI claim</li><li><i class="sw sw-unsup"></i>Unsupported claim</li></ul>' +
      '<div class="gwrap"><div class="gcanvas" tabindex="-1"></div><aside class="insp"></aside></div>';
    const canvas = $('.gcanvas', box), insp = $('.insp', box);
    let mode = 'overlay';
    const visible = () => M.E.filter((e) => (mode === 'truth' ? e.truth : mode === 'claim' ? e.claim : (e.truth || e.claim)));
    const g = interactiveGraph(canvas, insp, (kind, id) => (kind === 'node' ? inspectSimNode(t, M, id) : inspectSimEdge(t, M, id)));
    function draw() {
      canvas.innerHTML = graphSvg('sim', [940, 500], M.G, M.deps, visible().map((e) => ({ k: e.k, a: e.a, b: e.b, bend: 24, vis: simVis(e, mode) })));
      g.redraw();
    }
    box.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
      mode = b.dataset.mode;
      box.querySelectorAll('[data-mode]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      draw();
      const s = g.sel();
      if (!s || (s.kind === 'edge' && !visible().some((e) => e.k === s.id))) g.reset(); else g.redraw();
    }));
    draw();
    g.reset();
  }

  /* ---------- incidents shell ---------- */
  function renderIncidents() {
    $('#incidents').innerHTML =
      '<h1>Incidents</h1>' +
      '<p class="lede">Pick an incident to see the replay of its fix and the causal graph behind it.</p>' +
      '<div class="inc"><nav class="ilist">' +
        D.tasks.map((t, i) => '<a href="#incidents/' + esc(t.id) + '" data-id="' + esc(t.id) + '"><span><span class="n">' + (i + 1) + '</span>' + esc(t.title) + '</span>' + vword(t.first.verdict) + '</a>').join('') +
      '</nav><div id="detail"></div></div>';
  }

  /* ---------- real data ---------- */
  const SPLITS = { dev: { n: '2', label: 'Dev' }, test: { n: '1', label: 'Test' }, fresh: { n: '3', label: 'Fresh' } };
  const SPLIT_NOTE = {
    dev: 'Instance 2 (dev).',
    test: 'Instance 1. The rules were tuned on this split, so its numbers are optimistic.',
    fresh: 'Instance 3. This split was never looked at while tuning the rules.'
  };
  const METRICS = [
    ['ai_service_acc', 'Right service'],
    ['ai_service_and_fault_acc', 'Right service and fault'],
    ['baseline_ac1', 'Baseline, top 1'],
    ['baseline_ac3', 'Baseline, top 3'],
    ['verifier_accuracy', 'Verifier accuracy'],
    ['verifier_flag_recall', 'Wrong answers caught'],
    ['pass_precision', 'PASS verdicts that are right']
  ];
  let split = 'fresh';
  const cellScore = (s, m) => {
    const ci = s.ci95 && s.ci95[m];
    return '<td>' + pct(s[m]) + (ci ? '<span class="ci">' + pct(ci[0]) + ' to ' + pct(ci[1]) + '</span>' : '') + '</td>';
  };

  function renderReal() {
    const R = D.rcaeval;
    const keys = ['dev', 'test', 'fresh'];
    $('#real').innerHTML =
      '<h1>Real data</h1>' +
      '<p class="lede">How the AI does on public RCAEval cases with real microservice faults, with the verifier\'s verdict on each.</p>' +
      '<h2>Scores</h2>' +
      '<div class="tablewrap"><table class="scores"><thead><tr><th>Measure</th>' +
        keys.map((k) => '<th>' + SPLITS[k].label + ' (instance ' + SPLITS[k].n + ')</th>').join('') + '</tr></thead><tbody>' +
        '<tr><td>Cases</td>' + keys.map((k) => '<td>' + R[k].cases + '</td>').join('') + '</tr>' +
        METRICS.map((m) => '<tr><td>' + m[1] + '</td>' + keys.map((k) => cellScore(R[k], m[0])).join('') + '</tr>').join('') +
      '</tbody></table></div>' +
      '<p class="muted small-note">Ranges are 95% confidence intervals.</p>' +
      '<h2>Cases</h2>' +
      '<div class="seg" id="splitseg">' + keys.map((k) => '<button type="button" data-split="' + k + '">' + SPLITS[k].label + '</button>').join('') + '</div>' +
      '<p class="muted" id="splitnote"></p>' +
      '<div class="tablewrap"><table class="cases"><thead><tr><th>Case</th><th>True root</th><th>Fault</th><th>AI root</th><th>AI fault</th><th>Verifier</th></tr></thead><tbody id="casebody"></tbody></table></div>' +
      '<div id="casepanel"></div>';
    $('#splitseg').addEventListener('click', (e) => {
      const b = e.target.closest('[data-split]');
      if (!b) return;
      split = b.dataset.split;
      $('#casepanel').innerHTML = '';
      renderCases();
    });
    $('#casebody').addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-case]');
      if (tr) pickCase(tr.dataset.case);
    });
    $('#casebody').addEventListener('keydown', (e) => {
      const tr = e.target.closest('tr[data-case]');
      if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pickCase(tr.dataset.case); }
    });
    renderCases();
  }

  let caseSel = null;
  function renderCases() {
    const n = SPLITS[split].n;
    $$('#splitseg [data-split]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.split === split)));
    $('#splitnote').textContent = SPLIT_NOTE[split];
    const cases = Object.keys(D.rcaeval_cases).filter((c) => c.slice(-(n.length + 1)) === '_' + n).sort();
    $('#casebody').innerHTML = cases.map((c) => {
      const r = D.rcaeval_cases[c].row;
      return '<tr data-case="' + esc(c) + '" tabindex="0"' + (c === caseSel ? ' class="on"' : '') + '>' +
        '<td>' + esc(c) + '</td><td>' + esc(r.truth_service) + '</td><td>' + esc(r.truth_fault) + '</td>' +
        '<td>' + esc(r.ai_service) + '</td><td>' + esc(r.ai_fault) + '</td>' +
        '<td class="' + (r.verifier === 'PASS' ? 'v-accept' : 'v-partial') + '">' + esc(r.verifier) + '</td></tr>';
    }).join('');
  }

  function pickCase(c) {
    caseSel = c;
    renderCases();
    showRealCase(c);
  }

  function realModel(c) {
    const C = D.rcaeval_cases[c], r = C.row;
    const deps = D.rcaeval.deps;
    const svcs = new Set(Object.keys(deps));
    Object.keys(deps).forEach((p) => deps[p].forEach((s) => svcs.add(s)));
    [r.truth_service, r.ai_service].concat(r.ai_path || []).forEach((s) => { if (s) svcs.add(s); });
    const incoming = new Set();
    Object.keys(deps).forEach((p) => deps[p].forEach((s) => incoming.add(s)));
    const all = Array.from(svcs).sort();
    const roots = all.filter((s) => !incoming.has(s));
    const root = roots.indexOf('frontend') >= 0 ? 'frontend' : (roots[0] || all[0]);
    const depth = {};
    depth[root] = 0;
    const q = [root];
    while (q.length) {
      const p = q.shift();
      (deps[p] || []).forEach((cl) => { if (!(cl in depth)) { depth[cl] = depth[p] + 1; q.push(cl); } });
    }
    let maxD = Math.max.apply(null, Object.values(depth).concat([0]));
    all.forEach((s) => { if (!(s in depth)) depth[s] = maxD + 1; });
    maxD = Math.max.apply(null, Object.values(depth));
    const layers = {};
    all.forEach((s) => { (layers[depth[s]] = layers[depth[s]] || []).push(s); });
    const W = 1200;
    const G = {};
    Object.keys(layers).forEach((d) => {
      const list = layers[d];
      list.forEach((s, i) => {
        const tags = [];
        if (s === r.truth_service) tags.push('true root');
        if (s === r.ai_service) tags.push("AI's root");
        const cls = s === r.truth_service && s === r.ai_service ? 'r-both' : s === r.truth_service ? 'r-true' : s === r.ai_service ? 'r-ai' : '';
        G[s] = { id: s, job: false, x: (i + 0.5) * W / list.length, y: 70 + Number(d) * 120, w: 160, h: NODE_H, cls: cls, label: tags.join(', ') };
      });
    });
    const H = 70 + maxD * 120 + 80;
    const edges = [];
    Object.keys(deps).forEach((p) => (deps[p] || []).forEach((cl) => {
      if (G[p] && G[cl]) edges.push({ k: 'dep:' + p + '>' + cl, a: p, b: cl, bend: 0, vis: [{ cls: 'e-dep', mk: 'dep' }], kind: 'dep' });
    }));
    const path = r.ai_path || [];
    for (let i = 0; i < path.length - 1; i++) {
      edges.push({ k: 'path:' + path[i] + '>' + path[i + 1], a: path[i], b: path[i + 1], bend: 26, vis: [{ cls: 'e-claim', mk: 'claim' }], kind: 'path' });
    }
    return { G: G, W: W, H: H, edges: edges, root: root };
  }

  function inspectRealNode(c, M, s) {
    const C = D.rcaeval_cases[c], f = C.features, r = C.row;
    const inj = f.inject_minute;
    const roles = [];
    if (s === r.truth_service) roles.push('True root cause.');
    if (s === r.ai_service) roles.push("The AI's named root.");
    if ((r.ai_path || []).indexOf(s) >= 0) roles.push("On the AI's claimed path.");
    let h = '<h3>' + esc(s) + '</h3><p class="muted">' + esc(roles.join(' ') || 'No role in the answer.') + '</p>';
    const mets = (f.metrics && f.metrics[s]) || {};
    const marks = [{ x: inj, cls: 'onset' }];
    const rows = Object.keys(mets).filter((m) => mets[m] && mets[m].some((v) => v != null)).map((m) => {
      const arr = mets[m];
      const xs = arr.map((_, i) => i);
      return sr(esc(m), '', spark(xs, arr, { marks: marks }));
    });
    h += '<h4>Metrics per minute</h4>' + (rows.length ? rows.join('') : '<p class="muted">No metrics for this service.</p>');
    const logs = f.error_logs && f.error_logs[s];
    if (logs) h += '<h4>Error log lines per minute</h4>' + sr('Count', String(logs.reduce((a, b) => a + b, 0)) + ' total', spark(logs.map((_, i) => i), logs, { ymin: 0, marks: marks }));
    const checks = (r.checks || []).filter((ck) => (ck.detail || '').indexOf(s) >= 0);
    h += '<h4>Checks on this service</h4>' + (checks.length
      ? '<ul class="checks">' + checks.map((ck) => '<li>' + (ck.pass ? '<span class="v-accept">pass</span>' : '<span class="v-partial">flag</span>') + ' <b>' + esc(ck.check) + '</b>: ' + esc(ck.detail) + '</li>').join('') + '</ul>'
      : '<p class="muted">No check names this service.</p>');
    return h;
  }

  function inspectRealEdge(c, M, k) {
    const C = D.rcaeval_cases[c], f = C.features;
    const e = M.edges.find((x) => x.k === k);
    const inj = f.inject_minute;
    const rel = e.kind === 'dep'
      ? e.a + ' calls ' + e.b + '.'
      : "In the AI's claimed path, " + e.a + "'s failures reach " + e.b + '.';
    let h = '<span class="pill ' + (e.kind === 'dep' ? 'k-src' : 'k-claim') + '">' + (e.kind === 'dep' ? 'Call' : "AI's claimed step") + '</span>' +
      '<h3>' + esc(e.a) + ' → ' + esc(e.b) + '</h3><p>' + esc(rel) + '</p>';
    const tr = C.traces && C.traces.edges
      ? C.traces.edges.find((x) => (x.from === e.a && x.to === e.b) || (x.from === e.b && x.to === e.a))
      : null;
    if (!C.traces) {
      h += '<p class="muted">Per-minute call data is not available for this split.</p>';
    } else if (!tr) {
      h += '<p class="muted">No per-minute call data for this edge.</p>';
    } else {
      const xs = tr.calls.map((_, i) => i);
      const marks = [{ x: inj, cls: 'onset' }];
      h += '<h4>Per minute</h4>' +
        sr('Calls', String(tr.calls.reduce((a, b) => a + (b || 0), 0)) + ' total', spark(xs, tr.calls, { ymin: 0, marks: marks })) +
        sr('p90 latency, ms', num(tr.p90_ms && tr.p90_ms[inj]) + ' at injection', spark(xs, tr.p90_ms || [], { ymin: 0, marks: marks }));
    }
    return h;
  }

  function showRealCase(c) {
    const C = D.rcaeval_cases[c], r = C.row;
    $('#casepanel').innerHTML =
      '<h2>' + esc(c) + '</h2>' +
      '<p class="muted">True root ' + esc(r.truth_service) + ' (' + esc(r.truth_fault) + '). The AI named ' + esc(r.ai_service) + ' (' + esc(r.ai_fault) + '). Verifier: ' + esc(r.verifier) + '.</p>' +
      '<ul class="glegend"><li><i class="sw sw-fail"></i>Call</li><li><i class="sw sw-claim"></i>AI claimed path</li>' +
        '<li>Red ring: true root</li><li>Amber ring: AI root</li></ul>' +
      (C.traces ? '<p class="muted">Call counts and p90 latency per minute come from the trace export. Error counts are not recorded there.</p>' : '') +
      '<div class="gwrap wide"><div class="gcanvas"></div><aside class="insp"></aside></div>';
    const M = realModel(c);
    const canvas = $('#casepanel .gcanvas'), insp = $('#casepanel .insp');
    const g = interactiveGraph(canvas, insp, (kind, id) => (kind === 'node' ? inspectRealNode(c, M, id) : inspectRealEdge(c, M, id)));
    canvas.innerHTML = graphSvg('real', [M.W, M.H], M.G, [], M.edges);
    g.reset();
  }

  /* ---------- method ---------- */
  function renderMethod() {
    const s = D.stats;
    $('#method').innerHTML =
      '<h1>Method</h1>' +
      '<p class="lede">How each answer is produced, replayed and scored.</p>' +
      '<div class="method"><ol>' +
        '<li>Each incident is a small simulated service system with an injected fault, logs and an event timeline. The AI sees what an on-call engineer would see.</li>' +
        '<li>The AI names a root cause and actions. We apply those actions in the simulator and measure the entry error rate in a fixed judged window.</li>' +
        '<li>A verifier runs checks on the answer, such as whether the named service is on the request path and whether it changed during the incident.</li>' +
        '<li>Each incident is run five times. The first run is the official verdict. The other runs show how often the verdict changes.</li>' +
        '<li>The causal graph in the answer is compared with the graph the replay produces. A separate judge answers one yes or no question per candidate step.</li>' +
        '<li>RCAEval is a public set of real microservice faults. The same pipeline runs on each case.</li>' +
      '</ol></div>' +
      '<h2>Limits</h2>' +
      '<p>The incidents are written by us, so they test the failure modes we thought of. Five runs estimate how often a verdict changes but do not cover every source of variation. ' +
      'The AI finds ' + pct(s.chain_recall) + ' of the real causal steps. On RCAEval, the test split (instance 1) was used while tuning the rules, and the fresh split (instance 3) was never looked at while tuning.</p>';
  }

  /* ---------- boot ---------- */
  renderOverview();
  renderIncidents();
  renderReal();
  renderMethod();
  window.addEventListener('hashchange', route);
  route();
})();
