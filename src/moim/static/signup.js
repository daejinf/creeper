(function () {
  // 비밀번호 표시/숨기기 (두 비밀번호 칸 함께 전환)
  var pw = document.getElementById('su-pw');
  var pw2 = document.getElementById('su-pw2');
  var eye = document.getElementById('su-eye');
  var wrap = document.querySelector('.shapes');
  function pwFocused() { var a = document.activeElement; return a === pw || a === pw2; }
  eye.addEventListener('click', function () {
    var show = pw.type === 'password';
    pw.type = pw2.type = show ? 'text' : 'password';
    eye.classList.toggle('on', show);
    eye.setAttribute('aria-pressed', show ? 'true' : 'false');
    eye.setAttribute('aria-label', show ? '비밀번호 숨기기' : '비밀번호 표시');
    wrap.classList.toggle('shy', !show && pwFocused());
  });

  // 아이디 중복 확인
  var idInput = document.getElementById('su-id');
  var msg = document.getElementById('su-id-msg');
  function setStatus(state, text) {
    idInput.classList.toggle('ok', state === 'ok');
    idInput.classList.toggle('bad', state === 'bad');
    idInput.setAttribute('aria-invalid', state === 'bad' ? 'true' : 'false');
    msg.classList.toggle('ok', state === 'ok');
    msg.textContent = text || '';
  }
  idInput.addEventListener('input', function () { setStatus('idle', ''); });
  var checkBtn = document.getElementById('su-check');
  checkBtn.addEventListener('click', function () {
    var v = idInput.value.trim();
    if (!v) return setStatus('bad', '아이디를 입력해주세요.');
    if (!/^[a-z0-9]{4,16}$/.test(v)) return setStatus('bad', '영문 소문자와 숫자 4~16자로 입력해주세요.');
    checkBtn.disabled = true;
    MoimAuth.post('/api/auth/username-check', { username: v }).then(function (r) {
      checkBtn.disabled = false;
      if (!r.ok) return setStatus('bad', r.message);
      setStatus(r.data.status === 'available' ? 'ok' : 'bad', r.data.message);
    });
  });

  // 캐릭터: 커서 따라보기 · 깜빡임 · 비밀번호 입력 땐 눈 감기
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
  [document.getElementById('su-name'), idInput].forEach(function (inp) {
    inp.addEventListener('input', function () {
      var r = inp.getBoundingClientRect();
      look(r.left + 16 + Math.min(inp.value.length * 8, r.width - 32), r.top + r.height / 2);
    });
  });
  [pw, pw2].forEach(function (inp) {
    inp.addEventListener('focus', function () { if (inp.type === 'password') wrap.classList.add('shy'); });
    inp.addEventListener('blur', function () { setTimeout(function () { if (!pwFocused()) wrap.classList.remove('shy'); }); });
  });

  var form = document.getElementById('signup-form');
  var nameIn = document.getElementById('su-name');
  var terms = document.getElementById('su-terms');
  var noti = document.getElementById('su-noti');
  var submit = form.querySelector('button[type=submit]');
  var slots = {
    name: document.getElementById('su-name-msg'),
    password: document.getElementById('su-pw-msg'),
    password_confirm: document.getElementById('su-pw2-msg'),
    terms: document.getElementById('su-terms-msg'),
    form: document.getElementById('su-form-msg')
  };
  function clearErrors() {
    Object.keys(slots).forEach(function (k) { slots[k].textContent = ''; });
  }
  function showError(field, text) {
    if (field === 'username') return setStatus('bad', text);
    (slots[field] || slots.form).textContent = text;
  }
  function hop() {
    if (reduce) return;
    wrap.classList.remove('hop'); void wrap.offsetWidth; wrap.classList.add('hop');
  }
  // 서버가 같은 규칙으로 다시 검증한다. 여기서는 빠른 안내만 한다.
  function validate() {
    var name = nameIn.value.trim();
    if (!name) return ['name', '이름을 입력해주세요.'];
    if (name.length > 30) return ['name', '이름은 30자 이하로 입력해주세요.'];
    if (!idInput.value.trim()) return ['username', '아이디를 입력해주세요.'];
    if (!/^[a-z0-9]{4,16}$/.test(idInput.value.trim())) return ['username', '영문 소문자와 숫자 4~16자로 입력해주세요.'];
    if (pw.value.length < 8 || !/[A-Za-z]/.test(pw.value) || !/[0-9]/.test(pw.value)) return ['password', '비밀번호는 8자 이상, 영문과 숫자를 포함해야 해요.'];
    if (pw.value !== pw2.value) return ['password_confirm', '비밀번호가 서로 달라요.'];
    if (!terms.checked) return ['terms', '이용약관 및 개인정보 처리방침에 동의해주세요.'];
    return null;
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (submit.disabled) return;
    clearErrors();
    var problem = validate();
    if (problem) return showError(problem[0], problem[1]);
    submit.disabled = true; submit.textContent = '가입 중…';
    MoimAuth.post('/api/auth/signup', {
      name: nameIn.value, username: idInput.value, password: pw.value, password_confirm: pw2.value,
      terms: terms.checked, notifications: noti.checked
    }).then(function (r) {
      if (r.ok) { hop(); MoimAuth.go(r.data.redirect); return; }
      showError(r.field || 'form', r.message);
      submit.disabled = false; submit.textContent = '회원가입';
    });
  });
})();
