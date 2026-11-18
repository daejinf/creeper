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
  function addMs() {
    var li = document.createElement('li');
    var nm = document.createElement('input');
    nm.className = 'inp name'; nm.type = 'text'; nm.maxLength = 20; nm.autocomplete = 'off';
    nm.placeholder = '예) 중간 발표'; nm.setAttribute('aria-label', '일정 이름');
    var dt = document.createElement('input');
    dt.className = 'inp empty'; dt.type = 'date'; dt.setAttribute('aria-label', '일정 날짜');
    var rm = document.createElement('button');
    rm.type = 'button'; rm.className = 'rm'; rm.setAttribute('aria-label', '일정 삭제');
    rm.appendChild(xIcon('', 14, 2.4));
    li.appendChild(nm); li.appendChild(dt); li.appendChild(rm);
    ms.appendChild(li);
    syncMsBounds();
    li.querySelector('.name').focus();
  }
  function syncMsBounds() {
    ms.querySelectorAll('input[type=date]').forEach(function (d) { d.min = start || today; d.max = end || ''; });
  }
  $('msAdd').addEventListener('click', function () { addMs(); update(); });
  ms.addEventListener('click', function (e) {
    var rm = e.target.closest('.rm'); if (!rm) return;
    rm.closest('li').remove(); update();
  });
  ms.addEventListener('input', function (e) {
    if (e.target.type === 'date') e.target.classList.toggle('empty', !e.target.value);
    update();
  });

  // ---------- 상태 ----------
  function update() {
    syncMsBounds();
    var msBad = '';
    ms.querySelectorAll('input[type=date]').forEach(function (d) {
      var v = d.value, bad = false;
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
      var n = li.querySelector('.name').value.trim(), d = li.querySelector('input[type=date]').value;
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
        var d = li.querySelector('input[type=date]');
        d.value = m.date; d.classList.remove('empty');
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
