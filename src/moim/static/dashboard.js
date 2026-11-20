/* Dashboard: drag-to-scroll for the group cards (from the original), and the original's card progress lines, team news
   and right panel (오늘의 할 일 · 대기 중 · 그룹 마감), filled from GET /api/dashboard.

   The data is what each group screen already shows the viewer (D-E1): progress, the viewer's own todos, completion
   records. Completion is never done here (D-H1): a todo item links to its group, where material is attached and the AI
   or the team confirms it. Built with DOM nodes and textContent only. */
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

(function () {
  var panel = document.getElementById('panel');
  if (!panel) return;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var DOW = ['일', '월', '화', '수', '목', '금', '토'];
  var DOT = { blue: 'cap', coral: 'mkt', sun: 'std' };
  var data = null, picked = null, colour = {};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function icon(paths, size, width) {
    var s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('width', size); s.setAttribute('height', size); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', width);
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) { var p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  function hourglass() { return icon(['M6 3h12M6 21h12M7 3c0 4.5 5 6.5 5 9s-5 4.5-5 9M17 3c0 4.5-5 6.5-5 9s5 4.5 5 9'], 13, 2); }
  function parseDay(iso) { var p = iso.split('-'); return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])); }
  function ymd(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function md(iso) { var p = iso.split('-'); return p[1] + '/' + p[2]; }
  function addDays(iso, n) { var d = parseDay(iso); d.setDate(d.getDate() + n); return ymd(d); }
  function days(from, to) { return Math.round((parseDay(to) - parseDay(from)) / 86400000); }
  // 받침이 있으면 '을', 없으면 '를'
  function obj(word) {
    var c = word.charCodeAt(word.length - 1);
    return c >= 0xAC00 && c <= 0xD7A3 && (c - 0xAC00) % 28 ? '을' : '를';
  }
  function gdot(gid) { var s = el('span', 'gdot ' + (DOT[colour[gid]] || 'cap')); s.setAttribute('aria-hidden', 'true'); return s; }

  document.querySelectorAll('.card[data-group]').forEach(function (c) {
    var t = c.querySelector('.thumb');
    colour[c.getAttribute('data-group')] = ['blue', 'coral', 'sun'].filter(function (k) { return t.classList.contains(k); })[0];
  });

  function todos() {
    var all = [];
    data.groups.forEach(function (gr) {
      gr.todos.forEach(function (t) { all.push({ g: gr, title: t.title, due: t.due_date, state: t.state }); });
    });
    return all;
  }
  function open(t) { return t.state === 'OPEN' || t.state === 'REJECTED'; }

  // ---- 그룹 카드: 진척도 · 남은 할 일 · 오늘 마감 ----
  function renderCards() {
    var todayCount = 0;
    data.groups.forEach(function (gr) {
      var card = document.querySelector('.card[data-group="' + gr.id + '"]');
      var hot = gr.todos.filter(function (t) { return t.due_date === data.today; }).length;
      todayCount += hot;
      if (!card) return;
      var pct = gr.total ? Math.round(gr.done / gr.total * 100) : 0;
      var prog = card.querySelector('.prog');
      prog.querySelector('.bar span').style.width = pct + '%';
      prog.querySelector('b').textContent = pct + '%';
      prog.hidden = false;
      var line = card.querySelector('.todo-line');
      line.querySelector('[data-left]').textContent = gr.todos.length + '개';
      var h = line.querySelector('[data-hot]');
      h.textContent = '오늘 마감 ' + hot;
      h.hidden = line.querySelector('.sep').hidden = !hot;
      line.hidden = false;
    });
    document.getElementById('todayPill').textContent = todayCount;
    document.getElementById('todayPillBox').hidden = false;
  }

  // ---- 최근 팀 소식 ----
  function renderNews() {
    var list = document.getElementById('newsList');
    clear(list);
    if (!data.news.length) {
      var li = el('li', 'news-item empty'); li.appendChild(el('span', 'txt', '아직 팀 소식이 없어요. 팀원이 자료를 올리면 여기에 보여요.'));
      list.appendChild(li);
      return;
    }
    data.news.forEach(function (n) {
      var li = el('li', 'news-item');
      var av = el('span', 'av ' + n.shape); av.setAttribute('aria-hidden', 'true');
      av.appendChild(el('i')); av.appendChild(el('i'));
      li.appendChild(av);
      var txt = el('span', 'txt');
      txt.appendChild(el('b', null, n.who));
      txt.appendChild(document.createTextNode(n.who === '나간 팀원' ? '이 ' : '님이 '));
      txt.appendChild(el('em', null, n.title));
      txt.appendChild(document.createTextNode(
        n.kind === 'VOTING' ? ' 확인을 요청했어요' : obj(n.title) + (n.kind === 'DONE' ? ' 끝냈어요' : ' 올렸어요')));
      li.appendChild(txt);
      li.appendChild(el('span', 'grp', n.group));
      li.appendChild(el('time', null, n.label.replace(/^오늘 /, '')));  // 원본처럼 오늘은 시각만
      list.appendChild(li);
    });
  }

  // ---- 오른쪽 패널 ----
  function item(t, waiting) {
    var li = el('li');
    var a = el('a', 'ditem' + (waiting ? ' waiting' : ''));
    a.href = t.g.href;
    a.title = waiting ? t.g.name + '에서 확인 상태 보기' : t.g.name + '에서 자료를 올려 완료하기';
    if (waiting) { var ic = el('span', 'wait-ic'); ic.setAttribute('aria-hidden', 'true'); ic.appendChild(hourglass()); a.appendChild(ic); }
    else { var cb = el('span', 'cb'); cb.setAttribute('aria-hidden', 'true'); a.appendChild(cb); }
    var box = el('div', 't');
    box.appendChild(el('b', null, t.title));
    var sm = el('small');
    sm.appendChild(gdot(t.g.id));
    sm.appendChild(document.createTextNode(t.g.name));
    if (waiting) sm.appendChild(document.createTextNode(t.state === 'REVIEWING' ? ' · AI 확인 중' : ' · 팀원 확인 중'));
    else if (t.state === 'REJECTED') sm.appendChild(el('span', 'late', '· 반려됨, 다시 올려 주세요'));
    else if (t.due < data.today) sm.appendChild(el('span', 'late', '· ' + md(t.due) + ' 마감 지남'));
    box.appendChild(sm);
    a.appendChild(box);
    li.appendChild(a);
    return li;
  }

  function renderPanel() {
    var all = todos(), today = data.today;
    var d0 = parseDay(today);
    document.getElementById('todayDate').textContent = (d0.getMonth() + 1) + '월 ' + d0.getDate() + '일 ' + DOW[d0.getDay()] + '요일';
    var week = document.getElementById('week');
    clear(week);
    for (var k = 0; k < 7; k++) {
      var day = addDays(today, k), dd = parseDay(day);
      var due = all.filter(function (t) { return t.due === day && open(t); });
      var b = el('button', 'day' + (k === 0 ? ' today' : ''));
      b.type = 'button';
      b.setAttribute('data-day', day);
      b.setAttribute('aria-pressed', String(picked === day));
      b.setAttribute('aria-label', (dd.getMonth() + 1) + '월 ' + dd.getDate() + '일 할 일 ' + due.length + '개');
      b.appendChild(el('small', null, DOW[dd.getDay()]));
      b.appendChild(el('b', null, String(dd.getDate())));
      var dots = el('span', 'dots');
      due.slice(0, 3).forEach(function (t) { var i = el('i', 'gdot ' + (DOT[colour[t.g.id]] || 'cap')); dots.appendChild(i); });
      b.appendChild(dots);
      week.appendChild(b);
    }
    // 오늘은 지난 마감도 함께 보인다
    var list = all.filter(function (t) { return open(t) && (t.due === picked || (picked === today && t.due < today)); })
      .sort(function (a, b) { return a.due < b.due ? -1 : a.due > b.due ? 1 : 0; });
    document.getElementById('dayTitle').textContent = picked === today ? '오늘' : picked === addDays(today, 1) ? '내일' : (parseDay(picked).getMonth() + 1) + '월 ' + parseDay(picked).getDate() + '일';
    document.getElementById('dayCount').textContent = list.length;
    var dl = document.getElementById('dayList');
    clear(dl);
    if (list.length) list.forEach(function (t) { dl.appendChild(item(t, false)); });
    else dl.appendChild(el('li', 'day-empty', '이날은 할 일이 없어요'));

    var waits = all.filter(function (t) { return t.state === 'REVIEWING' || t.state === 'VOTING'; });
    var wl = document.getElementById('waitList');
    clear(wl);
    waits.forEach(function (t) { wl.appendChild(item(t, true)); });
    document.getElementById('waitSec').hidden = !waits.length;

    var reqs = [];
    data.groups.forEach(function (gr) { gr.requests.forEach(function (r) { reqs.push({ g: gr, r: r }); }); });
    var rl = document.getElementById('reqList');
    clear(rl);
    reqs.forEach(function (x) {
      var li = el('li'), a = el('a', 'ditem waiting');
      a.href = x.g.href; a.title = x.g.name + '에서 확인하기';
      var ic = el('span', 'wait-ic'); ic.setAttribute('aria-hidden', 'true'); ic.appendChild(hourglass()); a.appendChild(ic);
      var box = el('div', 't');
      box.appendChild(el('b', null, x.r.title));
      var sm = el('small');
      sm.appendChild(gdot(x.g.id));
      sm.appendChild(document.createTextNode(x.r.who + '님 · ' + x.g.name + ' · 동의 ' + x.r.agree + '/' + x.r.need));
      box.appendChild(sm);
      a.appendChild(box); li.appendChild(a); rl.appendChild(li);
    });
    document.getElementById('reqCount').textContent = reqs.length;
    document.getElementById('reqSec').hidden = !reqs.length;

    var dls = document.getElementById('deadlines');
    clear(dls);
    data.groups.slice().sort(function (a, b) { return a.end_date < b.end_date ? -1 : 1; }).forEach(function (gr) {
      var n = days(today, gr.end_date);
      var li = el('li', 'dl');
      li.appendChild(el('span', 'n', gr.name));
      li.appendChild(el('time', null, md(gr.end_date)));
      li.appendChild(el('span', 'dd' + (n >= 0 && n <= 7 ? ' near' : ''), n > 0 ? 'D-' + n : n === 0 ? 'D-DAY' : '종료'));
      dls.appendChild(li);
    });
    if (!data.groups.length) dls.appendChild(el('li', 'day-empty', '진행 중인 그룹이 없어요'));
  }

  document.getElementById('week').addEventListener('click', function (e) {
    var b = e.target.closest('.day');
    if (!b || !data) return;
    picked = b.getAttribute('data-day');
    renderPanel();
  });

  fetch('/api/dashboard', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; }, function () { return null; })
    .then(function (d) {
      if (!d) {
        var list = document.getElementById('newsList');
        clear(list);
        var li = el('li', 'news-item empty'); li.appendChild(el('span', 'txt', '소식을 불러오지 못했어요. 새로고침해 주세요.'));
        list.appendChild(li);
        return;
      }
      data = d; picked = d.today;
      renderCards(); renderNews(); renderPanel();
    });
})();
