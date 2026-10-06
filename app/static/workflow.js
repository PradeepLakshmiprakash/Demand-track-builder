/* The workflow diagram: each main stage a container, each sub-stage a box, arrows in the order a demand
   moves. wfDiagram(layout, {counts}) puts the number of demands on each box (account overview);
   wfDiagram(layout, {current}) ticks the steps one demand has passed and fills in where it is now.
   Open escalations ride on the box they belong to: {esc: {sub: n}} on the overview, {escalations: [...]}
   for one demand.
   The layout comes from workflow_service.layout(). */
(function () {
  // One blue, light to dark, while a demand is in progress; green once joined; grey if abandoned.
  var COL = ['#8EA6D5', '#3573C0', '#1C4076', '#43A063', '#A6ACB5'];
  var TINT = ['#EFF0F4', '#E9EDF3', '#DFE1EB', '#E7F6EB', '#F1F4F7'];
  var ON = ['#121A38', '#fff', '#fff', '#fff', '#171A22'];  // text that sits on each colour
  var INK = '#171A22', MUTED = '#595E6A', WARN = '#C00036';
  var BW = 150, BH = 40, TOP = 60, GAP = 58;
  // container x, container width, main column x, problem column x (first stage only)
  var GEO = [{ x: 10, w: 340, mx: 185, ex: 25 }, { x: 380, w: 180, mx: 395 }, { x: 590, w: 180, mx: 605 }, { x: 800, w: 180, mx: 815 }];

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

  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function lines(t) {
    if (t.length <= 17) return [t];
    var mid = t.length / 2, best = -1;
    for (var i = 0; i < t.length; i++) if (t[i] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
    return best < 0 ? [t] : [t.slice(0, best), t.slice(best + 1)];
  }

  /* How to read the diagram, for an info mark beside its heading. */
  window.wfKey = function (single) {
    return 'Solid boxes represent the standard path, read top to bottom within a stage and then left to right across stages. Dashed boxes represent exception states that a demand may enter at the corresponding step.'
      + (single ? ' A tick marks a step already completed; the filled box marks the current step.' : ' The figure on each box is the number of demands currently at that step; select a box or a stage header to list them.')
      + ' A warning badge shows the number of open escalations at that step.';
  };

  window.wfDiagram = function (layout, o) {
    var cur = o.current, counts = o.counts, h = '', order = [], hangs = {}, SUBC = window.wfColours(layout).sub;
    layout.stages.forEach(function (c) {
      c.subs.forEach(function (b) { order.push(b); });
      c.problems.forEach(function (e) { hangs[e[0]] = c.subs[e[1]]; });
    });
    var left = cur && cur.stage === layout.abandoned.label;
    var reached = cur && !left ? order.indexOf(hangs[cur.sub] || cur.sub) : -1;
    var rows = Math.max.apply(null, layout.stages.map(function (c) { return c.subs.length; }));
    var H = TOP + rows * GAP + 2, AY = H + 26;  // container bottom, abandoned band top

    function box(stage, sub, x, y, ci, problem) {
      var n = counts ? (counts[sub] || 0) : 0, idx = order.indexOf(sub);
      var now = cur && cur.sub === sub && cur.stage === stage;
      var done = cur && !left && !problem && idx >= 0 && (idx < reached || (idx === reached && hangs[cur.sub]));
      var dim = counts ? !n : (cur && !now && !done), sel = o.sel === sub;
      var mine = SUBC[sub] || COL[ci];
      var fill = now ? mine : (done ? TINT[ci] : '#fff'), ink = now ? inkOn(mine) : (dim ? '#888F9A' : INK);
      var s = '<g class="wfbox" data-stage="' + esc(stage) + '" data-sub="' + esc(sub) + '"' + (counts ? ' style="cursor:pointer"' : '') + '>';
      s += '<rect x="' + x + '" y="' + y + '" width="' + BW + '" height="' + BH + '" rx="4" fill="' + fill + '" stroke="' + (sel || now ? INK : (problem ? WARN : '#C7CCD3')) + '" stroke-width="' + (sel || now ? 2 : 1) + '"' + (problem && !now ? ' stroke-dasharray="4 3"' : '') + '/>';
      if (!now) s += '<rect x="' + x + '" y="' + y + '" width="5" height="' + BH + '" rx="2" fill="' + mine + '" opacity="' + (dim ? .45 : 1) + '"/>';
      var ls = lines(sub), ty = y + (ls.length === 1 ? 24 : 17);
      ls.forEach(function (l, k) { s += '<text x="' + (x + 13) + '" y="' + (ty + k * 13) + '" font-size="11" fill="' + ink + '"' + (now ? ' font-weight="700"' : '') + '>' + esc(l) + '</text>'; });
      if (counts && n) s += '<circle cx="' + (x + BW - 16) + '" cy="' + (y + 20) + '" r="11" fill="' + mine + '" stroke="rgba(0,0,0,.18)" stroke-width="1"/><text x="' + (x + BW - 16) + '" y="' + (y + 24) + '" text-anchor="middle" font-size="11" font-weight="700" fill="' + inkOn(mine) + '">' + n + '</text>';
      if (done) s += '<text x="' + (x + BW - 14) + '" y="' + (y + 25) + '" text-anchor="middle" font-size="13" font-weight="700" fill="' + COL[ci] + '">✓</text>';
      if (now) s += '<text x="' + (x + BW - 14) + '" y="' + (y + 25) + '" text-anchor="middle" font-size="12" fill="' + inkOn(mine) + '">●</text>';
      var en = o.esc ? (o.esc[sub] || 0) : (now && o.escalations ? o.escalations.length : 0);
      if (en) s += '<rect x="' + (x + BW - 46) + '" y="' + (y - 9) + '" width="40" height="17" rx="8.5" fill="' + WARN + '" stroke="#fff" stroke-width="1.5"/><text x="' + (x + BW - 26) + '" y="' + (y + 3.5) + '" text-anchor="middle" font-size="10.5" font-weight="700" fill="#fff">⚠ ' + en + '</text><title>' + en + ' open escalation' + (en === 1 ? '' : 's') + '</title>';
      return s + '</g>';
    }

    h += '<defs><marker id="wfa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="' + MUTED + '"/></marker></defs>';
    layout.stages.forEach(function (c, ci) {
      var g = GEO[ci], total = 0;
      if (counts) c.subs.concat(c.problems.map(function (e) { return e[0]; })).forEach(function (b) { total += counts[b] || 0; });
      var on = o.sel === c.label || (cur && cur.stage === c.label);
      h += '<g class="wfstage" data-stage="' + esc(c.label) + '"' + (counts ? ' style="cursor:pointer"' : '') + '><rect x="' + g.x + '" y="10" width="' + g.w + '" height="' + (H - 10) + '" rx="6" fill="' + TINT[ci] + '" stroke="' + COL[ci] + '" stroke-width="' + (on ? 2.5 : 1) + '"/>';
      h += '<rect x="' + g.x + '" y="10" width="' + g.w + '" height="32" rx="6" fill="' + COL[ci] + '"/><rect x="' + g.x + '" y="30" width="' + g.w + '" height="12" fill="' + COL[ci] + '"/>';
      h += '<text x="' + (g.x + 12) + '" y="31" font-size="12.5" font-weight="700" fill="' + ON[ci] + '">' + esc(c.label) + '</text>';
      if (counts) h += '<text x="' + (g.x + g.w - 12) + '" y="31" text-anchor="end" font-size="13" font-weight="700" fill="' + ON[ci] + '">' + total + '</text>';
      h += '</g>';
      c.subs.forEach(function (b, k) {
        var y = TOP + k * GAP;
        if (k) h += '<line x1="' + (g.mx + BW / 2) + '" y1="' + (y - GAP + BH) + '" x2="' + (g.mx + BW / 2) + '" y2="' + (y - 2) + '" stroke="' + MUTED + '" stroke-width="1.3" marker-end="url(#wfa)"/>';
        h += box(c.label, b, g.mx, y, ci, false);
      });
      c.problems.forEach(function (e) {
        var y = TOP + e[1] * GAP;
        h += '<line x1="' + g.mx + '" y1="' + (y + 20) + '" x2="' + (g.ex + BW + 2) + '" y2="' + (y + 20) + '" stroke="' + WARN + '" stroke-width="1.2" stroke-dasharray="4 3" marker-end="url(#wfa)" marker-start="url(#wfa)"/>';
        h += box(c.label, e[0], g.ex, y, ci, true);
      });
      if (ci < layout.stages.length - 1) {  // last step of this stage → first step of the next, as an elbow
        var y1 = TOP + (c.subs.length - 1) * GAP + 20, xm = g.x + g.w + 15;
        h += '<path d="M' + (g.mx + BW) + ' ' + y1 + 'H' + xm + 'V' + (TOP + 20) + 'H' + (GEO[ci + 1].mx - 2) + '" fill="none" stroke="' + MUTED + '" stroke-width="1.3" marker-end="url(#wfa)"/>';
      }
    });
    var ab = layout.abandoned, abN = 0;
    if (counts) ab.subs.forEach(function (b) { abN += counts[b] || 0; });
    h += '<line x1="495" y1="' + (H + 2) + '" x2="495" y2="' + (AY - 2) + '" stroke="' + MUTED + '" stroke-width="1.2" stroke-dasharray="4 3" marker-end="url(#wfa)"/>';
    h += '<g class="wfstage" data-stage="' + esc(ab.label) + '"' + (counts ? ' style="cursor:pointer"' : '') + '><rect x="10" y="' + AY + '" width="970" height="64" rx="6" fill="' + TINT[4] + '" stroke="' + COL[4] + '" stroke-width="' + (o.sel === ab.label || left ? 2.5 : 1) + '"/><rect x="10" y="' + AY + '" width="150" height="64" rx="6" fill="' + COL[4] + '"/>';
    h += '<text x="22" y="' + (AY + 28) + '" font-size="12.5" font-weight="700" fill="' + ON[4] + '">' + esc(ab.label) + (counts ? ' · ' + abN : '') + '</text><text x="22" y="' + (AY + 46) + '" font-size="10.5" fill="' + ON[4] + '">leaves the flow</text></g>';
    ab.subs.forEach(function (b, k) { h += box(ab.label, b, 185 + k * 170, AY + 12, 4, false); });
    h += '<text x="' + (185 + ab.subs.length * 170 + 5) + '" y="' + (AY + 36) + '" font-size="11.5" fill="' + MUTED + '">A demand can be cancelled or closed from any step above.</text>';
    return '<svg viewBox="0 0 990 ' + (AY + 74) + '" width="100%" role="img" aria-label="Workflow diagram" style="min-width:860px">' + h + '</svg>'
      + (cur ? '<div class="wf-esc">' + (o.escalations && o.escalations.length
        ? '<strong>Open escalations:</strong> ' + o.escalations.map(function (e) { return '<span>⚠ ' + esc(e.t) + ' · L' + e.l + (e.l === 2 ? ' overdue' : '') + '</span>'; }).join(' ')
        : 'No open escalations on this demand.') + '</div>' : '');
  };
})();
