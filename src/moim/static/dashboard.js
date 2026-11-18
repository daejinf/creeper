/* Dashboard: drag-to-scroll for the group cards (from the original). Everything else lives in shell.js. */
(function () {
  var g = document.querySelector('.grid');
  if (!g) return;
  var down = false, moved = false, sx = 0, sl = 0;
  g.addEventListener('pointerdown', function (e) {
    if (e.pointerType !== 'mouse' || e.button !== 0 || g.scrollWidth <= g.clientWidth) return;
    down = true; moved = false; sx = e.clientX; sl = g.scrollLeft;
  });
  window.addEventListener('pointermove', function (e) {
    if (!down) return;
    if (Math.abs(e.clientX - sx) > 5) { moved = true; g.classList.add('dragging'); }
    if (moved) g.scrollLeft = sl - (e.clientX - sx);
  });
  window.addEventListener('pointerup', function () { down = false; g.classList.remove('dragging'); });
  g.addEventListener('click', function (e) { if (moved) { e.preventDefault(); moved = false; } }, true);
  g.addEventListener('dragstart', function (e) { e.preventDefault(); });
})();
