/* The workflow diagram: each main stage a container, each sub-stage a box, arrows in the order a demand
   moves. wfDiagram(layout, {counts}) puts the number of demands on each box (account overview);
   wfDiagram(layout, {current}) ticks the steps one demand has passed and fills in where it is now.
   Open escalations ride on the box they belong to: {esc: {sub: n}} on the overview, {escalations: [...]}
   for one demand.
   The layout comes from workflow_service.layout(). */
(function () {
  var COL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'];
  var TINT = ['#EAF2FC', '#FDEFE8', '#E6F6EF', '#FDF4DD', '#FCEEF3'];
  var INK = '#1B1B18', MUTED = '#6B675E', WARN = '#B4532A';
  var BW = 150, BH = 40, TOP = 60, GAP = 58;
  // container x, container width, main column x, problem column x (first stage only)
  var GEO = [{ x: 10, w: 340, mx: 185, ex: 25 }, { x: 380, w: 180, mx: 395 }, { x: 590, w: 180, mx: 605 }, { x: 800, w: 180, mx: 815 }];

  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function lines(t) {
    if (t.length <= 17) return [t];
    var mid = t.length / 2, best = -1;
    for (var i = 0; i < t.length; i++) if (t[i] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
    return best < 0 ? [t] : [t.slice(0, best), t.slice(best + 1)];
  }

  window.wfDiagram = function (layout, o) {
    var cur = o.current, counts = o.counts, h = '', order = [], hangs = {};
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
      var fill = now ? COL[ci] : (done ? TINT[ci] : '#fff'), ink = now ? '#fff' : (dim ? '#8B877E' : INK);
      var s = '<g class="wfbox" data-stage="' + esc(stage) + '" data-sub="' + esc(sub) + '"' + (counts ? ' style="cursor:pointer"' : '') + '>';
      s += '<rect x="' + x + '" y="' + y + '" width="' + BW + '" height="' + BH + '" rx="4" fill="' + fill + '" stroke="' + (sel || now ? INK : (problem ? WARN : '#C9C4B8')) + '" stroke-width="' + (sel || now ? 2 : 1) + '"' + (problem && !now ? ' stroke-dasharray="4 3"' : '') + '/>';
      if (!now) s += '<rect x="' + x + '" y="' + y + '" width="5" height="' + BH + '" rx="2" fill="' + (problem ? WARN : COL[ci]) + '" opacity="' + (dim ? .35 : 1) + '"/>';
      var ls = lines(sub), ty = y + (ls.length === 1 ? 24 : 17);
      ls.forEach(function (l, k) { s += '<text x="' + (x + 13) + '" y="' + (ty + k * 13) + '" font-size="11" fill="' + ink + '"' + (now ? ' font-weight="700"' : '') + '>' + esc(l) + '</text>'; });
      if (counts && n) s += '<circle cx="' + (x + BW - 16) + '" cy="' + (y + 20) + '" r="11" fill="' + COL[ci] + '"/><text x="' + (x + BW - 16) + '" y="' + (y + 24) + '" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">' + n + '</text>';
      if (done) s += '<text x="' + (x + BW - 14) + '" y="' + (y + 25) + '" text-anchor="middle" font-size="13" font-weight="700" fill="' + COL[ci] + '">✓</text>';
      if (now) s += '<text x="' + (x + BW - 14) + '" y="' + (y + 25) + '" text-anchor="middle" font-size="12" fill="#fff">●</text>';
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
      h += '<text x="' + (g.x + 12) + '" y="31" font-size="12.5" font-weight="700" fill="#fff">' + esc(c.label) + '</text>';
      if (counts) h += '<text x="' + (g.x + g.w - 12) + '" y="31" text-anchor="end" font-size="13" font-weight="700" fill="#fff">' + total + '</text>';
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
    h += '<text x="22" y="' + (AY + 28) + '" font-size="12.5" font-weight="700" fill="#fff">' + esc(ab.label) + (counts ? ' · ' + abN : '') + '</text><text x="22" y="' + (AY + 46) + '" font-size="10.5" fill="#fff">leaves the flow</text></g>';
    ab.subs.forEach(function (b, k) { h += box(ab.label, b, 185 + k * 170, AY + 12, 4, false); });
    h += '<text x="' + (185 + ab.subs.length * 170 + 5) + '" y="' + (AY + 36) + '" font-size="11.5" fill="' + MUTED + '">A demand can be cancelled or closed from any step above.</text>';
    return '<svg viewBox="0 0 990 ' + (AY + 74) + '" width="100%" role="img" aria-label="Workflow diagram" style="min-width:860px">' + h + '</svg>'
      + '<div class="wf-key">Solid boxes are the normal path, top to bottom then left to right. Dashed boxes are problem states a demand can fall into at that step.'
      + (cur ? ' ✓ = already passed · ● = where it is now.' : ' The number on a box is how many demands are there now; click a box or a stage to see them.')
      + ' ⚠ = open escalations at that step.</div>'
      + (cur ? '<div class="wf-esc">' + (o.escalations && o.escalations.length
        ? '<strong>Open escalations:</strong> ' + o.escalations.map(function (e) { return '<span>⚠ ' + esc(e.t) + ' · L' + e.l + (e.l === 2 ? ' overdue' : '') + '</span>'; }).join(' ')
        : 'No open escalations on this demand.') + '</div>' : '');
  };
})();
