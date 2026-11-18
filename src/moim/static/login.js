(function () {
  var pw = document.getElementById('login-pw');
  var eye = document.getElementById('login-eye');
  eye.addEventListener('click', function () {
    var show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    eye.classList.toggle('on', show);
    eye.setAttribute('aria-pressed', show ? 'true' : 'false');
    eye.setAttribute('aria-label', show ? '비밀번호 숨기기' : '비밀번호 표시');
  });

  // 캐릭터: 커서 따라보기 · 깜빡임 · 비밀번호 입력 땐 눈 감기
  var wrap = document.querySelector('.shapes');
  var shapes = [].slice.call(wrap.children);
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  function look(x, y) {
    shapes.forEach(function (el) {
      var r = el.getBoundingClientRect();
      var dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
      var d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 160);
      el.style.setProperty('--ex', (dx / d * 3 * k).toFixed(2) + 'px');
      el.style.setProperty('--ey', (dy / d * 2.5 * k).toFixed(2) + 'px');
    });
  }
  if (!reduce) {
    addEventListener('pointermove', function (e) { look(e.clientX, e.clientY); });
    shapes.forEach(function (el) {
      (function loop() {
        setTimeout(function () {
          el.classList.add('blink');
          setTimeout(function () { el.classList.remove('blink'); loop(); }, 140);
        }, 2500 + Math.random() * 4000);
      })();
    });
  }
  var idIn = document.getElementById('login-id');
  idIn.addEventListener('input', function () {
    var r = idIn.getBoundingClientRect();
    look(r.left + 16 + Math.min(idIn.value.length * 8, r.width - 32), r.top + r.height / 2);
  });
  pw.addEventListener('focus', function () { if (pw.type === 'password') wrap.classList.add('shy'); });
  pw.addEventListener('blur', function () { wrap.classList.remove('shy'); });
  eye.addEventListener('click', function () { wrap.classList.toggle('shy', pw.type === 'password' && document.activeElement === pw); });
  var form = document.getElementById('login-form');
  var msg = document.getElementById('login-msg');
  var keep = document.getElementById('login-keep');
  var submit = form.querySelector('button[type=submit]');
  function hop() {
    if (reduce) return;
    wrap.classList.remove('hop'); void wrap.offsetWidth; wrap.classList.add('hop');
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (submit.disabled) return;
    msg.textContent = '';
    var username = idIn.value.trim();
    if (!username) { msg.textContent = '아이디를 입력해주세요.'; idIn.focus(); return; }
    if (!pw.value) { msg.textContent = '비밀번호를 입력해주세요.'; pw.focus(); return; }
    submit.disabled = true; submit.textContent = '로그인 중…';
    MoimAuth.post('/api/auth/login', { username: username, password: pw.value, keep: keep.checked, next: form.dataset.next || null })
      .then(function (r) {
        if (r.ok) { hop(); MoimAuth.go(r.data.redirect); return; }
        msg.textContent = r.message;
        submit.disabled = false; submit.textContent = '로그인';
      });
  });

  // 비밀번호 찾기: 외부 이메일/SMS 계정이 승인되지 않아 제공하지 않는다. 지원 범위만 안내한다.
  var rt = document.getElementById('recovery-toggle');
  var rn = document.getElementById('recovery-note');
  rt.addEventListener('click', function (e) {
    e.preventDefault();
    rn.hidden = !rn.hidden;
    rt.setAttribute('aria-expanded', rn.hidden ? 'false' : 'true');
  });
})();
