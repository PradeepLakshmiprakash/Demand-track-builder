/* The workflow diagram: each main stage a column, each sub-stage a numbered step, arrows in the order a
   demand moves, exceptions on a dashed branch beside the step they belong to.
   wfDiagram(layout, {counts, esc}) puts the number of demands and open escalations on each step (account
   overview); wfDiagram(layout, {current}) marks the steps one demand has passed and where it is now.
   {detail} adds who acts, what happens, what moves it on, where it goes back to and which escalation
   fires there (the detailed page). The layout comes from workflow_service.layout().
   The diagram before this one is kept as workflow_classic.js. */
(function () {
  // One blue, light to dark, while a demand is in progress; green once joined; grey if abandoned.
  var COL = ['#8EA6D5', '#3573C0', '#1C4076', '#43A063', '#A6ACB5'];
  var TINT = ['#EFF0F4', '#E9EDF3', '#DFE1EB', '#E7F6EB', '#F1F4F7'];
  var ON = ['#121A38', '#fff', '#fff', '#fff', '#171A22'];  // text that sits on each colour

  function tint(hex, t) {  // mix towards white by t (0 = the colour itself)
    var n = parseInt(hex.slice(1), 16), ch = [n >> 16, (n >> 8) & 255, n & 255];
    return '#' + ch.map(function (v) { return ('0' + Math.round(v + (255 - v) * t).toString(16)).slice(-2); }).join('');
  }
  function inkOn(hex) {  // dark text on a light colour, white on a dark one
    var n = parseInt(hex.slice(1), 16);
    return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) > 150 ? '#121A38' : '#fff';
  }
  /* The colours everything on the overview shares: one per stage, and for each sub-stage its stage's
     colour, lighter the later it comes within the stage. Sub-stages are returned in workflow order. */
  window.wfColours = function (layout) {
    var sub = {}, order = [];
    layout.stages.concat([{ subs: layout.abandoned.subs, problems: [] }]).forEach(function (st, si) {
      var mine = [];
      st.subs.forEach(function (sb, k) {
        mine.push(sb);
        st.problems.forEach(function (pr) { if (pr[1] === k) mine.push(pr[0]); });
      });
      mine.forEach(function (sb, k) { order.push(sb); sub[sb] = tint(COL[si], mine.length > 1 ? .62 * k / (mine.length - 1) : 0); });
    });
    return { stage: COL.slice(), sub: sub, order: order };
  };

  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  /* How to read the diagram, for an info mark beside its heading. */
  window.wfKey = function (single) {
    return 'Numbered steps are the standard path, read top to bottom within a stage and then left to right across stages. A dashed red branch is an exception a demand may fall into at that step.'
      + (single ? ' Green steps are completed, with the date reached where it is recorded; the outlined step is the current one; faded steps are still ahead.' : ' The figure on each step is the number of demands currently there; select a step or a stage header to list them.')
      + ' A warning badge shows the number of open escalations at that step.';
  };

  window.wfDiagram = function (layout, o) {
    var cur = o.current, counts = o.counts, D = o.detail, SUBC = window.wfColours(layout).sub;
    var order = [], hangs = {};
    layout.stages.forEach(function (c) {
      c.subs.forEach(function (b) { order.push(b); });
      c.problems.forEach(function (e) { hangs[e[0]] = c.subs[e[1]]; });
    });
    var left = cur && cur.stage === layout.abandoned.label;
    var reached = cur && !left ? order.indexOf(hangs[cur.sub] || cur.sub) : -1;
    var when = o.when || {}, skipped = o.skipped || [], pick = counts ? ' style="cursor:pointer"' : '';

    function who(k) { var w = D.who[k]; return w ? '<span class="wx-who' + (w[2] ? ' out' : '') + '"><i style="background:' + w[1] + '"></i>' + esc(w[0]) + '</span>' : ''; }
    function more(sub) {
      var d = D && D.steps[sub]; if (!d) return '';
      return '<div class="wx-d">' + (d.who || []).map(who).join(' ')
        + (d.does ? '<p>' + esc(d.does) + '</p>' : '')
        + (d.next ? '<p class="wx-next">' + esc(d.next) + '</p>' : '')
        + (d.back || []).map(function (l) { return '<div><span class="wx-back"><b>↩</b>' + esc(l) + '</span></div>'; }).join('')
        + (d.trig || []).map(function (t) { return '<span class="wx-trig">⚠ ' + esc(t) + '</span>'; }).join('') + '</div>';
    }
    function step(stage, sub, ci, no, problem) {
      var n = counts ? (counts[sub] || 0) : 0, idx = order.indexOf(sub), mine = SUBC[sub] || COL[ci];
      var now = cur && cur.sub === sub && cur.stage === stage;
      var skip = cur && !problem && skipped.indexOf(sub) >= 0 && idx < reached;
      var done = cur && !left && !problem && !skip && idx >= 0 && (idx < reached || (idx === reached && hangs[cur.sub]));
      var cls = 'wx-step wfbox' + (problem ? ' ex' : ' main') + (o.sel === sub ? ' sel' : '');
      if (counts) cls += n ? '' : ' zero';
      if (cur) cls += now ? ' here' : done ? ' done' : (problem && when[sub] ? ' was' : ' later');
      var en = o.esc ? (o.esc[sub] || 0) : (now && o.escalations ? o.escalations.length : 0);
      var h = (problem ? '<div class="wx-exlink"><i></i>' + (D ? '<span>If it goes wrong</span>' : '') + '</div>' : '');
      h += '<div class="' + cls + '" data-stage="' + esc(stage) + '" data-sub="' + esc(sub) + '" style="--c:' + mine + ';--on:' + inkOn(mine) + '"' + pick + '>';
      h += '<div class="wx-h"><span class="wx-num">' + no + '</span><b>' + esc(sub) + '</b>';
      if (en) h += '<span class="wx-e" title="' + en + ' open escalation' + (en === 1 ? '' : 's') + '">⚠ ' + en + '</span>';
      if (cur && now) h += '<span class="wx-here">Now here' + (when[sub] ? ' · since ' + esc(when[sub]) : '') + '</span>';
      else if (cur && when[sub] && (done || problem)) h += '<span class="wx-when">' + (problem ? 'was here ' : '') + esc(when[sub]) + '</span>';
      if (n) h += '<span class="wx-n">' + n + '</span>';
      return h + '</div>' + more(sub) + '</div>';
    }

    var no = 0, h = '';
    layout.stages.forEach(function (c, ci) {
      var total = 0, body = '';
      if (counts) c.subs.concat(c.problems.map(function (e) { return e[0]; })).forEach(function (b) { total += counts[b] || 0; });
      c.subs.forEach(function (b, k) {
        if (k) body += '<div class="wx-arrow"><i></i></div>';
        body += step(c.label, b, ci, ++no, false);
        c.problems.forEach(function (e) { if (e[1] === k) body += step(c.label, e[0], ci, no + 'a', true); });
        if (D && D.decision && D.decision.after === b) {
          body += '<div class="wx-arrow"><i></i></div><div class="wx-dec"><div class="q">' + esc(D.decision.q) + '</div><div class="o"><b>Yes</b><span>' + esc(D.decision.yes) + '</span></div><div class="o"><b>No</b><span>' + esc(D.decision.no) + '</span></div></div>';
        }
      });
      var on = o.sel === c.label || (cur && cur.stage === c.label);
      h += '<section class="wx-stage wfstage' + (on ? ' on' : '') + '" data-stage="' + esc(c.label) + '" style="--c:' + COL[ci] + ';--on:' + ON[ci] + ';--t:' + TINT[ci] + '"' + pick + '><header><span>' + esc(c.label) + '</span>' + (counts ? '<span>' + total + '</span>' : '') + '</header><div class="wx-body">' + body + '</div></section>';
    });
    var ab = layout.abandoned, abN = 0;
    if (counts) ab.subs.forEach(function (b) { abN += counts[b] || 0; });
    var band = '<div class="wx-band wfstage' + (o.sel === ab.label || left ? ' on' : '') + '" data-stage="' + esc(ab.label) + '"' + pick + '><div class="t">' + esc(ab.label) + (counts ? ' · ' + abN : '') + '<small>leaves the flow from any step</small></div><div class="c">'
      + ab.subs.map(function (b) { return step(ab.label, b, 4, '–', false); }).join('') + '</div></div>';
    var cls = 'wx' + (D ? ' detailed' : ' simple') + (cur ? ' demand' : '') + (o.still ? ' still' : '');
    setTimeout(window.wfLinks, 0);
    return '<div class="' + cls + '"><div class="wx-scroll"><div class="wx-flow">' + h + '<svg class="wx-links" aria-hidden="true"></svg></div></div>' + band + '</div>'
      + (cur ? '<div class="wf-esc">' + (o.escalations && o.escalations.length
        ? '<strong>Open escalations:</strong> ' + o.escalations.map(function (e) { return '<span>⚠ ' + esc(e.t) + ' · L' + e.l + (e.l === 2 ? ' overdue' : '') + '</span>'; }).join(' ')
        : 'No open escalations on this demand.') + '</div>' : '');
  };

  /* The arrow from the last step of each stage into the first step of the next. Positions are measured,
     so this runs after a diagram is put on the page and again when the page changes size. */
  window.wfLinks = function () {
    document.querySelectorAll('.wx-flow').forEach(function (flow, f) {
      var svg = flow.querySelector('.wx-links'), o = flow.getBoundingClientRect(), cols = flow.querySelectorAll('.wx-stage'), h = '';
      if (!o.width) return;
      var moving = flow.closest('.wx').classList.contains('detailed') && !flow.closest('.wx').classList.contains('still');
      for (var i = 0; i < cols.length - 1; i++) {
        var m = cols[i].querySelectorAll('.wx-step.main'), a = m[m.length - 1].getBoundingClientRect(), b = cols[i + 1].querySelector('.wx-step.main').getBoundingClientRect();
        var x1 = a.right - o.left, y1 = a.top - o.top + 21, x2 = b.left - o.left, y2 = b.top - o.top + 21, xm = cols[i].getBoundingClientRect().right - o.left + 28;
        var up = y2 < y1 ? -1 : 1, r = Math.min(10, Math.abs(y2 - y1) / 2), id = 'wxl' + f + '-' + i;
        var d = 'M' + x1 + ' ' + y1 + 'H' + (xm - r) + 'Q' + xm + ' ' + y1 + ' ' + xm + ' ' + (y1 + up * r) + 'V' + (y2 - up * r) + 'Q' + xm + ' ' + y2 + ' ' + (xm + r) + ' ' + y2 + 'H' + (x2 - 12);
        h += '<path class="line" id="' + id + '" d="' + d + '" fill="none" stroke="#33373F" stroke-width="3"/>';
        if (moving) h += '<circle r="5.5" fill="#F17817"><animateMotion dur="2.4s" repeatCount="indefinite"><mpath href="#' + id + '"/></animateMotion></circle>';
        h += '<path d="M' + (x2 - 13) + ' ' + (y2 - 8) + 'L' + (x2 - 1) + ' ' + y2 + 'L' + (x2 - 13) + ' ' + (y2 + 8) + 'z" fill="#33373F"/>';
      }
      svg.innerHTML = h;
    });
  };
  var again;
  window.addEventListener('resize', function () { clearTimeout(again); again = setTimeout(window.wfLinks, 120); });
  window.addEventListener('load', window.wfLinks);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(window.wfLinks);
})();
