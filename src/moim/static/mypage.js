/* Mypage (stage 6): the original moim-mypage behaviour wired to the server.
   - 내 정보: name change (POST /api/account/profile).
   - 비밀번호 변경: current password checked on the server; every other session ends, this one is renewed.
   - 참여 중인 그룹: rendered by the server template.
   - 계정 관리: logout (original confirm popup), account deletion with the current password and the impact summary
     from the server (D-D1 D7, D-F1). Deletion cannot be undone; nothing is shown as done until the server says so.
   Everything is built with DOM nodes and textContent. Nothing is stored in the browser. */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var post = window.MoimAuth.post;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var toastEl = $('toast'), toastT;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.classList.remove('on'); }, 2000);
  }
  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }, function () { return { ok: false, status: r.status, data: null }; });
    }, function () { return { ok: false, status: 0, data: null }; });
  }

  /* ---------- 메뉴(탭) ---------- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.menu [role="tab"]'));
  function openTab(btn) {
    tabs.forEach(function (b) {
      var on = b === btn;
      b.setAttribute('aria-selected', on);
      b.tabIndex = on ? 0 : -1;
      $(b.getAttribute('aria-controls')).hidden = !on;
    });
    // 비밀번호 변경을 열면 캐릭터가 눈을 감아요
    $('sideFace').classList.toggle('shut', btn.id === 't-pw');
    // 포트폴리오는 탭을 처음 열 때 불러와요 (static/portfolio.js)
    if (window.MoimPortfolio) window.MoimPortfolio.shown(btn.id === 't-pf');
  }
  tabs.forEach(function (b, i) {
    b.addEventListener('click', function () { openTab(b); });
    b.addEventListener('keydown', function (e) {
      var k = e.key, n = null;
      if (k === 'ArrowDown' || k === 'ArrowRight') n = tabs[(i + 1) % tabs.length];
      if (k === 'ArrowUp' || k === 'ArrowLeft') n = tabs[(i - 1 + tabs.length) % tabs.length];
      if (n) { e.preventDefault(); n.focus(); openTab(n); }
    });
  });
  // 주소 끝에 #pw, #grp, #acc를 붙이면 그 메뉴로 바로 열려요
  var startTab = /^#(info|pw|grp|acc|pf)$/.test(location.hash) ? $('t-' + location.hash.slice(1)) : null;
  if (startTab) openTab(startTab);

  function setHint(box, hint, msg, bad, base) {
    $(box).classList.toggle('bad', !!bad);
    var h = $(hint);
    h.textContent = bad ? msg : base;
    h.classList.toggle('err', !!bad);
  }

  /* ---------- 내 정보 ---------- */
  var nameIn = $('name');
  var savedName = nameIn.getAttribute('data-saved') || '';
  var busyInfo = false;
  function checkInfo() {
    var n = nameIn.value.trim();
    var nameBad = n.length < 2;
    setHint('nameBox', 'nameHint', '이름은 2자 이상으로 정해 주세요.', nameBad && nameIn.value !== '', '');
    $('saveInfo').disabled = busyInfo || n === savedName || nameBad;
  }
  nameIn.addEventListener('input', checkInfo);
  $('saveInfo').addEventListener('click', function () {
    busyInfo = true; checkInfo();
    post('/api/account/profile', { name: nameIn.value }).then(function (r) {
      busyInfo = false;
      if (r.ok) {
        savedName = r.data.name;
        nameIn.value = savedName;
        $('sideName').textContent = savedName;
        checkInfo();
        toast('내 정보를 저장했어요');
      } else {
        checkInfo();
        setHint('nameBox', 'nameHint', r.message, true, '');
      }
    });
  });

  /* ---------- 비밀번호 ---------- */
  function eyeIcon(open) {
    var s = document.createElementNS(SVGNS, 'svg');
    [['width', '18'], ['height', '18'], ['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'],
     ['stroke-width', '2'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]
      .forEach(function (a) { s.setAttribute(a[0], a[1]); });
    if (open) {
      var p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z'); s.appendChild(p);
      var c = document.createElementNS(SVGNS, 'circle'); c.setAttribute('cx', '12'); c.setAttribute('cy', '12'); c.setAttribute('r', '3'); s.appendChild(c);
    } else {
      var q = document.createElementNS(SVGNS, 'path');
      q.setAttribute('d', 'M3 3l18 18M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a10 10 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2');
      s.appendChild(q);
    }
    return s;
  }
  function paintEye(b, show) {
    while (b.firstChild) b.removeChild(b.firstChild);
    b.appendChild(eyeIcon(show));   // 보이면 그냥 눈, 가려지면 빗금 친 눈
    b.setAttribute('aria-label', show ? '비밀번호 숨기기' : '비밀번호 보기');
  }
  Array.prototype.forEach.call(document.querySelectorAll('.eye'), function (b) {
    paintEye(b, false);               // 처음엔 가려져 있으니 빗금 친 눈
    b.addEventListener('click', function () {
      var inp = $(b.dataset.for), show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      paintEye(b, show);
    });
  });

  var pw0 = $('pw0'), pw1 = $('pw1'), pw2 = $('pw2');
  var busyPw = false;
  function rule(r, ok) { document.querySelector('#rules [data-r="' + r + '"]').classList.toggle('ok', ok); }
  function checkPw(keepErr) {
    var a = pw0.value, b = pw1.value, c = pw2.value;
    var len = b.length >= 8, mix = /[A-Za-z]/.test(b) && /\d/.test(b);
    rule('len', len); rule('mix', mix);
    var match = c === b;
    setHint('pw2Box', 'pw2Hint', '불일치', c !== '' && !match, c && match && b ? '일치' : '');
    $('pw2Hint').classList.toggle('ok', !!(c && match && b));
    if (!keepErr) setHint('pw0Box', 'pw0Hint', '', false, '');
    $('savePw').disabled = busyPw || !(a && len && mix && c && match);
  }
  [pw0, pw1, pw2].forEach(function (i) { i.addEventListener('input', function () { checkPw(false); }); });
  $('savePw').addEventListener('click', function () {
    busyPw = true; checkPw(false);
    post('/api/account/password', { current_password: pw0.value, new_password: pw1.value, new_password_confirm: pw2.value })
      .then(function (r) {
        busyPw = false;
        if (r.ok) {
          [pw0, pw1, pw2].forEach(function (i) { i.value = ''; });
          checkPw(false);
          // The session and the CSRF token were renewed; later requests from this page use the new token.
          if (r.data && /^[A-Za-z0-9_-]{43}$/.test(r.data.csrf_token || '')) document.querySelector('meta[name="csrf-token"]').content = r.data.csrf_token;
          toast('비밀번호를 바꿨어요');
          return;
        }
        if (r.status === 401 && r.code === 'UNAUTHENTICATED') { location.assign('/login?next=/mypage'); return; }
        checkPw(true);
        // 현재 비밀번호 오류는 그 칸 아래, 새 비밀번호 오류는 마지막 칸 아래에 보여요 (원본에는 새 비밀번호 안내 줄이 없어요)
        if (r.field === 'new_password' || r.field === 'new_password_confirm') {
          $('pw2Hint').classList.remove('ok');
          setHint(r.field === 'new_password' ? 'pw1Box' : 'pw2Box', 'pw2Hint', r.message, true, '');
        } else {
          setHint('pw0Box', 'pw0Hint', r.message, true, '');
        }
      });
  });

  /* ---------- 계정 관리 ---------- */
  // 로그아웃: 한 번 더 묻는 팝업
  var modal = $('logoutModal');
  function setModal(open) {
    modal.classList.toggle('show', open);
    modal.setAttribute('aria-hidden', !open);
    if (open) $('logoutNo').focus(); else $('logout').focus();
  }
  $('logout').addEventListener('click', function () { setModal(true); });
  $('logoutNo').addEventListener('click', function () { setModal(false); });
  modal.addEventListener('click', function (e) { if (e.target === modal) setModal(false); });   // 바깥을 누르면 닫혀요
  document.addEventListener('keydown', function (e) {
    if (!modal.classList.contains('show')) return;
    if (e.key === 'Escape') setModal(false);
    if (e.key === 'Tab') {                         // 팝업 안에서만 이동
      e.preventDefault();
      ($('logoutNo') === document.activeElement ? $('logoutYes') : $('logoutNo')).focus();
    }
  });
  $('logoutYes').addEventListener('click', function () {
    $('logoutYes').disabled = true;
    post('/api/auth/logout', {}).then(function (r) {
      $('logoutYes').disabled = false;
      if (r.ok) { location.assign('/'); return; }   // 로그아웃 뒤 랜딩 페이지로
      setModal(false);
      toast(r.message);
    });
  });

  var leave = $('leave'), leaveIn = $('leaveInput'), impactEl = $('leaveImpact');
  var busyLeave = false;
  function impactText(d) {
    var parts = [];
    if (d.groups) parts.push('참여 중인 그룹 ' + d.groups + '곳에서 나가요.');
    d.handoffs.forEach(function (h) { parts.push('‘' + h.group_name + '’의 그룹장은 ' + h.successor_name + '님에게 넘어가요.'); });
    if (d.deleted_groups.length) parts.push('남는 팀원이 없는 ' + d.deleted_groups.map(function (n) { return '‘' + n + '’'; }).join(', ') + ' 그룹은 함께 삭제돼요.');
    if (d.records) parts.push('내 완료 기록 ' + d.records + '개' + (d.files ? '와 첨부 파일 ' + d.files + '개' : '') + '가 삭제돼요.');
    return parts.join(' ');
  }
  function loadImpact() {
    impactEl.hidden = true;
    getJSON('/api/account/delete').then(function (r) {
      if (!r.ok || !r.data || !r.data.impact) return;
      var t = impactText(r.data.impact);
      impactEl.textContent = t;
      impactEl.hidden = !t;
    });
  }
  function setLeave(open) {
    leave.hidden = !open;
    $('leaveOpen').setAttribute('aria-expanded', open);
    leaveIn.value = '';
    leaveIn.type = 'password';
    paintEye(leave.querySelector('.eye'), false);
    setHint('leaveBox', 'leaveHint', '', false, '');
    $('leaveBtn').disabled = true;
    if (open) { loadImpact(); leaveIn.focus(); }
  }
  $('leaveOpen').addEventListener('click', function () { setLeave(leave.hidden); });
  $('leaveCancel').addEventListener('click', function () { setLeave(false); });
  leaveIn.addEventListener('input', function () { $('leaveBtn').disabled = busyLeave || !leaveIn.value; setHint('leaveBox', 'leaveHint', '', false, ''); });
  $('leaveBtn').addEventListener('click', function () {
    busyLeave = true;
    $('leaveBtn').disabled = true;
    // The panel already asked for the password and showed what happens; this button is the confirmation.
    post('/api/account/delete', { password: leaveIn.value, confirm: true }).then(function (r) {
      busyLeave = false;
      if (r.ok) { location.assign('/'); return; }   // 탈퇴가 끝난 뒤에만 이동 (세션도 이미 끝남)
      $('leaveBtn').disabled = !leaveIn.value;
      setHint('leaveBox', 'leaveHint', r.message, true, '');
    });
  });

  /* ---------- 프로필 캐릭터 눈이 커서를 따라가요 ---------- */
  (function () {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var raf = 0, px = 0, py = 0;
    function look() {
      raf = 0;
      Array.prototype.forEach.call(document.querySelectorAll('.face'), function (f) {
        var r = f.getBoundingClientRect();
        if (!r.width) return;
        var dx = px - (r.left + r.width / 2), dy = py - (r.top + r.height / 2);
        var dist = Math.hypot(dx, dy) || 1;
        var max = r.width * 0.13;                       // 얼굴 크기에 맞춰 눈이 움직이는 최대 거리
        var k = Math.min(max, dist / 12) / dist;        // 가까우면 조금, 멀면 끝까지
        var t = 'translate(' + (dx * k).toFixed(1) + 'px,' + (dy * k).toFixed(1) + 'px)';
        Array.prototype.forEach.call(f.querySelectorAll('i'), function (eye) { eye.style.transform = t; });
      });
    }
    document.addEventListener('pointermove', function (e) {
      px = e.clientX; py = e.clientY;
      if (!raf) raf = requestAnimationFrame(look);
    });
    // 커서가 창 밖으로 나가면 다시 정면을 봐요
    document.documentElement.addEventListener('mouseleave', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.face i'), function (eye) { eye.style.transform = ''; });
    });
  })();

  checkInfo();
})();
