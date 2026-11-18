  // 카드가 자동으로 흐르고, 드래그·스와이프·트랙패드로 좌우 이동도 됩니다.
  (function () {
    var track = document.getElementById('track');
    var marquee = track && track.parentElement;
    if (!track) return;

    // 끊김 없이 이어지도록 카드 묶음을 한 번 더 붙임 (복제본은 스크린리더에서 숨김)
    var originals = Array.prototype.slice.call(track.children);
    originals.forEach(function (card) {
      var copy = card.cloneNode(true);
      copy.setAttribute('aria-hidden', 'true');
      track.appendChild(copy);
    });

    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var SPEED = reduce ? 0 : 38;   // 자동 흐름 속도 (px/초)
    var loop = 0, x = 0, vel = 0;
    var hovering = false, dragging = false;
    var lastX = 0, lastT = 0, prev = 0;

    function measure() { loop = track.children[originals.length].offsetLeft - track.children[0].offsetLeft; }
    function wrap() { if (loop > 0) { x = x % loop; if (x > 0) x -= loop; } }
    function paint() { track.style.transform = 'translate3d(' + x + 'px,0,0)'; }

    function frame(t) {
      var dt = prev ? Math.min((t - prev) / 1000, 0.05) : 0;
      prev = t;
      if (!dragging) {
        if (!hovering) x -= SPEED * dt;      // 자동 흐름
        x += vel * dt;                       // 손을 뗀 뒤 관성
        vel *= Math.pow(0.04, dt);           // 관성 감속
        if (Math.abs(vel) < 2) vel = 0;
      }
      wrap(); paint();
      requestAnimationFrame(frame);
    }

    // 마우스를 올리면 잠시 멈춤 (터치 기기에서는 적용 안 함)
    marquee.addEventListener('pointerenter', function (e) { if (e.pointerType === 'mouse') hovering = true; });
    marquee.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') hovering = false; });

    // 드래그 / 스와이프
    marquee.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      dragging = true; vel = 0;
      lastX = e.clientX; lastT = performance.now();
      marquee.classList.add('dragging');
      try { marquee.setPointerCapture(e.pointerId); } catch (err) {}
    });
    marquee.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var now = performance.now();
      var dx = e.clientX - lastX;
      var dts = Math.max((now - lastT) / 1000, 0.001);
      x += dx;
      vel = vel * 0.6 + (dx / dts) * 0.4;
      lastX = e.clientX; lastT = now;
      wrap(); paint();
    });
    function release() {
      if (!dragging) return;
      dragging = false;
      marquee.classList.remove('dragging');
      if (performance.now() - lastT > 80) vel = 0;   // 멈췄다 뗀 경우 관성 없음
      vel = Math.max(-2400, Math.min(2400, vel));
    }
    marquee.addEventListener('pointerup', release);
    marquee.addEventListener('pointercancel', release);

    // 트랙패드 좌우 스크롤
    marquee.addEventListener('wheel', function (e) {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        x -= e.deltaX; vel = 0;
      }
    }, { passive: false });

    window.addEventListener('resize', measure);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
    measure(); paint();
    requestAnimationFrame(frame);
  })();
  // 캐릭터 눈이 커서를 따라보고, 버튼에 올리면 다 같이 폴짝
  (function () {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var shapes = Array.prototype.slice.call(document.querySelectorAll('.cast .shape'));
    var cast = document.getElementById('cast');
    var cta = document.getElementById('cta');
    var mx = null, my = null, raf = 0;
    function look() {
      raf = 0;
      shapes.forEach(function (sh) {
        if (mx === null) { sh.style.setProperty('--ex', '0px'); sh.style.setProperty('--ey', '0px'); return; }
        var r = sh.getBoundingClientRect();
        var dx = mx - (r.left + r.width / 2), dy = my - (r.top + r.height / 2);
        var d = Math.hypot(dx, dy);
        if (d < r.width * 0.45) { dx = 0; dy = 0; d = 1; }          // 나를 보면 정면
        var max = Math.max(2.5, r.width * 0.035);
        sh.style.setProperty('--ex', (dx / d * max).toFixed(2) + 'px');
        sh.style.setProperty('--ey', (dy / d * max).toFixed(2) + 'px');
      });
    }
    window.addEventListener('pointermove', function (e) { mx = e.clientX; my = e.clientY; if (!raf) raf = requestAnimationFrame(look); });
    document.addEventListener('pointerleave', function () { mx = null; look(); });
    window.addEventListener('blur', function () { mx = null; look(); });

    var btn = cta.querySelector('.btn');
    function hop() {
      cast.classList.remove('hop'); void cast.offsetWidth; cast.classList.add('hop');
    }
    btn.addEventListener('pointerenter', hop);
    btn.addEventListener('focus', hop);
    cast.addEventListener('animationend', function (e) { if (e.target.closest('.member:last-of-type')) cast.classList.remove('hop'); });
  })();
