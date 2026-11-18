/* Invite room. The server owns every fact (codes, members, who may create); this script only shows it.
   Server text goes in with textContent. Nothing is stored in the browser. */
var MoimCast = (function () {
  var cast = document.getElementById('cast');
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!cast || cast.hidden) return { look: function () {}, hop: function () {}, shy: function () {} };
  var chs = [].slice.call(cast.children);
  function look(x, y) {
    chs.forEach(function (el) {
      var r = el.getBoundingClientRect();
      var dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
      var d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 140);
      el.style.setProperty('--ex', (dx / d * 2.4 * k).toFixed(2) + 'px');
      el.style.setProperty('--ey', (dy / d * 2 * k).toFixed(2) + 'px');
    });
  }
  function hop() {
    if (reduce) return;
    cast.classList.remove('hop'); void cast.offsetWidth; cast.classList.add('hop');
  }
  function shy() {
    cast.classList.add('shy');
    setTimeout(function () { cast.classList.remove('shy'); }, 700);
  }
  if (!reduce) {
    addEventListener('pointermove', function (e) { look(e.clientX, e.clientY); });
    chs.forEach(function (el) {
      (function loop() {
        setTimeout(function () {
          el.classList.add('blink');
          setTimeout(function () { el.classList.remove('blink'); loop(); }, 140);
        }, 2500 + Math.random() * 4000);
      })();
    });
  }
  return { look: look, hop: hop, shy: shy };
})();

(function () {
  var LEN = 6;
  var wrap = document.getElementById('wrap');
  var mode = wrap.getAttribute('data-mode');
  var gid = wrap.getAttribute('data-group');
  var codeEl = document.getElementById('code');
  var msg = document.getElementById('msg');
  var inputs = [];
  var busy = false;

  function $(id) { return document.getElementById(id); }

  // ---- 토스트 ----
  var toastEl = $('toast'), toastTimer;
  function showToast(text, isFail) {
    toastEl.textContent = text;
    toastEl.classList.toggle('fail', !!isFail);
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 2200);
  }

  // ---- 초대코드 입력 ----
  function value() { return inputs.map(function (n) { return n.value; }).join(''); }
  function setState(state, text) {
    if (!codeEl) { msg.textContent = text || ''; msg.className = 'msg' + (state === 'bad' ? ' err' : state === 'ok' ? ' ok' : ''); return; }
    codeEl.classList.remove('ok', 'bad');
    msg.className = 'msg';
    if (state) { codeEl.classList.add(state); msg.classList.add(state === 'ok' ? 'ok' : 'err'); }
    msg.textContent = text || '';
  }
  function resetInputs() {
    inputs.forEach(function (n) { n.value = ''; });
    if (codeEl) codeEl.classList.remove('ok', 'bad');
  }
  function flash(text) {
    setState(null, '');
    if (codeEl) void codeEl.offsetWidth;
    setState('bad', text);
    MoimCast.shy();
  }

  function check() {
    var v = value();
    if (v.length < LEN) { setState(null, ''); return; }
    if (busy) return;
    busy = true;
    MoimAuth.post('/api/groups/join', { code: v }).then(function (r) {
      busy = false;
      if (r.ok) {
        setState('ok', r.data.joined ? '참여했어요' : '이미 참여한 초대방이에요');
        MoimCast.hop();
        setTimeout(function () { MoimAuth.go(r.data.redirect); }, 500);
        return;
      }
      resetInputs();
      flash(r.message);
      inputs[0].focus();
    });
  }
  function fill(str) {
    var digits = str.replace(/\D/g, '').slice(0, LEN).split('');
    inputs.forEach(function (n, k) { n.value = digits[k] || ''; });
    inputs[Math.min(digits.length, LEN - 1)].focus();
    check();
  }

  if (codeEl) {
    for (var i = 0; i < LEN; i++) {
      var inp = document.createElement('input');
      inp.type = 'text';
      inp.inputMode = 'numeric';
      inp.autocomplete = i === 0 ? 'one-time-code' : 'off';
      inp.maxLength = 1;
      inp.placeholder = ' ';
      inp.setAttribute('aria-label', '초대코드 ' + (i + 1) + '번째 자리');
      codeEl.appendChild(inp);
      inputs.push(inp);
    }
    inputs.forEach(function (inp, idx) {
      inp.addEventListener('input', function () {
        var d = inp.value.replace(/\D/g, '');
        if (d.length > 1) { fill(value() + d); return; }
        inp.value = d;
        if (d && idx < LEN - 1) inputs[idx + 1].focus();
        var r = inp.getBoundingClientRect(); MoimCast.look(r.left + r.width / 2, r.top + r.height / 2);
        check();
      });
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Backspace' && !inp.value && idx > 0) { inputs[idx - 1].focus(); inputs[idx - 1].value = ''; check(); e.preventDefault(); }
        else if (e.key === 'ArrowLeft' && idx > 0) { inputs[idx - 1].focus(); e.preventDefault(); }
        else if (e.key === 'ArrowRight' && idx < LEN - 1) { inputs[idx + 1].focus(); e.preventDefault(); }
      });
      inp.addEventListener('focus', function () { inp.select(); });
      inp.addEventListener('paste', function (e) {
        e.preventDefault();
        fill((e.clipboardData || window.clipboardData).getData('text'));
      });
    });
    inputs[0].focus();
  }

  // ---- 새 그룹 만들기 (초대방이 없는 사용자) ----
  var newRoom = $('newRoom');
  if (newRoom) {
    newRoom.addEventListener('click', function () {
      if (newRoom.disabled) return;
      newRoom.disabled = true;
      MoimAuth.post('/api/groups', {}).then(function (r) {
        if (r.ok) { MoimAuth.go(r.data.redirect); return; }
        flash(r.message);
        newRoom.disabled = false;
      });
    });
  }

  // ---- 초대방 화면 (그룹장·대기자) ----
  if (!gid) return;
  var chips = $('chips'), cap = $('cap'), genEl = $('genCode'), codeMsg = $('codeMsg');
  var createBtn = $('create'), needMsg = $('needMsg');
  var current = null;

  function chip(m) {
    var c = document.createElement('span');
    c.className = 'chip k-' + m.shape;
    var av = document.createElement('span');
    av.className = 'av ' + m.shape;
    av.setAttribute('aria-hidden', 'true');
    av.appendChild(document.createElement('i'));
    av.appendChild(document.createElement('i'));
    c.appendChild(av);
    c.appendChild(document.createTextNode(m.is_me ? '나' : m.name));
    return c;
  }

  function render(d) {
    current = d;
    if (d.status === 'ACTIVE' && d.next) { MoimAuth.go(d.next); return; }
    if (d.status === 'CANCELLED') {
      setState('bad', '초대방이 닫혔어요.');
      setTimeout(function () { location.reload(); }, 1500);
      return;
    }
    if (chips) {
      chips.textContent = '';
      d.members.forEach(function (m) { chips.appendChild(chip(m)); });
      cap.textContent = '함께할 팀원 ' + d.member_count + '명';
    }
    if (genEl) {
      genEl.textContent = d.code || '------';
      genEl.setAttribute('aria-label', d.code ? '초대코드 ' + d.code : '초대코드 없음');
      codeMsg.textContent = d.code_expired ? '초대코드가 만료됐어요. 새로 만들기를 눌러 주세요.' : '';
    }
    if (createBtn) {
      createBtn.disabled = !d.can_create;
      needMsg.textContent = d.can_create ? '' : d.need_members_message;
    }
  }

  function poll() {
    if (document.hidden) return;
    fetch('/api/groups/' + encodeURIComponent(gid) + '/room', { credentials: 'same-origin' })
      .then(function (res) {
        if (res.status === 404 || res.status === 401) { location.reload(); return null; }
        return res.ok ? res.json() : null;
      })
      .then(function (d) { if (d) render(d); }, function () {});
  }
  setInterval(poll, 4000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });

  if (createBtn) {
    // 그룹 생성하기는 상태를 바꾸지 않아요: 초대방(FORMING)을 그대로 둔 채 ProjectBrief 화면으로 이동만 해요.
    createBtn.addEventListener('click', function () {
      if (createBtn.disabled) return;
      MoimAuth.go('/groups/' + gid + '/create');
    });
  }

  var copyBtn = $('copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      var code = current && current.code;
      if (!code) { showToast('복사할 초대코드가 없어요.', true); return; }
      function ok() {
        copyBtn.querySelector('span').textContent = '복사됨'; copyBtn.classList.add('copied');
        setTimeout(function () { copyBtn.querySelector('span').textContent = '복사'; copyBtn.classList.remove('copied'); }, 1500);
        showToast('초대코드가 복사됐어요');
        MoimCast.hop();
      }
      function fail() { showToast('복사하지 못했어요. 코드를 직접 선택해 복사해 주세요.', true); }
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(code).then(ok, fail);
        else fail();
      } catch (e) { fail(); }
    });
  }

  var regen = $('regen');
  if (regen) {
    regen.addEventListener('click', function () {
      if (regen.disabled) return;
      regen.disabled = true;
      MoimAuth.post('/api/groups/' + encodeURIComponent(gid) + '/code', {}).then(function (r) {
        regen.disabled = false;
        if (r.ok) { render(r.data); showToast('새 초대코드를 만들었어요'); MoimCast.hop(); return; }
        showToast(r.message, true);
      });
    });
  }

  // 초대방 닫기는 되돌릴 수 없어서 두 번 눌러야 해요.
  var cancel = $('cancelRoom');
  if (cancel) {
    var armed = false, armTimer;
    var label = cancel.querySelector('span');
    cancel.addEventListener('click', function () {
      if (!armed) {
        armed = true;
        label.textContent = '한 번 더 누르면 닫혀요';
        cancel.classList.add('warn');
        armTimer = setTimeout(function () { armed = false; label.textContent = '초대방 닫기'; cancel.classList.remove('warn'); }, 4000);
        return;
      }
      clearTimeout(armTimer);
      cancel.disabled = true;
      MoimAuth.post('/api/groups/' + encodeURIComponent(gid) + '/cancel', { confirm: true }).then(function (r) {
        if (r.ok) { MoimAuth.go(r.data.redirect); return; }
        cancel.disabled = false; armed = false; label.textContent = '초대방 닫기'; cancel.classList.remove('warn');
        showToast(r.message, true);
      });
    });
  }

  var seed = $('roomData');
  if (seed) { try { current = JSON.parse(seed.textContent); } catch (e) {} }
})();
