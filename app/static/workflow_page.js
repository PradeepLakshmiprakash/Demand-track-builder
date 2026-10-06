/* The detailed workflow page: draws the diagram with its detail, lets the steps arrive one after another,
   and Play walks one demand along the normal path, step by step. */
(function () {
  var W = window.WF, root = document.getElementById('wx-root');
  root.innerHTML = wfDiagram(W.layout, { counts: W.counts, esc: W.esc, detail: W.detail, still: W.still });
  var wx = root.querySelector('.wx');
  root.querySelectorAll('.wx-flow .wx-step, .wx-flow .wx-dec, .wx-flow .wx-arrow').forEach(function (el, i) { el.style.setProperty('--i', i); });
  setTimeout(wfLinks, 2800);  // once the steps have settled into place

  var play = document.getElementById('wx-play'), stopBtn = document.getElementById('wx-stop'), cap = document.getElementById('wx-caption');
  if (!play) return;
  var main = [].slice.call(root.querySelectorAll('.wx-flow .wx-step.main')), at = -1, timer = null;
  function $(id) { return document.getElementById(id); }
  function show(i) {
    main.forEach(function (el, k) { el.classList.toggle('active', k === i); el.classList.toggle('done', i >= 0 && k < i); });
    if (i < 0) return;
    var el = main[i], next = el.querySelector('.wx-next');
    var who = [].map.call(el.querySelectorAll('.wx-who'), function (w) { return w.textContent; });
    $('wx-cap-k').textContent = 'Step ' + (i + 1) + ' of ' + main.length + ' · ' + el.dataset.stage;
    $('wx-cap-t').textContent = el.dataset.sub;
    $('wx-cap-x').textContent = (who.length ? who.join(' and ') + (who.length > 1 ? ' act. ' : ' acts. ') : '') + (next ? 'Moves on when ' + next.textContent : 'The journey ends here.');
    $('wx-cap-bar').style.width = ((i + 1) / main.length * 100) + '%';
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function stop() {
    clearInterval(timer); timer = null; at = -1;
    wx.classList.remove('playing'); cap.hidden = true; stopBtn.hidden = true;
    show(-1); play.textContent = '▶ Play the journey';
  }
  function tick() {
    at++;
    if (at >= main.length) {
      clearInterval(timer); timer = null; play.textContent = '↻ Play again';
      main.forEach(function (el) { el.classList.remove('active'); el.classList.add('done'); });
      return;
    }
    show(at);
  }
  play.addEventListener('click', function () {
    clearInterval(timer); at = -1;
    wx.classList.add('playing'); cap.hidden = false; stopBtn.hidden = false; play.textContent = 'Playing…';
    tick(); timer = setInterval(tick, 2600);
  });
  stopBtn.addEventListener('click', stop);
})();
