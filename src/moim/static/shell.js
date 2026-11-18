/* Shared by dashboard, group and the chat placeholder: logout, sidebar, "새 그룹 만들기", group menu.
   Stores nothing in the browser. Server text is only ever written with textContent. */
(function () {
  function $(id) { return document.getElementById(id); }

  // ---- 로그아웃 ----
  var logout = $('logout');
  if (logout) {
    logout.addEventListener('click', function () {
      if (logout.disabled) return;
      logout.disabled = true;
      MoimAuth.post('/api/auth/logout', {}).then(function (r) {
        if (r.ok) { MoimAuth.go(r.data.redirect); return; }
        logout.disabled = false;
      });
    });
  }

  // ---- 안내 문구 (기존 빈 상태 패널 스타일) ----
  function say(text) {
    var el = $('dashMsg') || $('shellMsg');
    if (!el) {
      var main = document.querySelector('.main');
      if (!main) return;
      el = document.createElement('p');
      el.id = 'shellMsg';
      el.className = 'day-empty notice';
      el.setAttribute('role', 'alert');
      main.insertBefore(el, main.firstChild);
    }
    el.hidden = false;
    el.textContent = text;
  }

  // ---- 새 그룹 만들기: 생성 의도가 확인되는 POST를 거쳐 생성자용 초대방으로 ----
  var newGroup = $('newGroup');
  if (newGroup) {
    newGroup.addEventListener('click', function () {
      if (newGroup.disabled) return;
      newGroup.disabled = true;
      MoimAuth.post('/api/groups', {}).then(function (r) {
        if (r.ok) { MoimAuth.go(r.data.redirect); return; }
        say(r.message);
        newGroup.disabled = false;
      });
    });
  }

  // ---- 사이드바 접기 / 서랍 ----
  var app = $('app'), toggleBtn = $('toggleBtn'), menuBtn = $('menuBtn'), scrim = $('scrim');
  if (app && toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      var collapsed = app.classList.toggle('collapsed');
      toggleBtn.setAttribute('aria-expanded', String(!collapsed));
      toggleBtn.setAttribute('aria-label', collapsed ? '사이드바 펼치기' : '사이드바 접기');
    });
  }
  function setDrawer(open) {
    if (!app || !menuBtn) return;
    app.classList.toggle('drawer-open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', open ? '그룹 목록 닫기' : '그룹 목록 열기');
  }
  if (menuBtn) menuBtn.addEventListener('click', function () { setDrawer(!app.classList.contains('drawer-open')); });
  if (scrim) scrim.addEventListener('click', function () { setDrawer(false); });

  // ---- 그룹 메뉴 (⋯) ----
  var more = $('more'), moreBtn = $('moreBtn');
  function setMore(open) {
    if (!more) return;
    more.classList.toggle('open', open);
    moreBtn.setAttribute('aria-expanded', String(open));
  }
  if (more && moreBtn) {
    moreBtn.addEventListener('click', function (e) { e.stopPropagation(); setMore(!more.classList.contains('open')); });
    document.addEventListener('click', function (e) { if (!more.contains(e.target)) setMore(false); });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { setDrawer(false); setMore(false); }
  });
})();
