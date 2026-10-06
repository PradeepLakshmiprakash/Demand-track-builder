/* Account overview: one ring over every position, split by stage or by business unit. Every click (a
   slice, a row beside it, a box in the workflow) narrows the page; the demands behind the numbers are
   listed once something is narrowed. Data comes from the page as window.OV (leadership_dashboard). */
(function () {
  var OV = window.OV;
  if (!OV) return;
  var C = ['#0058AB', '#F17817', '#00AE9D', '#DD1D46', '#5685C6', '#43A063', '#9F500D', '#29656F'];
  // Stages read as a journey: one blue, light to dark, while in progress; green once joined; grey if
  // abandoned. Business units have no order, so they keep the categorical colours above.
  var D = OV.rows, ORDER = OV.order, MEANS = OV.means, L = OV.layout;
  var STAGE_C = wfColours(L).stage;
  // Sub-stage order and colours come from the workflow diagram, so the two never drift apart.
  var WFC = wfColours(L), SUB_C = WFC.sub;
  ORDER.sub = WFC.order;
  var NAME = { stage: 'Stage', sub: 'Sub-stage', bu: 'Business unit', type: 'Type', practice: 'Practice', pstart: 'Timing', escd: 'Escalations', costing: 'Costing' };
  var HINT = {
    sub: 'Distribution of the open positions in view across the detailed workflow sub-stages. Each sub-stage carries the colour of its parent stage. Select a slice or a row to restrict the page to that sub-stage.',
    bu: 'Distribution of the positions in view across business units.',
    stage: 'Distribution of the positions in view across the main workflow stages.',
    type: 'Classification of the open positions in view by demand type (new or replacement) and commercial type (billable or non-billable). Select a row to restrict the page to that classification.',
    practice: 'Distribution of the open positions in view by delivery practice, as recorded on each demand. Select a row to restrict the page to that practice.'
  };
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
    if (key === 'sub' && F.sub) { var one = D.filter(function (x) { return x.sub === val; })[0]; if (one) F.stage = one.stage; }
    draw();
  }

  // The sub-stages of the stage that is picked, listed under it.
  function subRows(stage) {
    var rows = rowsFor('sub').filter(function (x) { return x.stage === stage; });
    return count(rows, 'sub').map(function (p) {
      return '<li class="sub-row' + (F.sub === p[0] ? ' on' : '') + '" data-key="sub" data-val="' + esc(p[0]) + '"><span class="sw" style="background:' + (SUB_C[p[0]] || '#888F9A') + '"></span><span>' + esc(p[0]) + '</span><span class="num">' + p[1] + '</span><span class="pct">' + (rows.length ? Math.round(p[1] / rows.length * 100) : 0) + '%</span></li>';
    }).join('');
  }

  function ring() {
    // The ring is the open positions only. Joined and abandoned ones stay out of it; by stage they are
    // still listed under the ring, so they can be opened.
    var all = rowsFor(dim), base = all.filter(function (x) { return x.open; });
    var total = base.length, sel = F[dim], R = 74, LEN = 2 * Math.PI * R, start = 0;
    var keys = ORDER[dim].slice();
    base.forEach(function (x) { if (keys.indexOf(x[dim]) < 0) keys.push(x[dim]); });
    var items = keys.map(function (k, j) { var out = dim === 'stage' && j >= 3; return { k: k, out: out, n: (out ? all : base).filter(function (x) { return x[dim] === k; }).length, c: (dim === 'stage' ? STAGE_C[j] : C[j]) || '#888F9A' }; });
    var h = '<g transform="rotate(-90 100 100)" fill="none">';
    items.forEach(function (it) {
      if (!it.n || it.out) return;
      var len = it.n / total * LEN, vis = Math.max(len - 2, 1);
      h += '<circle class="slice" data-key="' + dim + '" data-val="' + esc(it.k) + '" cx="100" cy="100" r="' + R + '" stroke="' + it.c + '" stroke-width="' + (sel === it.k ? 34 : 26) + '" opacity="' + (sel && sel !== it.k ? .35 : 1) + '" stroke-dasharray="' + vis.toFixed(2) + ' ' + (LEN - vis).toFixed(2) + '" stroke-dashoffset="' + (-start).toFixed(2) + '"></circle>';
      start += len;
    });
    var rows = rowsFor(), openAll = D.filter(function (x) { return x.open; }).length;
    var openNow = rows.filter(function (x) { return x.open; }).length, finished = rows.length && !openNow;
    h += '</g><text x="100" y="97" text-anchor="middle" class="ov-total">' + (finished ? rows.length : openNow) + '</text>';
    h += '<text x="100" y="116" text-anchor="middle" class="ov-unit">' + (finished ? 'finished · not in the ring' : openNow < openAll ? 'of ' + openAll + ' open positions' : 'open positions') + '</text>';
    $('ov-ring').innerHTML = h;
    $('ov-legend').innerHTML = items.map(function (it, j) {
      var head = dim !== 'stage' ? '' : j === 0 ? '<li class="grp-l">In progress · lighter to darker as it moves on</li>' : j === 3 ? '<li class="grp-l">Finished · not in the ring</li>' : '';
      return head + '<li data-key="' + dim + '" data-val="' + esc(it.k) + '" class="' + (sel === it.k ? 'on' : '') + (it.n ? '' : ' zero') + '"><span class="sw" style="background:' + it.c + '"></span><span>' + esc(it.k) + (MEANS[it.k] ? tip(MEANS[it.k]) : '') + '</span><span class="num">' + it.n + '</span><span class="pct">' + (it.out ? '' : (total ? Math.round(it.n / total * 100) : 0) + '%') + '</span>' + '</li>'
        + (dim === 'stage' && sel === it.k ? subRows(it.k) : '');
    }).join('');
    return rows;
  }

  // A small ring beside the main one: the same positions cut another way. Each entity keeps its own
  // colour (by its place in the fixed order), so a filter never repaints what is left.
  // What the breakdowns beside the ring count: open positions, like the ring itself. If the view holds
  // only finished ones (a finished stage was picked), those are shown instead of nothing.
  function live(rows) {
    var open = rows.filter(function (x) { return x.open; });
    return open.length ? open : rows;
  }

  function mini(key) {
    var base = live(rowsFor(key)), pairs = count(base, key), total = base.length;
    if (!pairs.length) return '';
    var R = 36, LEN = 2 * Math.PI * R, start = 0, sel = F[key];
    var order = ORDER[key] || [];
    var colour = function (v) {
      var i = order.indexOf(v);
      if (key === 'stage') return STAGE_C[i] || '#888F9A';
      if (key === 'sub') return SUB_C[v] || '#888F9A';
      return i < 0 ? '#888F9A' : C[i % C.length];
    };
    var svg = '<svg viewBox="0 0 100 100" width="170" height="170" role="img" aria-label="By ' + NAME[key].toLowerCase() + '"><g transform="rotate(-90 50 50)" fill="none">';
    pairs.forEach(function (p) {
      var len = p[1] / total * LEN, vis = pairs.length === 1 ? len : Math.max(len - 1.5, 0.8);
      svg += '<circle class="slice" data-key="' + key + '" data-val="' + esc(p[0]) + '" cx="50" cy="50" r="' + R + '" stroke="' + colour(p[0]) + '" stroke-width="' + (sel === p[0] ? 16 : 12) + '" opacity="' + (sel && sel !== p[0] ? .35 : 1) + '" stroke-dasharray="' + vis.toFixed(2) + ' ' + (LEN - vis).toFixed(2) + '" stroke-dashoffset="' + (-start).toFixed(2) + '"></circle>';
      start += len;
    });
    svg += '</g><text x="50" y="55" text-anchor="middle" class="mini-total">' + total + '</text></svg>';
    return '<div class="mini' + (key === 'sub' ? ' wide' : '') + '"><div class="t"><b>By ' + NAME[key].toLowerCase() + '</b>' + tip(HINT[key]) + '</div><div class="mini-body">' + svg + '<ul class="mini-legend">' + pairs.map(function (p) {
      return '<li class="' + (sel === p[0] ? 'on' : '') + '" data-key="' + key + '" data-val="' + esc(p[0]) + '"><span class="sw" style="background:' + colour(p[0]) + '"></span><span class="nm" title="' + esc(p[0]) + '">' + esc(p[0]) + '</span><span class="n">' + p[1] + '</span></li>';
    }).join('') + '</ul></div></div>';
  }

  // Type and practice stay as plain bars: quick to read, and they don't compete with the rings.
  function bars(key) {
    var base = live(rowsFor(key)), pairs = count(base, key), max = base.length || 1;
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
    var more = function (on) { return on ? ' The page is currently restricted to these positions; select the figure again to remove the restriction.' : ' Select the figure to list these positions.'; };
    $('ov-kpis').innerHTML = kpi('Positions', 'Total number of positions in the current view: every demand past draft that is open, or that was fulfilled or abandoned within the selected period.', rows.length)
      + kpi('Open', 'Positions not yet fulfilled: no candidate has joined and the demand has not been abandoned.', open)
      + kpi('Past start', 'Open positions whose requested start date has passed without a candidate having joined. These positions are accruing revenue loss.' + more(F.pstart), late, late ? 'amber' : '', late ? 'needs action' : '', 'pstart', 'Past start')
      + kpi('Escalated', 'Positions with at least one open escalation. An escalation becomes overdue (Level 2) when the responsible party has not responded within the agreed response time; leadership and the delivery head are then informed.' + more(F.escd), escd, overdue ? 'red' : escd ? 'amber' : '', overdue ? overdue + ' overdue' : '', 'escd', 'Escalated')
      + kpi('Revenue lost', 'Estimated revenue forgone on billable positions that remain unfilled after their requested start date. Calculated as the client bill rate per hour × ' + OV.hours + ' billable hours per day × working days elapsed since the start date.' + (norate ? ' ' + norate + ' position' + (norate === 1 ? ' has' : 's have') + ' no client bill rate recorded and ' + (norate === 1 ? 'is' : 'are') + ' excluded.' : ''), money(lost), lost ? 'red' : '')
      + kpi('Non-billable cost', 'Cost incurred to the account by proactive, non-billable positions since their start date. Calculated as the rate card cost per hour for the practice and grade × ' + OV.hours + ' hours per day × working days elapsed, until the demand owner confirms that client billing has started. Reported separately from revenue lost.' + more(F.costing), money(cost), '', costn ? costn + ' not billing' : '', 'costing', 'Non-billable cost');
  }

  function detail(rows) {
    var ks = Object.keys(F);
    var h = '<div class="eyebrow">You are looking at' + (ks.length ? tip('The filters currently applied to this page. Select a filter tag to remove it individually, or select Clear all to return to the full account view.') : '') + '</div>';
    if (ks.length) {
      h += '<div class="chips">' + ks.map(function (k) { return '<button class="chip-x" data-key="' + k + '" data-val="' + esc(F[k]) + '">' + esc(F[k]) + ' ✕</button>'; }).join('') + '<button class="chip-x clear" id="ov-clear">Clear all</button></div>';
    } else {
      h += '<h2>The whole account' + tip('No filter is applied: every figure on this page reflects the full account for the selected period. Select a slice of the chart, a row in a breakdown, or a headline figure marked with an arrow to restrict the page. All figures and the demand list update with each selection.') + '</h2>';
    }
    if (F.costing) h += costing(rows);  // the breakdown opens only when the Non-billable cost number is clicked
    // Sub-stage is always shown; picking one there also narrows to its stage.
    // One small ring only: the sub-stage cut of what the main ring shows. A second ring on a different
    // footing (business unit beside stage, or the reverse) invited comparisons that don't hold.
    h += '<div class="minis one">' + mini('sub') + '</div>';
    h += '<div class="two">' + bars('type') + bars('practice') + '</div>';
    $('ov-detail').innerHTML = h;
  }

  function costing(rows) {
    var c = rows.filter(function (x) { return x.costing; });
    if (!c.length) return '';
    var sofar = 0, norate = 0;
    c.forEach(function (x) { sofar += x.cost; norate += x.cost_rate === null ? 1 : 0; });
    var h = '<div class="costing"><div class="k">Non-billable cost so far' + tip('Total cost incurred to date by the proactive, non-billable positions in view. Calculated as the rate card cost per hour for each position\'s practice and grade × ' + OV.hours + ' hours per day × working days elapsed since its start date. Accrual stops on the date the demand owner confirms that client billing has started.') + '</div><div class="v">' + money(sofar) + '</div>';
    if (norate) h += '<div class="small late" style="margin-top:6px">' + norate + ' position' + (norate === 1 ? ' has' : 's have') + ' no rate card entry for the practice and grade, and ' + (norate === 1 ? 'is' : 'are') + ' not included.</div>';
    return h + '</div>';
  }

  function costList(rows, title) {
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' non-billable position' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span>' + tip('Proactive, non-billable positions and the cost each has incurred to the account since its start date, calculated as the rate card cost per hour for the practice and grade, multiplied by billable hours per day and the working days elapsed. Rows shown in grey have since been marked billable and no longer accrue cost.') + '</h2></div>';
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>Position</th><th>Business unit</th><th>Practice</th><th>Grade</th><th class="r">Cost so far</th></tr></thead><tbody>';
    var total = 0;
    h += rows.map(function (x) {
      total += x.cost;
      return '<tr' + (x.cost_active ? '' : ' class="stopped"') + '><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a> ' + esc(x.name) + (x.cost_until ? '<div class="small muted">Billable since ' + esc(x.cost_until) + '</div>' : '') + '</td><td>' + esc(x.bu) + '</td><td>' + esc(x.practice) + '</td><td>' + esc(x.grade) + '</td><td class="r"><strong>' + (x.cost_rate === null ? '—' : money(x.cost)) + '</strong></td></tr>';
    }).join('');
    h += '</tbody><tfoot><tr><td colspan="4" class="r"><strong>Total</strong></td><td class="r"><strong>' + money(total) + '</strong></td></tr></tfoot></table></div>';
    $('ov-list').innerHTML = h;
  }

  // Nothing narrowed yet: open with what needs attention first, not with every demand.
  function urgent() {
    var late2 = function (x) { return x.esc.some(function (e) { return e.l === 2; }); };
    var rows = D.filter(function (x) { return late2(x) || x.late; }).sort(function (a, b) {
      return (late2(b) - late2(a)) || (b.days_late - a.days_late) || (a.ref < b.ref ? -1 : 1);
    });
    var total = rows.length; rows = rows.slice(0, 10);
    var h = '<div class="ov-listhead"><h2>Most urgent <span class="small muted">· ' + (total > rows.length ? rows.length + ' of ' + total : total) + '</span>' + tip('Demands requiring immediate attention when no filter is applied. Demands with an overdue (Level 2) escalation are listed first, followed by demands whose requested start date has passed, in order of longest delay. Apply a filter above to list any other set of demands.') + '</h2></div>';
    if (!rows.length) { $('ov-list').innerHTML = h + '<div class="empty">Nothing is overdue or past its start date. Click a slice, a row or a number above to list demands.</div>'; return; }
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Demand</th><th>Owner · BU</th><th>Stage</th><th>Start</th><th>Why it is here</th><th class="r">Revenue lost</th><th></th></tr></thead><tbody>';
    h += rows.map(function (x) {
      var on = openRef === x.ref;
      var why = x.esc.map(function (e) { return '<div class="' + (e.l === 2 ? 'late' : '') + '">⚠ ' + esc(e.t) + ' · L' + e.l + (e.l === 2 ? ' overdue' : '') + '</div>'; }).join('') || '<div>Past its start date</div>';
      return '<tr><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + esc(x.type) + '</div></td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div></td><td class="nw' + (x.late ? ' late' : '') + '">' + esc(x.start || '—') + (x.late ? '<div class="small late">' + x.days_late + ' days late</div>' : '') + '</td><td class="small">' + why + '</td><td class="r">' + (x.lost ? money(x.lost) : '—') + '</td><td><a href="#" class="wf-link" data-flow="' + esc(x.ref) + '">' + (on ? 'Hide workflow' : 'Show workflow') + '</a></td></tr>'
        + (on ? '<tr class="wfrow"><td colspan="8"><div class="wf-wrap">' + wfDiagram(L, { current: { stage: x.stage, sub: x.sub }, escalations: x.esc }) + '</div></td></tr>' : '');
    }).join('');
    $('ov-list').innerHTML = h + '</tbody></table></div>';
  }

  function list(rows) {
    var ks = Object.keys(F);
    if (!ks.length) { urgent(); return; }
    var title = ks.map(function (k) { return NAME[k].toLowerCase() + ': ' + F[k]; }).join(' · ');
    if (F.costing) { costList(rows, title); return; }
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' demand' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span>' + tip('The individual demands that make up the figures shown above for the current selection. Select a reference to open the full demand record, or Show workflow to see its position in the process.') + '</h2></div>';
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Demand</th><th>Owner · BU</th><th>Practice</th><th>Stage</th><th>Start</th><th>Joining</th><th class="r">Revenue lost</th><th></th></tr></thead><tbody>';
    h += rows.map(function (x) {
      var on = openRef === x.ref;
      return '<tr><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + esc(x.type) + '</div></td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.practice) + '</td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div>' + x.esc.map(function (e) { return '<div class="small late">⚠ ' + esc(e.t) + ' · L' + e.l + '</div>'; }).join('') + '</td><td class="nw' + (x.late ? ' late' : '') + '">' + esc(x.start || '—') + (x.late ? '<div class="small late">' + x.days_late + ' days late</div>' : '') + '</td><td>' + esc(x.joining || 'Not set') + '</td><td class="r">' + (x.lost ? money(x.lost) : '—') + '</td><td><a href="#" class="wf-link" data-flow="' + esc(x.ref) + '">' + (on ? 'Hide workflow' : 'Show workflow') + '</a></td></tr>'
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
    $('ov-flowlink').setAttribute('aria-expanded', showFlow ? 'true' : 'false');
    $('ov-flow').hidden = !showFlow;
    if (showFlow) $('ov-flow').innerHTML = '<div class="wx-top"><h2>Workflow' + tip('The end-to-end demand fulfilment process, showing every main stage and sub-stage. ' + wfKey(false)) + '</h2><div class="wx-seg" role="group" aria-label="Level of detail"><span class="on">Simple</span><a href="/overview/workflow" target="_blank" rel="noopener" title="Opens the detailed workflow as a new page">Detailed ↗</a></div></div>'
      + '<div class="wx-backup">Backup versions: <a href="/overview/workflow/classic" target="_blank" rel="noopener">Earlier workflow diagram ↗</a><a href="/overview/workflow?motion=off" target="_blank" rel="noopener">Detailed, without animation ↗</a></div><div class="wf-wrap">' + wfDiagram(L, { counts: counts, esc: em, sel: F.sub || F.stage }) + '</div>';
  }

  function draw() {
    tipBox.hidden = true;
    var rows = ring(); kpis(rows); flow(); detail(rows); list(rows);
    $('ov-reset').disabled = !Object.keys(F).length && !openRef && !showFlow && dim === 'stage';
  }

  $('ov-tabs').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    dim = b.dataset.d;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    draw();
  });
  // Hovering a slice of any ring says what is inside it.
  var tipBox = document.createElement('div');
  tipBox.className = 'ov-tip'; tipBox.hidden = true; document.body.appendChild(tipBox);
  function hover(e) {
    var t = e.target.closest ? e.target.closest('circle.slice') : null;
    if (!t) { tipBox.hidden = true; return; }
    var key = t.dataset.key, val = t.dataset.val;
    var base = live(rowsFor(key)), rows = base.filter(function (x) { return x[key] === val; });
    var lost = 0, late = 0, escd = 0, overdue = 0, cost = 0, oldest = 0;
    rows.forEach(function (x) {
      lost += x.lost || 0; late += x.late ? 1 : 0; cost += x.cost || 0; oldest = Math.max(oldest, x.days_late || 0);
      escd += x.esc.length ? 1 : 0; overdue += x.esc.some(function (q) { return q.l === 2; }) ? 1 : 0;
    });
    var line = function (k, v, cls) { return '<div class="l' + (cls ? ' ' + cls : '') + '"><span>' + k + '</span><b>' + v + '</b></div>'; };
    var h = '<div class="h">' + esc(val) + '</div>'
      + line('Positions', rows.length + ' · ' + (base.length ? Math.round(rows.length / base.length * 100) : 0) + '% of ' + base.length)
      + line('Past start', late + (late ? ' · longest ' + oldest + ' days' : ''), late ? 'warn' : '')
      + line('Escalated', escd + (overdue ? ' · ' + overdue + ' overdue' : ''), overdue ? 'warn' : '')
      + line('Revenue lost', money(lost), lost ? 'warn' : '');
    if (cost) h += line('Non-billable cost', money(cost));
    if (key !== 'sub') {
      var top = count(rows, 'sub').sort(function (a, b) { return b[1] - a[1]; }).slice(0, 3);
      if (top.length) h += '<div class="s">Mostly at: ' + top.map(function (q) { return esc(q[0]) + ' (' + q[1] + ')'; }).join(', ') + '</div>';
    } else {
      var bus = count(rows, 'bu').map(function (q) { return esc(q[0]) + ' (' + q[1] + ')'; }).join(', ');
      if (bus) h += '<div class="s">In: ' + bus + '</div>';
    }
    h += '<div class="s">' + (F[key] === val ? 'Click to stop narrowing to this' : 'Click to see these demands') + '</div>';
    tipBox.innerHTML = h; tipBox.hidden = false;
    var w = tipBox.offsetWidth, hh = tipBox.offsetHeight, x = e.clientX + 16, y = e.clientY + 16;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 16;
    if (y + hh > window.innerHeight - 8) y = e.clientY - hh - 16;
    tipBox.style.left = Math.max(8, x) + 'px'; tipBox.style.top = Math.max(8, y) + 'px';
  }
  $('ov').addEventListener('mousemove', hover);
  $('ov').addEventListener('mouseleave', function () { tipBox.hidden = true; });

  // The workflow button lives in the page head, outside the overview block.
  $('ov-flowlink').addEventListener('click', function (e) {
    e.preventDefault(); showFlow = !showFlow; draw();
    if (showFlow) $('ov-flow').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  $('ov').addEventListener('click', function (e) {
    if (e.target.id === 'ov-clear') { F = {}; draw(); return; }
    if (e.target.id === 'ov-reset') {  // everything back to how the page opens
      F = {}; openRef = null; showFlow = false; dim = 'stage';
      $('ov-tabs').querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x.dataset.d === 'stage'); });
      draw(); return;
    }
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
