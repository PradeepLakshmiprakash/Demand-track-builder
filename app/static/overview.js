/* Account overview: one ring over every position, split by stage or by business unit. Every click (a
   slice, a row beside it, a box in the workflow) narrows the page; the demands behind the numbers are
   listed once something is narrowed. Data comes from the page as window.OV (leadership_dashboard). */
(function () {
  var OV = window.OV;
  if (!OV) return;
  var C = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
  var D = OV.rows, ORDER = OV.order, MEANS = OV.means, L = OV.layout;
  var NAME = { stage: 'Stage', sub: 'Sub-stage', bu: 'Business unit', type: 'Type', practice: 'Practice', pstart: 'Timing', escd: 'Escalations' };
  var HINT = { sub: 'where exactly inside this stage', bu: 'which business unit they belong to', stage: 'how far along they are',
    type: 'new or replacement, billable or not', practice: 'the skill area' };
  var dim = 'stage', F = {}, openRef = null, showFlow = location.hash === '#workflow';
  var $ = function (id) { return document.getElementById(id); };

  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function money(v) { return '$' + Math.round(v).toLocaleString('en-US'); }
  function rowsFor(skip) { return D.filter(function (x) { for (var k in F) { if (k !== skip && x[k] !== F[k]) return false; } return true; }); }
  function count(rows, key) {
    var m = {}; rows.forEach(function (x) { m[x[key]] = (m[x[key]] || 0) + 1; });
    var keys = ORDER[key] ? ORDER[key].filter(function (k) { return m[k]; }) : [];
    Object.keys(m).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    return keys.map(function (k) { return [k, m[k]]; });
  }
  function toggle(key, val) {
    if (F[key] === val) { delete F[key]; } else { F[key] = val; }
    if (key === 'stage') delete F.sub;
    draw();
  }

  function ring() {
    var base = rowsFor(dim), total = base.length, sel = F[dim], R = 74, LEN = 2 * Math.PI * R, start = 0;
    var keys = ORDER[dim].slice();
    base.forEach(function (x) { if (keys.indexOf(x[dim]) < 0) keys.push(x[dim]); });
    var items = keys.map(function (k, j) { return { k: k, n: base.filter(function (x) { return x[dim] === k; }).length, c: C[j] || '#8B877E' }; });
    var h = '<g transform="rotate(-90 100 100)" fill="none">';
    items.forEach(function (it) {
      if (!it.n) return;
      var len = it.n / total * LEN, vis = Math.max(len - 2, 1);
      h += '<circle class="slice" data-key="' + dim + '" data-val="' + esc(it.k) + '" cx="100" cy="100" r="' + R + '" stroke="' + it.c + '" stroke-width="' + (sel === it.k ? 34 : 26) + '" opacity="' + (sel && sel !== it.k ? .35 : 1) + '" stroke-dasharray="' + vis.toFixed(2) + ' ' + (LEN - vis).toFixed(2) + '" stroke-dashoffset="' + (-start).toFixed(2) + '"><title>' + esc(it.k) + ': ' + it.n + '</title></circle>';
      start += len;
    });
    var rows = rowsFor();
    h += '</g><text x="100" y="97" text-anchor="middle" class="ov-total">' + rows.length + '</text>';
    h += '<text x="100" y="116" text-anchor="middle" class="ov-unit">' + (rows.length < D.length ? 'of ' + D.length + ' positions' : 'positions') + '</text>';
    $('ov-ring').innerHTML = h;
    $('ov-legend').innerHTML = items.map(function (it) {
      return '<li data-key="' + dim + '" data-val="' + esc(it.k) + '" class="' + (sel === it.k ? 'on' : '') + (it.n ? '' : ' zero') + '"><span class="sw" style="background:' + it.c + '"></span><span>' + esc(it.k) + '</span><span class="num">' + it.n + '</span><span class="pct">' + (total ? Math.round(it.n / total * 100) : 0) + '%</span>' + (MEANS[it.k] ? '<span class="d">' + esc(MEANS[it.k]) + '</span>' : '') + '</li>';
    }).join('');
    return rows;
  }

  function bars(key) {
    var base = rowsFor(key), pairs = count(base, key), max = base.length || 1;
    if (!pairs.length) return '';
    return '<div class="grp"><div class="t"><b>By ' + NAME[key].toLowerCase() + '</b> · ' + HINT[key] + '</div>' + pairs.map(function (p) {
      return '<div class="bar ' + (F[key] === p[0] ? 'on' : '') + '" data-key="' + key + '" data-val="' + esc(p[0]) + '"><span>' + esc(p[0]) + '</span><span class="track"><span class="fill" style="width:' + (p[1] / max * 100) + '%"></span></span><span class="n">' + p[1] + '</span></div>';
    }).join('') + '</div>';
  }

  function detail(rows) {
    var lost = 0, late = 0, open = 0, norate = 0, escd = 0, ks = Object.keys(F);
    rows.forEach(function (x) { lost += x.lost || 0; late += x.late ? 1 : 0; open += x.open ? 1 : 0; norate += x.norate ? 1 : 0; escd += x.esc.length ? 1 : 0; });
    var h = '<div class="eyebrow">' + (ks.length ? 'You are looking at · click a tag to remove it' : 'You are looking at') + '</div>';
    if (ks.length) {
      h += '<div class="chips">' + ks.map(function (k) { return '<button class="chip-x" data-key="' + k + '" data-val="' + esc(F[k]) + '">' + esc(F[k]) + ' ✕</button>'; }).join('') + '<button class="chip-x clear" id="ov-clear">Clear all</button></div>';
    } else {
      h += '<h2>The whole account</h2><div class="small muted">Click any row below, or a slice, to narrow down.</div>';
    }
    h += '<div class="facts4"><div class="fact"><div class="k">Positions</div><div class="v">' + rows.length + '</div><div class="h">in this view</div></div>'
      + '<div class="fact"><div class="k">Open</div><div class="v">' + open + '</div><div class="h">nobody has joined yet</div></div>'
      + '<div class="fact pick' + (F.pstart ? ' on' : '') + '" data-key="pstart" data-val="Past start" role="button" tabindex="0"><div class="k">Past start</div><div class="v">' + late + '</div><div class="h">start date gone, still unfilled · ' + (F.pstart ? 'showing only these' : 'click to see them') + '</div></div>'
      + '<div class="fact pick' + (F.escd ? ' on' : '') + '" data-key="escd" data-val="Escalated" role="button" tabindex="0"><div class="k">Escalated</div><div class="v">' + escd + '</div><div class="h">with an open escalation · ' + (F.escd ? 'showing only these' : 'click to see them') + '</div></div>'
      + '<div class="fact"><div class="k">Revenue lost</div><div class="v">' + money(lost) + '</div><div class="h">bill rate × ' + OV.hours + ' h × working days late' + (norate ? ' · ' + norate + ' with no bill rate' : '') + '</div></div></div>';
    if (F.stage) h += bars('sub');
    h += bars(dim === 'stage' ? 'bu' : 'stage');
    h += '<div class="two">' + bars('type') + bars('practice') + '</div>';
    $('ov-detail').innerHTML = h;
  }

  function list(rows) {
    var ks = Object.keys(F);
    if (!ks.length) {
      $('ov-list').innerHTML = '<div class="empty">Demands are listed here once you narrow down. Click a slice of the ring, any row beside it, the Past start or Escalated number, or a box in the workflow.</div>';
      return;
    }
    var title = ks.map(function (k) { return NAME[k].toLowerCase() + ': ' + F[k]; }).join(' · ');
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' demand' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span></h2><span class="small muted">These are the demands behind the numbers above.</span></div>';
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Demand</th><th>Owner · BU</th><th>Practice</th><th>Stage</th><th>Start</th><th>Joining</th><th class="r">Revenue lost</th><th></th></tr></thead><tbody>';
    h += rows.map(function (x) {
      var on = openRef === x.ref;
      return '<tr><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + esc(x.type) + '</div></td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.practice) + '</td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div>' + x.esc.map(function (e) { return '<div class="small late">⚠ ' + esc(e.t) + ' · L' + e.l + '</div>'; }).join('') + '</td><td class="' + (x.late ? 'late' : '') + '">' + esc(x.start || '—') + (x.late ? '<div class="small late">' + x.days_late + ' days late</div>' : '') + '</td><td>' + esc(x.joining || 'Not set') + '</td><td class="r">' + (x.lost ? money(x.lost) : '—') + '</td><td><a href="#" class="wf-link" data-flow="' + esc(x.ref) + '">' + (on ? 'Hide workflow' : 'Show workflow') + '</a></td></tr>'
        + (on ? '<tr class="wfrow"><td colspan="9"><div class="small" style="margin-bottom:8px"><strong>' + esc(x.ref) + ' · ' + esc(x.name) + '</strong> is now at <strong>' + esc(x.stage) + ' · ' + esc(x.sub) + '</strong></div><div class="wf-wrap">' + wfDiagram(L, { current: { stage: x.stage, sub: x.sub }, escalations: x.esc }) + '</div></td></tr>' : '');
    }).join('');
    if (!rows.length) h += '<tr><td colspan="9" class="small muted">No demands match. Remove a tag above.</td></tr>';
    $('ov-list').innerHTML = h + '</tbody></table></div>';
  }

  function flow() {
    var rows = D.filter(function (x) { for (var k in F) { if (k !== 'stage' && k !== 'sub' && x[k] !== F[k]) return false; } return true; });
    var counts = {}, em = {};
    rows.forEach(function (x) { counts[x.sub] = (counts[x.sub] || 0) + 1; if (x.esc.length) em[x.sub] = (em[x.sub] || 0) + x.esc.length; });
    $('ov-flowlink').textContent = showFlow ? 'Hide workflow' : 'Show workflow';
    $('ov-flow').hidden = !showFlow;
    if (showFlow) $('ov-flow').innerHTML = '<h2>Workflow</h2><div class="hint" style="margin-bottom:10px">Every stage and sub-stage a demand can be in, with the open escalations at each step.</div><div class="wf-wrap">' + wfDiagram(L, { counts: counts, esc: em, sel: F.sub || F.stage }) + '</div>';
  }

  function draw() { var rows = ring(); flow(); detail(rows); list(rows); }

  $('ov-tabs').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    dim = b.dataset.d;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    draw();
  });
  $('ov').addEventListener('click', function (e) {
    if (e.target.id === 'ov-clear') { F = {}; draw(); return; }
    if (e.target.id === 'ov-flowlink') { e.preventDefault(); showFlow = !showFlow; draw(); return; }
    var w = e.target.closest('[data-flow]');
    if (w) { e.preventDefault(); openRef = openRef === w.dataset.flow ? null : w.dataset.flow; draw(); return; }
    var inFlow = e.target.closest('#ov-flow');
    var sb = inFlow && e.target.closest('.wfbox');
    if (sb) { if (F.sub === sb.dataset.sub) { delete F.sub; } else { F.stage = sb.dataset.stage; F.sub = sb.dataset.sub; } draw(); return; }
    var st = inFlow && e.target.closest('.wfstage');
    if (st) { toggle('stage', st.dataset.stage); return; }
    var t = e.target.closest('[data-key]');
    if (t) toggle(t.dataset.key, t.dataset.val);
  });
  draw();
})();
