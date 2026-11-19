/* ProjectBrief form (create = activate the group, edit = new revision). Dates, chips and counters are the original's. */
(function () {
  var SVGNS = 'http://www.w3.org/2000/svg';
  function icon(cls, size, sw, d) {
    var s = document.createElementNS(SVGNS, 'svg');
    if (cls) s.setAttribute('class', cls);
    s.setAttribute('width', size); s.setAttribute('height', size); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', sw);
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', d); s.appendChild(p);
    return s;
  }
  function checkIcon() { return icon('', 13, 3, 'M5 12.5l4.5 4.5L19 7.5'); }
  function xIcon(cls, size, sw) { return icon(cls, size, sw, 'M6 6l12 12M18 6L6 18'); }
  function customChip(label) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'chip custom'; b.setAttribute('aria-pressed', 'true');
    b.setAttribute('aria-label', label + ' 삭제');
    b.appendChild(checkIcon());
    var t = document.createElement('span'); t.textContent = label; b.appendChild(t);
    b.appendChild(xIcon('x', 12, 2.6));
    return b;
  }
  var $ = function (id) { return document.getElementById(id); };

  // ---------- 진행 막대 ----------
  var walk = document.getElementById('walk'), who = document.getElementById('who');
  var lastP = -1;
  function progress(p) {
    if (p === lastP) return;
    walk.style.setProperty('--p', 'calc((100% - 14px) * ' + p + ')');
    walk.style.setProperty('--pp', p);
    who.classList.remove('go'); void who.offsetWidth; who.classList.add('go');
    walk.classList.toggle('done', p === 1);
    lastP = p;
  }

  var name = $('name'), desc = $('desc'), chips = $('chips'), etcChip = $('etcChip');
  var etcBox = $('etcBox'), etcIn = $('etcIn'), etcAdd = $('etcAdd'), ms = $('ms'), next = $('next');

  function ymd(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  var now = new Date();
  var today = ymd(now);
  function diff(a, b) { return Math.round((new Date(b + 'T00:00') - new Date(a + 'T00:00')) / 864e5); }
  function dot(v) { return v.replace(/-/g, '.'); }

  // 글자 수
  function counter(inp, out, max) {
    inp.addEventListener('input', function () { out.textContent = inp.value.length + '/' + max; update(); });
  }
  counter(name, $('nameCnt'), 30);
  counter(desc, $('descCnt'), 1000);

  // ---------- 프로젝트 기간 (date range) ----------
  var start = '', end = '', hover = '';
  var view = new Date(now.getFullYear(), now.getMonth(), 1);
  var range = $('range'), cal = $('cal'), days = $('days'), field = $('rangeField');

  function renderCal() {
    var y = view.getFullYear(), m = view.getMonth();
    $('calTitle').textContent = y + '년 ' + (m + 1) + '월';
    $('prev').disabled = y === now.getFullYear() && m === now.getMonth();
    var first = new Date(y, m, 1).getDay(), last = new Date(y, m + 1, 0).getDate();
    days.textContent = '';
    for (var i = 0; i < first; i++) {
      var o = document.createElement('button');
      o.type = 'button'; o.className = 'out'; o.tabIndex = -1; o.setAttribute('aria-hidden', 'true');
      days.appendChild(o);
    }
    for (var d = 1; d <= last; d++) {
      var v = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var db = document.createElement('button');
      db.type = 'button'; db.setAttribute('data-v', v); if (v < today) db.disabled = true;
      db.setAttribute('aria-label', (m + 1) + '월 ' + d + '일');
      var ds = document.createElement('span'); ds.textContent = String(d); db.appendChild(ds);
      days.appendChild(db);
    }
    paint();
  }
  function paint() {
    var tail = end || (start && hover && hover > start ? hover : '');
    days.querySelectorAll('button[data-v]').forEach(function (b) {
      var v = b.dataset.v;
      b.classList.toggle('today', v === today);
      b.classList.toggle('s', v === start);
      b.classList.toggle('e', (v === start && !tail) || (!!tail && v === tail));
      b.classList.toggle('in', !!(start && tail && v > start && v < tail));
      if (v === start || v === end) b.setAttribute('aria-pressed', 'true'); else b.removeAttribute('aria-pressed');
    });
    $('calHint').textContent = !start ? '시작일을 골라주세요' : !end ? '마감일을 골라주세요' : (diff(start, end) + 1) + '일 동안';
  }
  function renderField() {
    var v = $('rangeVal');
    v.textContent = '';
    function part(txt, ph) { var s = document.createElement('span'); if (ph) s.className = 'ph'; s.textContent = txt; v.appendChild(s); }
    part(start ? dot(start) : '시작일', !start);
    var t = document.createElement('span'); t.className = 'tilde'; t.textContent = '~'; v.appendChild(t);
    part(end ? dot(end) : '마감일', !end);
  }
  function openCal(open) {
    cal.hidden = !open;
    field.classList.toggle('has-cal', open);
    range.setAttribute('aria-expanded', open);
    if (open) {
      var base = start ? new Date(start + 'T00:00') : now;
      view = new Date(base.getFullYear(), base.getMonth(), 1);
      hover = '';
      renderCal();
    }
  }
  range.addEventListener('click', function () { openCal(cal.hidden); });
  $('prev').addEventListener('click', function () { view.setMonth(view.getMonth() - 1); renderCal(); });
  $('nextM').addEventListener('click', function () { view.setMonth(view.getMonth() + 1); renderCal(); });
  $('calReset').addEventListener('click', function () { start = end = ''; paint(); renderField(); update(); });
  days.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-v]'); if (!b || b.disabled) return;
    var v = b.dataset.v;
    if (!start || end || v < start) { start = v; end = ''; }
    else { end = v; }
    hover = '';
    paint(); renderField(); update();
    if (end) setTimeout(function () { openCal(false); range.focus(); }, 180);
  });
  days.addEventListener('mouseover', function (e) {
    var b = e.target.closest('button[data-v]');
    if (!b || !start || end) return;
    if (hover !== b.dataset.v) { hover = b.dataset.v; paint(); }
  });
  days.addEventListener('mouseleave', function () { if (hover) { hover = ''; paint(); } });
  document.addEventListener('click', function (e) { if (!cal.hidden && !field.contains(e.target)) openCal(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !cal.hidden) { openCal(false); range.focus(); } });

  // ---------- 결과물 형태 ----------
  function types() {
    return [].slice.call(chips.querySelectorAll('.chip[aria-pressed="true"]:not(#etcChip)')).map(function (c) { return c.textContent.trim(); });
  }
  chips.addEventListener('click', function (e) {
    var c = e.target.closest('.chip'); if (!c) return;
    if (c.classList.contains('custom')) { c.remove(); update(); return; }
    var on = c.getAttribute('aria-pressed') !== 'true';
    c.setAttribute('aria-pressed', on);
    if (c === etcChip) {
      etcChip.setAttribute('aria-expanded', on);
      etcBox.hidden = !on;
      if (on) etcIn.focus();
    }
    update();
  });
  etcIn.addEventListener('input', function () { etcAdd.disabled = !etcIn.value.trim(); });
  function addEtc() {
    var v = etcIn.value.trim(); if (!v) return;
    var same = [].slice.call(chips.querySelectorAll('.chip:not(#etcChip)')).filter(function (c) { return c.textContent.trim().toLowerCase() === v.toLowerCase(); })[0];
    if (same) same.setAttribute('aria-pressed', 'true');
    else {
      chips.insertBefore(customChip(v), etcChip);
    }
    etcIn.value = ''; etcAdd.disabled = true; etcIn.focus();
    update();
  }
  etcAdd.addEventListener('click', addEtc);
  etcIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addEtc(); } });

  // ---------- 중간 일정 ----------
  // 날짜 칸은 프로젝트 기간과 같은 달력(.range + .cal)을 써요. 고를 수 있는 날은 프로젝트 기간 안으로 제한해요.
  function calIcon() {
    var s = icon('', 18, 1.8, 'M3.5 10h17M8 3v4M16 3v4');
    var r = document.createElementNS(SVGNS, 'rect');
    r.setAttribute('x', '3.5'); r.setAttribute('y', '5'); r.setAttribute('width', '17'); r.setAttribute('height', '15.5'); r.setAttribute('rx', '3');
    s.insertBefore(r, s.firstChild);
    return s;
  }
  function msVal(btn) { return btn.getAttribute('data-v') || ''; }
  function setMsVal(btn, v) {
    if (v) btn.setAttribute('data-v', v); else btn.removeAttribute('data-v');
    var t = btn.querySelector('.v'); t.textContent = '';
    var s = document.createElement('span');
    if (!v) s.className = 'ph';
    s.textContent = v ? dot(v) : '연도-월-일';
    t.appendChild(s);
  }
  function addMs() {
    var li = document.createElement('li');
    var nm = document.createElement('input');
    nm.className = 'inp name'; nm.type = 'text'; nm.maxLength = 20; nm.autocomplete = 'off';
    nm.placeholder = '예) 중간 발표'; nm.setAttribute('aria-label', '일정 이름');
    var dt = document.createElement('button');
    dt.type = 'button'; dt.className = 'range msd'; dt.setAttribute('aria-haspopup', 'dialog'); dt.setAttribute('aria-expanded', 'false');
    dt.setAttribute('aria-label', '일정 날짜');
    var dv = document.createElement('span'); dv.className = 'v'; dt.appendChild(dv);
    dt.appendChild(calIcon());
    setMsVal(dt, '');
    var rm = document.createElement('button');
    rm.type = 'button'; rm.className = 'rm'; rm.setAttribute('aria-label', '일정 삭제');
    rm.appendChild(xIcon('', 14, 2.4));
    li.appendChild(nm); li.appendChild(dt); li.appendChild(rm);
    ms.appendChild(li);
    li.querySelector('.name').focus();
  }

  // 하나의 달력 팝업을 열린 일정 줄에 붙여 써요.
  var msCal = document.createElement('div');
  msCal.className = 'cal'; msCal.setAttribute('role', 'dialog'); msCal.setAttribute('aria-label', '일정 날짜 선택'); msCal.hidden = true;
  var msHead = document.createElement('div'); msHead.className = 'cal-h';
  function navBtn(label, d) { var b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', label); b.appendChild(icon('', 16, 2.2, d)); return b; }
  var msPrev = navBtn('이전 달', 'M15 6l-6 6 6 6'), msNext = navBtn('다음 달', 'M9 6l6 6-6 6');
  var msTitle = document.createElement('b'); msTitle.setAttribute('aria-live', 'polite');
  msHead.appendChild(msPrev); msHead.appendChild(msTitle); msHead.appendChild(msNext);
  var msWk = document.createElement('div'); msWk.className = 'wk'; msWk.setAttribute('aria-hidden', 'true');
  ['일', '월', '화', '수', '목', '금', '토'].forEach(function (w) { var s = document.createElement('span'); s.textContent = w; msWk.appendChild(s); });
  var msDays = document.createElement('div'); msDays.className = 'days';
  var msFoot = document.createElement('div'); msFoot.className = 'cal-f';
  var msHint = document.createElement('span');
  var msClear = document.createElement('button'); msClear.type = 'button'; msClear.textContent = '초기화';
  msFoot.appendChild(msHint); msFoot.appendChild(msClear);
  msCal.appendChild(msHead); msCal.appendChild(msWk); msCal.appendChild(msDays); msCal.appendChild(msFoot);
  var msBtn = null, msView = new Date(now.getFullYear(), now.getMonth(), 1);

  function msMin() { return start || today; }
  function renderMsCal() {
    var y = msView.getFullYear(), m = msView.getMonth();
    var lo = msMin(), hi = end || '';
    msTitle.textContent = y + '년 ' + (m + 1) + '월';
    var firstV = y + '-' + String(m + 1).padStart(2, '0') + '-01';
    var lastD = new Date(y, m + 1, 0).getDate();
    var lastV = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(lastD).padStart(2, '0');
    msPrev.disabled = firstV <= lo;
    msNext.disabled = !!hi && lastV >= hi;
    msDays.textContent = '';
    for (var i = 0, first = new Date(y, m, 1).getDay(); i < first; i++) {
      var o = document.createElement('button');
      o.type = 'button'; o.className = 'out'; o.tabIndex = -1; o.setAttribute('aria-hidden', 'true');
      msDays.appendChild(o);
    }
    var cur = msBtn ? msVal(msBtn) : '';
    for (var d = 1; d <= lastD; d++) {
      var v = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var db = document.createElement('button');
      db.type = 'button'; db.setAttribute('data-v', v);
      if (v < lo || (hi && v > hi)) db.disabled = true;
      db.setAttribute('aria-label', (m + 1) + '월 ' + d + '일');
      if (v === today) db.classList.add('today');
      if (v === cur) { db.classList.add('s', 'e'); db.setAttribute('aria-pressed', 'true'); }
      var ds = document.createElement('span'); ds.textContent = String(d); db.appendChild(ds);
      msDays.appendChild(db);
    }
    msHint.textContent = start && end ? '프로젝트 기간 안에서 골라주세요' : '오늘 이후 날짜를 골라주세요';
  }
  function closeMsCal(focus) {
    if (!msBtn) return;
    var b = msBtn; msBtn = null;
    msCal.hidden = true;
    b.setAttribute('aria-expanded', 'false');
    b.closest('li').classList.remove('has-cal');
    if (focus) b.focus();
  }
  function openMsCal(btn) {
    closeMsCal(false);
    openCal(false);
    msBtn = btn;
    var li = btn.closest('li');
    li.appendChild(msCal); li.classList.add('has-cal');
    btn.setAttribute('aria-expanded', 'true');
    var v = msVal(btn), lo = msMin();
    var base = new Date((v || (today > lo ? today : lo)) + 'T00:00');
    msView = new Date(base.getFullYear(), base.getMonth(), 1);
    msCal.hidden = false;
    renderMsCal();
  }
  // 날짜를 누르면 칸을 다시 그리므로, 바깥 클릭 판정에 닿지 않게 달력 안 클릭은 여기서 멈춰요.
  msCal.addEventListener('click', function (e) { e.stopPropagation(); });
  msPrev.addEventListener('click', function () { msView.setMonth(msView.getMonth() - 1); renderMsCal(); });
  msNext.addEventListener('click', function () { msView.setMonth(msView.getMonth() + 1); renderMsCal(); });
  msClear.addEventListener('click', function () { if (msBtn) { setMsVal(msBtn, ''); renderMsCal(); update(); } });
  msDays.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-v]'); if (!b || b.disabled || !msBtn) return;
    setMsVal(msBtn, b.dataset.v); renderMsCal(); update();
    setTimeout(function () { closeMsCal(true); }, 180);
  });
  range.addEventListener('click', function () { closeMsCal(false); });
  document.addEventListener('click', function (e) {
    if (msBtn && !msBtn.closest('li').contains(e.target)) closeMsCal(false);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && msBtn) closeMsCal(true); });

  $('msAdd').addEventListener('click', function () { addMs(); update(); });
  ms.addEventListener('click', function (e) {
    if (msCal.contains(e.target)) return;
    var rm = e.target.closest('.rm');
    if (rm) { if (msBtn && rm.closest('li') === msBtn.closest('li')) closeMsCal(false); rm.closest('li').remove(); update(); return; }
    var dt = e.target.closest('.msd');
    if (dt) { if (msBtn === dt) closeMsCal(false); else openMsCal(dt); }
  });
  ms.addEventListener('input', function () { update(); });

  // ---------- 상태 ----------
  function update() {
    var msBad = '';
    ms.querySelectorAll('.msd').forEach(function (d) {
      var v = msVal(d), bad = false;
      if (v && v < (start || today)) { bad = true; msBad = start ? '중간 일정은 프로젝트 기간 안에 있어야 해요.' : '오늘 이후 날짜를 골라주세요.'; }
      if (v && end && v > end) { bad = true; msBad = '중간 일정은 프로젝트 기간 안에 있어야 해요.'; }
      d.classList.toggle('bad', bad);
    });
    $('msErr').textContent = msBad;
    next.disabled = !name.value.trim() || !start || !end || !types().length || !!msBad;
    var got = (name.value.trim() ? 1 : 0) + (start && end ? 1 : 0) + (types().length ? 1 : 0);
    progress(got / 3);
  }

  // ---------- 서버 연결 ----------
  var form = $('f'), formErr = $('formErr');
  var mode = form.getAttribute('data-mode'), gid = form.getAttribute('data-group'), rev = form.getAttribute('data-revision');
  var sending = false;

  function milestones() {
    var out = [];
    [].slice.call(ms.querySelectorAll('li')).forEach(function (li) {
      var n = li.querySelector('.name').value.trim(), d = msVal(li.querySelector('.msd'));
      if (n || d) out.push({ name: n, date: d });
    });
    return out;
  }

  function seedChip(label) {
    var same = [].slice.call(chips.querySelectorAll('.chip:not(#etcChip)')).filter(function (c) { return c.textContent.trim().toLowerCase() === label.toLowerCase(); })[0];
    if (same) { same.setAttribute('aria-pressed', 'true'); return; }
    chips.insertBefore(customChip(label), etcChip);
  }

  // 수정 화면: 저장된 현재 ProjectBrief를 채워 넣어요.
  var seedEl = document.getElementById('briefData');
  if (seedEl) {
    try {
      var seed = JSON.parse(seedEl.textContent);
      name.value = seed.name; desc.value = seed.content;
      $('nameCnt').textContent = name.value.length + '/30';
      $('descCnt').textContent = desc.value.length + '/1000';
      start = seed.start_date; end = seed.end_date;
      (seed.types || []).forEach(seedChip);
      (seed.milestones || []).forEach(function (m) {
        addMs();
        var li = ms.lastElementChild;
        li.querySelector('.name').value = m.name;
        setMsVal(li.querySelector('.msd'), m.date);
      });
      name.blur();
    } catch (e) { formErr.textContent = '저장된 내용을 불러오지 못했어요. 새로고침해 주세요.'; }
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (next.disabled || sending) return;
    sending = true; next.disabled = true; formErr.textContent = '';
    var body = { name: name.value.trim(), content: desc.value, start_date: start, end_date: end, types: types(), milestones: milestones() };
    var url = '/api/groups/' + encodeURIComponent(gid) + '/activate';
    if (mode === 'edit') { url = '/api/groups/' + encodeURIComponent(gid) + '/brief'; body.base_revision = Number(rev); }
    MoimAuth.post(url, body).then(function (r) {
      if (r.ok) { MoimAuth.go(r.data.redirect); return; }
      if (r.code === 'ALREADY_ACTIVE') { MoimAuth.go('/groups/' + gid + '/chat'); return; }
      sending = false;
      formErr.textContent = r.message;
      update();
    });
  });

  renderField();
  update();
})();
