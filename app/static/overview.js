/* Account overview: one ring over every position, split by stage or by business unit. Every click (a
   slice, a row beside it, a box in the workflow) narrows the page; the demands behind the numbers are
   listed once something is narrowed. Data comes from the page as window.OV (leadership_dashboard). */
(function () {
  var OV = window.OV;
  if (!OV) return;
  var C = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
  // Stages read as a journey: one blue, light to dark, while in progress; green once joined; grey if
  // abandoned. Business units have no order, so they keep the categorical colours above.
  var STAGE_C = ['#6FA8EA', '#2468C2', '#123B73', '#17966A', '#B0ACA2'];
  var D = OV.rows, ORDER = OV.order, MEANS = OV.means, L = OV.layout;
  var NAME = { stage: 'Stage', sub: 'Sub-stage', bu: 'Business unit', type: 'Type', practice: 'Practice', pstart: 'Timing', escd: 'Escalations', costing: 'Costing' };
  var HINT = { sub: 'where exactly inside this stage', bu: 'which business unit they belong to', stage: 'how far along they are',
    type: 'new or replacement, billable or not', practice: 'the skill area' };
  var dim = 'stage', F = {}, openRef = null, showFlow = location.hash === '#workflow';
  var $ = function (id) { return document.getElementById(id); };

  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  // An "i" mark: the explanation shows on hover or keyboard focus instead of sitting on the page.
  function tip(text) { return ' <span class="info" tabindex="0" role="note" aria-label="' + esc(text) + '" data-tip="' + esc(text) + '">i</span>'; }
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
    // The ring is the open positions only. Joined and abandoned ones stay out of it; by stage they are
    // still listed under the ring, so they can be opened.
    var all = rowsFor(dim), base = all.filter(function (x) { return x.open; });
    var total = base.length, sel = F[dim], R = 74, LEN = 2 * Math.PI * R, start = 0;
    var keys = ORDER[dim].slice();
    base.forEach(function (x) { if (keys.indexOf(x[dim]) < 0) keys.push(x[dim]); });
    var items = keys.map(function (k, j) { var out = dim === 'stage' && j >= 3; return { k: k, out: out, n: (out ? all : base).filter(function (x) { return x[dim] === k; }).length, c: (dim === 'stage' ? STAGE_C[j] : C[j]) || '#8B877E' }; });
    var h = '<g transform="rotate(-90 100 100)" fill="none">';
    items.forEach(function (it) {
      if (!it.n || it.out) return;
      var len = it.n / total * LEN, vis = Math.max(len - 2, 1);
      h += '<circle class="slice" data-key="' + dim + '" data-val="' + esc(it.k) + '" cx="100" cy="100" r="' + R + '" stroke="' + it.c + '" stroke-width="' + (sel === it.k ? 34 : 26) + '" opacity="' + (sel && sel !== it.k ? .35 : 1) + '" stroke-dasharray="' + vis.toFixed(2) + ' ' + (LEN - vis).toFixed(2) + '" stroke-dashoffset="' + (-start).toFixed(2) + '"><title>' + esc(it.k) + ': ' + it.n + '</title></circle>';
      start += len;
    });
    var rows = rowsFor(), openAll = D.filter(function (x) { return x.open; }).length;
    var openNow = rows.filter(function (x) { return x.open; }).length, finished = rows.length && !openNow;
    h += '</g><text x="100" y="97" text-anchor="middle" class="ov-total">' + (finished ? rows.length : openNow) + '</text>';
    h += '<text x="100" y="116" text-anchor="middle" class="ov-unit">' + (finished ? 'finished · not in the ring' : openNow < openAll ? 'of ' + openAll + ' open positions' : 'open positions') + '</text>';
    $('ov-ring').innerHTML = h;
    $('ov-legend').innerHTML = items.map(function (it, j) {
      var head = dim !== 'stage' ? '' : j === 0 ? '<li class="grp-l">In progress · lighter to darker as it moves on</li>' : j === 3 ? '<li class="grp-l">Finished · not in the ring</li>' : '';
      return head + '<li data-key="' + dim + '" data-val="' + esc(it.k) + '" class="' + (sel === it.k ? 'on' : '') + (it.n ? '' : ' zero') + '"><span class="sw" style="background:' + it.c + '"></span><span>' + esc(it.k) + (MEANS[it.k] ? tip(MEANS[it.k]) : '') + '</span><span class="num">' + it.n + '</span><span class="pct">' + (it.out ? '' : (total ? Math.round(it.n / total * 100) : 0) + '%') + '</span>' + '</li>';
    }).join('');
    return rows;
  }

  function bars(key) {
    var base = rowsFor(key), pairs = count(base, key), max = base.length || 1;
    if (!pairs.length) return '';
    return '<div class="grp"><div class="t"><b>By ' + NAME[key].toLowerCase() + '</b>' + tip(HINT[key]) + '</div>' + pairs.map(function (p) {
      return '<div class="bar ' + (F[key] === p[0] ? 'on' : '') + '" data-key="' + key + '" data-val="' + esc(p[0]) + '"><span>' + esc(p[0]) + '</span><span class="track"><span class="fill" style="width:' + (p[1] / max * 100) + '%"></span></span><span class="n">' + p[1] + '</span></div>';
    }).join('') + '</div>';
  }

  function kpi(label, tipText, value, state, tag, key, val) {
    var pick = key ? ' pick' + (F[key] ? ' on' : '') + '" data-key="' + key + '" data-val="' + val + '" role="button" tabindex="0' : '';
    return '<div class="kpi' + (state ? ' ' + state : '') + pick + '"><div class="k">' + label + tip(tipText) + '</div><div class="v">' + value + '</div>' + (tag ? '<span class="tag">' + tag + '</span>' : '') + '</div>';
  }

  // The six numbers, across the top. The ones that need action turn amber or red; zero stays neutral.
  function kpis(rows) {
    var lost = 0, late = 0, open = 0, norate = 0, escd = 0, overdue = 0, cost = 0, costn = 0;
    rows.forEach(function (x) {
      lost += x.lost || 0; late += x.late ? 1 : 0; open += x.open ? 1 : 0; norate += x.norate ? 1 : 0;
      escd += x.esc.length ? 1 : 0; overdue += x.esc.some(function (e) { return e.l === 2; }) ? 1 : 0;
      cost += x.cost || 0; costn += x.cost_active ? 1 : 0;
    });
    var more = function (on) { return on ? ' Showing only these; click again to go back.' : ' Click to see them.'; };
    $('ov-kpis').innerHTML = kpi('Positions', 'Positions in this view.', rows.length)
      + kpi('Open', 'Nobody has joined yet.', open)
      + kpi('Past start', 'Start date gone, still unfilled.' + more(F.pstart), late, late ? 'amber' : '', late ? 'needs action' : '', 'pstart', 'Past start')
      + kpi('Escalated', 'Demands with an open escalation.' + more(F.escd), escd, overdue ? 'red' : escd ? 'amber' : '', overdue ? overdue + ' overdue' : '', 'escd', 'Escalated')
      + kpi('Revenue lost', 'Bill rate × ' + OV.hours + ' h × working days late, on billable positions.' + (norate ? ' ' + norate + ' with no bill rate.' : ''), money(lost), lost ? 'red' : '')
      + kpi('Non-billable cost', 'What proactive, non-billable positions have cost the account since their start date.' + more(F.costing), money(cost), '', costn ? costn + ' not billing' : '', 'costing', 'Non-billable cost');
  }

  function detail(rows) {
    var ks = Object.keys(F);
    var h = '<div class="eyebrow">You are looking at' + (ks.length ? tip('Click a tag to remove it, or Clear all to see the whole account again.') : '') + '</div>';
    if (ks.length) {
      h += '<div class="chips">' + ks.map(function (k) { return '<button class="chip-x" data-key="' + k + '" data-val="' + esc(F[k]) + '">' + esc(F[k]) + ' ✕</button>'; }).join('') + '<button class="chip-x clear" id="ov-clear">Clear all</button></div>';
    } else {
      h += '<h2>The whole account' + tip('Click a slice of the ring, any row below, or a number with an arrow, to narrow down. The numbers and the list of demands follow every click.') + '</h2>';
    }
    if (F.costing) h += costing(rows);  // the breakdown opens only when the Non-billable cost number is clicked
    if (F.stage) h += bars('sub');
    h += bars(dim === 'stage' ? 'bu' : 'stage');
    h += '<div class="two">' + bars('type') + bars('practice') + '</div>';
    $('ov-detail').innerHTML = h;
  }

  function costing(rows) {
    var c = rows.filter(function (x) { return x.costing; });
    if (!c.length) return '';
    var active = 0, sofar = 0, month = 0, norate = 0, bu = {};
    c.forEach(function (x) {
      active += x.cost_active ? 1 : 0; sofar += x.cost; month += x.cost_month; norate += x.cost_rate === null ? 1 : 0;
      var b = bu[x.bu] || (bu[x.bu] = { n: 0, cost: 0, month: 0 });
      b.n += x.cost_active ? 1 : 0; b.cost += x.cost; b.month += x.cost_month;
    });
    var max = Math.max.apply(null, Object.keys(bu).map(function (k) { return bu[k].cost; })) || 1;
    var h = '<div class="costing"><div class="ov-listhead"><h2>Costing' + tip('Proactive, non-billable positions past their start date. They cost the account every working day until the demand owner marks them billable. Never counted as revenue lost.') + '</h2></div>'
      + '<div class="cost3"><div><div class="k">Positions not billing' + tip('the client isn\'t paying for them yet') + '</div><div class="v">' + active + '</div></div>'
      + '<div><div class="k">Cost so far' + tip('cost rate × ' + OV.hours + ' h × working days since the start date') + '</div><div class="v">' + money(sofar) + '</div></div>'
      + '<div><div class="k">Cost per month from here' + tip('if nothing changes · ' + OV.month_days + ' working days') + '</div><div class="v">' + money(month) + '</div></div></div>';
    if (norate) h += '<div class="small late" style="margin-top:8px">' + norate + ' position' + (norate === 1 ? ' has' : 's have') + ' no cost rate: no offer, and no rate card entry for the grade, practice and region.</div>';
    h += '<table class="ov-table"><thead><tr><th>Business unit</th><th class="r">Not billing</th><th>Agreed cap</th><th>Cost so far</th><th class="r">Per month</th></tr></thead><tbody>';
    Object.keys(bu).sort().forEach(function (k) {
      var b = bu[k], cap = OV.caps[k];
      h += '<tr><td>' + esc(k) + '</td><td class="r">' + b.n + '</td><td>' + (cap === null || cap === undefined ? '—' : cap + (b.n > cap ? ' <span class="chip esc">over cap</span>' : '')) + '</td><td><span class="cbar"><i style="width:' + (b.cost / max * 100) + '%"></i></span>' + money(b.cost) + '</td><td class="r">' + money(b.month) + '</td></tr>';
    });
    return h + '</tbody></table></div>';
  }

  function costList(rows, title) {
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' non-billable position' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span>' + tip('What each one has cost the account since its start date. Greyed rows are already billable: their costing has stopped.') + '</h2></div>';
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Position</th><th>Resource</th><th>Owner · BU</th><th>Practice · grade</th><th>Stage</th><th>Start date</th><th class="r">Working days</th><th class="r">Cost / h</th><th class="r">Cost so far</th></tr></thead><tbody>';
    var total = 0;
    h += rows.map(function (x) {
      if (x.cost_active) total += x.cost;
      return '<tr' + (x.cost_active ? '' : ' class="stopped"') + '><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + (x.cost_until ? 'Billable since ' + esc(x.cost_until) : esc(x.type)) + '</div></td><td>' + esc(x.resource || 'Not named yet') + '</td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.practice) + ' · ' + esc(x.grade) + '</td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div></td><td>' + esc(x.start || '—') + '</td><td class="r">' + x.cost_days + '</td><td class="r">' + (x.cost_rate === null ? '<span class="late">none</span>' : '$' + x.cost_rate.toFixed(2) + '<div class="small muted">' + esc(x.cost_source) + '</div>') + '</td><td class="r"><strong>' + (x.cost_rate === null ? '—' : money(x.cost)) + '</strong>' + (x.cost_active ? '' : '<div class="small muted">costing stopped</div>') + '</td></tr>';
    }).join('');
    h += '</tbody><tfoot><tr><td colspan="9" class="r"><strong>Still costing</strong></td><td class="r"><strong>' + money(total) + '</strong></td></tr></tfoot></table></div>';
    $('ov-list').innerHTML = h;
  }

  // Nothing narrowed yet: open with what needs attention first, not with every demand.
  function urgent() {
    var late2 = function (x) { return x.esc.some(function (e) { return e.l === 2; }); };
    var rows = D.filter(function (x) { return late2(x) || x.late; }).sort(function (a, b) {
      return (late2(b) - late2(a)) || (b.days_late - a.days_late) || (a.ref < b.ref ? -1 : 1);
    });
    var total = rows.length; rows = rows.slice(0, 10);
    var h = '<div class="ov-listhead"><h2>Most urgent <span class="small muted">· ' + (total > rows.length ? rows.length + ' of ' + total : total) + '</span>' + tip('Demands with an overdue (L2) escalation first, then those past their start date, longest first. Narrow down above to list any other demands.') + '</h2></div>';
    if (!rows.length) { $('ov-list').innerHTML = h + '<div class="empty">Nothing is overdue or past its start date. Click a slice, a row or a number above to list demands.</div>'; return; }
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Demand</th><th>Owner · BU</th><th>Stage</th><th>Start</th><th>Why it is here</th><th class="r">Revenue lost</th><th></th></tr></thead><tbody>';
    h += rows.map(function (x) {
      var on = openRef === x.ref;
      var why = x.esc.map(function (e) { return '<div class="' + (e.l === 2 ? 'late' : '') + '">⚠ ' + esc(e.t) + ' · L' + e.l + (e.l === 2 ? ' overdue' : '') + '</div>'; }).join('') || '<div>Past its start date</div>';
      return '<tr><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + esc(x.type) + '</div></td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div></td><td class="' + (x.late ? 'late' : '') + '">' + esc(x.start || '—') + (x.late ? '<div class="small late">' + x.days_late + ' days late</div>' : '') + '</td><td class="small">' + why + '</td><td class="r">' + (x.lost ? money(x.lost) : '—') + '</td><td><a href="#" class="wf-link" data-flow="' + esc(x.ref) + '">' + (on ? 'Hide workflow' : 'Show workflow') + '</a></td></tr>'
        + (on ? '<tr class="wfrow"><td colspan="8"><div class="wf-wrap">' + wfDiagram(L, { current: { stage: x.stage, sub: x.sub }, escalations: x.esc }) + '</div></td></tr>' : '');
    }).join('');
    $('ov-list').innerHTML = h + '</tbody></table></div>';
  }

  function list(rows) {
    var ks = Object.keys(F);
    if (!ks.length) { urgent(); return; }
    var title = ks.map(function (k) { return NAME[k].toLowerCase() + ': ' + F[k]; }).join(' · ');
    if (F.costing) { costList(rows, title); return; }
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' demand' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span>' + tip('These are the demands behind the numbers above. Click a reference to open the demand.') + '</h2></div>';
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
    if (showFlow) $('ov-flow').innerHTML = '<h2 style="margin-bottom:10px">Workflow' + tip('Every stage and sub-stage a demand can be in. ' + wfKey(false)) + '</h2><div class="wf-wrap">' + wfDiagram(L, { counts: counts, esc: em, sel: F.sub || F.stage }) + '</div>';
  }

  function draw() {
    var rows = ring(); kpis(rows); flow(); detail(rows); list(rows);
    $('ov-reset').disabled = !Object.keys(F).length && !openRef && !showFlow && dim === 'stage';
  }

  $('ov-tabs').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    dim = b.dataset.d;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    draw();
  });
  $('ov').addEventListener('click', function (e) {
    if (e.target.id === 'ov-clear') { F = {}; draw(); return; }
    if (e.target.id === 'ov-reset') {  // everything back to how the page opens
      F = {}; openRef = null; showFlow = false; dim = 'stage';
      $('ov-tabs').querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x.dataset.d === 'stage'); });
      draw(); return;
    }
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
