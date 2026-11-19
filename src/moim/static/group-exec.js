/* Group execution screen (stage 5): progress, team activity, members, completion records, my todos, upload, done list.

   Data comes from GET /api/groups/{id}/execution only: confirmed role names, todo titles/due dates, done state and the
   records members posted. Hidden criteria never reach the browser. Everything is built with DOM nodes and textContent
   (never an HTML string). Nothing is stored in the browser. The M2 line is shown to the submitter only (the server sends it
   only for the viewer's own records). */
(function () {
  var root = document.getElementById('exec');
  if (!root) return;
  var gid = root.getAttribute('data-group');
  var base = '/api/groups/' + encodeURIComponent(gid);
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var DAY = 86400000;
  var MSG_DELETE = '첨부 자료도 삭제되며 복구할 수 없어요. 되돌릴까요?';

  var data = null, current = null, booted = false, polls = {}, lastRecord = null, openRows = {};

  // ---- small DOM helpers ----
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function svg(paths, size, width, attrs) {
    var s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('width', size); s.setAttribute('height', size); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', width);
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) { var p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  function tick(size, w) { return svg(['M5 12.5l4.5 4.5L19 7.5'], size || 12, w || 3.2); }
  function clip() { return svg(['M20 12.5l-7.8 7.8a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8'], 14, 2); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function action() {
    var a = new Uint8Array(16); crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function parseDay(iso) { var p = iso.split('-'); return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])); }
  function md(iso) { var p = iso.split('-'); return p[1] + '/' + p[2]; }
  function setLater(fn) { if (booted) fn(); else requestAnimationFrame(function () { requestAnimationFrame(fn); }); }
  function slideIn(n, done) {
    if (calm || !n.animate) { if (done) done(); return; }
    var h = n.offsetHeight; n.style.overflow = 'hidden';
    n.animate([{ height: '0px', opacity: 0 }, { height: h + 'px', opacity: 1 }], { duration: 320, easing: 'cubic-bezier(.22,.8,.3,1)' })
      .onfinish = function () { n.style.overflow = ''; if (done) done(); };
  }
  function slideOut(n, done) {
    if (calm || !n.animate) { done(); return; }
    var h = n.offsetHeight; n.style.overflow = 'hidden';
    n.animate([{ height: h + 'px', opacity: 1 }, { height: '0px', opacity: 0 }], { duration: 260, easing: 'cubic-bezier(.4,0,.6,1)' }).onfinish = done;
  }

  function request(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (res) {
      return res.json().then(function (d) { return d; }, function () { return null; }).then(function (d) {
        if (res.ok) return { ok: true, status: res.status, data: d };
        var e = (d && d.error) || {};
        return { ok: false, status: res.status, code: e.code || 'ERROR', message: e.message || '잠시 후 다시 시도해주세요.' };
      });
    }, function () { return { ok: false, status: 0, code: 'NETWORK', message: '네트워크에 연결할 수 없어요. 잠시 후 다시 시도해주세요.' }; });
  }

  function load(fresh) {
    return fetch(base + '/execution', { credentials: 'same-origin' }).then(function (res) { return res.ok ? res.json() : null; })
      .then(function (d) { if (d) { data = d; render(fresh); } });
  }

  // Records of a member who left stay on the feed as "나간 팀원" (D-D1 D6); they have no card and no colour.
  var LEFT = { key: 'left', name: '나간 팀원', shape: '' };
  function member(key) { for (var i = 0; i < data.members.length; i++) if (data.members[i].key === key) return data.members[i]; return key === LEFT.key ? LEFT : undefined; }
  function me() { for (var i = 0; i < data.members.length; i++) if (data.members[i].is_me) return data.members[i]; }

  // ================= 그리기 =================
  function render(fresh) {
    renderProgress();
    renderMembers();
    renderActivity();
    renderFeed(fresh);
    renderTodos(fresh);
    if (current) renderMemTodos();
    booted = true;
  }

  function renderProgress() {
    var total = 0, done = 0;
    data.members.forEach(function (m) { total += m.total; done += m.done; });
    var pct = total ? Math.round(done / total * 100) : 0;
    document.getElementById('pPct').textContent = pct + '%';
    var label = total + '개 중 ' + done + '개 완료';
    document.getElementById('pTxt').textContent = label;
    var stack = document.getElementById('stack');
    stack.setAttribute('aria-label', label);
    if (stack.children.length !== data.members.length) {
      clear(stack);
      data.members.forEach(function (m) { stack.appendChild(el('span', m.shape)); });
    }
    var widths = data.members.map(function (m) { return total ? (m.done / total * 100) + '%' : '0'; });
    setLater(function () { widths.forEach(function (w, k) { stack.children[k].style.width = w; }); });
  }

  function renderMembers() {
    data.members.forEach(function (m) {
      var card = document.querySelector('.mem[data-who="' + m.key + '"]');
      if (!card) return;
      card.setAttribute('data-shape', m.shape);
      card.querySelector('.role').textContent = m.role_name || (m.leader ? '그룹장' : '팀원');
      var frac = card.querySelector('.frac');
      frac.firstChild.textContent = String(m.done);
      frac.lastChild.textContent = '/' + m.total;
      var w = m.total ? Math.round(m.done / m.total * 100) + '%' : '0';
      var bar = card.querySelector('.bar > span');
      if (!booted) bar.style.width = '0';
      setLater(function () { bar.style.width = w; });
    });
  }

  // ---- 팀 활동 잔디: 날짜별 완료 기록 수 ----
  var actScroll = document.getElementById('actScroll');
  var firstDraw = true;
  function level(n) { return n <= 0 ? 0 : Math.min(4, n); }
  function ymd(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function renderActivity() {
    var start = parseDay(data.start_date), end = parseDay(data.end_date), now = parseDay(data.today);
    if (end < start) end = start;
    var days = Math.round((end - start) / DAY) + 1;
    var months = document.getElementById('actMonths'), rows = document.getElementById('actRows'), names = document.getElementById('actNames');
    clear(months); clear(rows); clear(names);
    var lastMonth = -1;
    for (var i = 0; i < days; i++) {
      var d = new Date(start.getTime() + i * DAY);
      var first = d.getMonth() !== lastMonth && d.getDate() <= 7;
      months.appendChild(el('span', null, first ? (d.getMonth() + 1) + '월' : null));
      if (first) lastMonth = d.getMonth();
    }
    var total4w = 0, acts = [];
    data.members.forEach(function (m) {
      var row = el('div', 'act-row'), mine = 0, counts = data.activity[m.key] || {};
      for (var i = 0; i < days; i++) {
        var d = new Date(start.getTime() + i * DAY), key = ymd(d), label = (d.getMonth() + 1) + '/' + d.getDate();
        var isEnd = i === days - 1;
        var cell = el('span', 'cell');
        if (d > now) {
          cell.className = 'cell future' + (isEnd ? ' deadline' : '');
          cell.title = label + (isEnd ? ' · 마감일' : '');
        } else {
          var n = counts[key] || 0;
          if ((now - d) / DAY < 28) { total4w += n; mine += n; }
          cell.className = 'cell l' + level(n) + (key === data.today ? ' today' : '');
          cell.title = m.name + ' · ' + label + ' · ' + n + '회 활동';
        }
        row.appendChild(cell);
      }
      acts.push(mine);
      rows.appendChild(row);
      var nm = el('span', 'act-name');
      nm.appendChild(el('span', 'dot-s ' + m.shape));
      nm.appendChild(document.createTextNode(m.name));
      names.appendChild(nm);
    });
    var sum = document.getElementById('actSum');
    clear(sum);
    sum.appendChild(document.createTextNode('최근 4주 동안 '));
    sum.appendChild(el('b', null, total4w + '회'));
    sum.appendChild(document.createTextNode(' 활동'));
    // 팀 기여도: 최근 4주 활동 + 끝낸 할 일(×3)
    var scores = data.members.map(function (m, k) { return acts[k] + m.done * 3; });
    var all = scores.reduce(function (a, b) { return a + b; }, 0) || 1;
    data.members.forEach(function (m, k) {
      var card = document.querySelector('.mem[data-who="' + m.key + '"]');
      if (!card) return;
      var c = card.querySelector('.contrib');
      clear(c);
      c.appendChild(el('span', null, '팀 기여도'));
      c.appendChild(el('b', null, Math.round(scores[k] / all * 100) + '%'));
    });
    if (firstDraw) { actScroll.scrollLeft = actScroll.scrollWidth; firstDraw = false; }
    actScroll.classList.toggle('at-start', actScroll.scrollLeft < 2);
  }
  (function () {
    var down = false, sx = 0, sl = 0;
    actScroll.addEventListener('scroll', function () { actScroll.classList.toggle('at-start', actScroll.scrollLeft < 2); });
    actScroll.addEventListener('pointerdown', function (e) {
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      down = true; sx = e.clientX; sl = actScroll.scrollLeft; actScroll.classList.add('dragging');
    });
    window.addEventListener('pointermove', function (e) { if (down) actScroll.scrollLeft = sl - (e.clientX - sx); });
    window.addEventListener('pointerup', function () { down = false; actScroll.classList.remove('dragging'); });
  })();

  // ---- 팀원을 누르면 그 팀원의 할 일 ----
  var memTodos = document.getElementById('memTodos');
  var LABEL = { doing: '미완료', late: '마감 지남', urgent: '오늘 마감' };
  function renderMemTodos() {
    var m = member(current);
    if (!m) return;
    clear(memTodos);
    var head = el('div', 'mt-head');
    head.appendChild(el('span', 'dot-s ' + m.shape));
    head.appendChild(el('b', null, m.name + '의 할 일'));
    memTodos.appendChild(head);
    var list = el('ul', 'mt-list');
    var items = (data.member_todos[m.key] || []).slice();
    var open = items.filter(function (x) { return !x.done_on; });
    var done = items.filter(function (x) { return x.done_on; }).sort(function (a, b) { return a.done_on < b.done_on ? 1 : -1; });
    open.concat(done).forEach(function (x) {
      var st = x.done_on ? 'done' : x.due_date < data.today ? 'late' : x.due_date === data.today ? 'urgent' : 'doing';
      var li = el('li', 'mt-item mt-' + (st === 'urgent' ? 'late' : st));
      var s = el('span', 'st'); s.setAttribute('aria-hidden', 'true');
      if (st === 'done') s.appendChild(tick(11, 3.4));
      li.appendChild(s);
      li.appendChild(el('span', 'tt', x.title));
      li.appendChild(el('span', 'tag', st === 'done' ? '완료' : LABEL[st]));
      li.appendChild(el('time', null, md(x.done_on || x.due_date)));
      list.appendChild(li);
    });
    if (!items.length) list.appendChild(el('li', 'mt-item mt-plan', null)).appendChild(el('span', 'tt', '아직 확정된 할 일이 없어요.'));
    memTodos.appendChild(list);
  }
  document.querySelectorAll('.mem').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!data) return;
      var who = btn.getAttribute('data-who'), prev = current;
      current = current === who ? null : who;
      applyFilter();
      if (!current) { slideOut(memTodos, function () { memTodos.hidden = true; memTodos.style.overflow = ''; }); return; }
      renderMemTodos();
      if (!prev) { memTodos.hidden = false; slideIn(memTodos); }
      else if (memTodos.animate && !calm) memTodos.animate([{ opacity: .4 }, { opacity: 1 }], { duration: 220 });
    });
  });

  // ---- 팀원 완료 기록 ----
  var feed = document.getElementById('feed'), feedEmpty = document.getElementById('feedEmpty');
  function reviewLine(r, recordId) {
    var p = el('p', 'ai-line');
    p.setAttribute('data-review', recordId);
    fillReview(p, r, recordId);
    return p;
  }
  function fillReview(p, r, recordId) {
    clear(p);
    p.appendChild(document.createTextNode(r.line));
    if (r.can_retry) {
      var b = el('button', 'goal-retry', '다시 시도'); b.type = 'button';
      b.addEventListener('click', function () { retryReview(recordId, r.attempt, b); });
      p.appendChild(document.createTextNode(' '));
      p.appendChild(b);
    }
  }
  function renderFeed(fresh) {
    Array.prototype.slice.call(feed.querySelectorAll('.feed-row')).forEach(function (r) { r.remove(); });
    data.feed.forEach(function (f, i) {
      var m = member(f.who);
      var row = el('div', 'feed-row');
      row.setAttribute('data-who', f.who);
      var did = 'fd' + i;
      var btn = el('button', 'feed-item'); btn.type = 'button';
      btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-controls', did);
      var tag = el('span', 'who-tag');
      tag.appendChild(el('span', 'dot-s ' + m.shape));
      tag.appendChild(document.createTextNode(m.name));
      btn.appendChild(tag);
      btn.appendChild(el('span', 'ft', f.title));
      btn.appendChild(el('time', null, f.label));
      row.appendChild(btn);
      var det = el('div', 'detail'); det.id = did;
      if (f.memo) det.appendChild(el('p', null, f.memo));
      f.files.forEach(function (x, k) {
        var a = el('a', 'attach');
        a.href = base + '/files/' + encodeURIComponent(x.id);
        a.setAttribute('download', '');
        a.appendChild(clip());
        a.appendChild(document.createTextNode(x.name));
        if (k) det.appendChild(document.createTextNode(' '));
        det.appendChild(a);
      });
      if (!f.memo && !f.files.length) det.appendChild(el('p', 'none', '남긴 설명이나 첨부 파일이 없어요.'));
      if (f.review) {
        det.appendChild(reviewLine(f.review, f.id));
        if (f.review.state === 'PENDING' || f.review.state === 'RUNNING') poll(f.id);
      }
      row.appendChild(det);
      var key = f.id || (f.who + f.at);
      if (openRows[key]) { row.classList.add('open'); btn.setAttribute('aria-expanded', 'true'); }
      row.setAttribute('data-key', key);
      feed.insertBefore(row, feedEmpty);
      if (fresh && f.id && f.id === fresh) {
        row.classList.add('is-new');
        slideIn(row, function () { setTimeout(function () { row.classList.remove('is-new'); }, 1400); });
      }
    });
    applyFilter();
  }
  function applyFilter() {
    var shown = 0;
    feed.querySelectorAll('.feed-row').forEach(function (it) {
      var show = !current || it.getAttribute('data-who') === current;
      it.hidden = !show; if (show) shown++;
    });
    feedEmpty.style.display = shown ? 'none' : 'block';
    document.querySelectorAll('.mem').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-who') === current)); });
  }
  feed.addEventListener('click', function (e) {
    var btn = e.target.closest('.feed-item');
    if (!btn) return;
    var row = btn.closest('.feed-row'), detail = row.querySelector('.detail'), key = row.getAttribute('data-key');
    if (row.classList.contains('closing')) return;
    if (!row.classList.contains('open')) {
      row.classList.add('open'); openRows[key] = true;
      btn.setAttribute('aria-expanded', 'true');
      if (!calm && detail.animate) {
        detail.animate([{ opacity: 0, transform: 'translateY(-6px)', maxHeight: '0px', marginBottom: '0px', paddingTop: '0px', paddingBottom: '0px' },
                        { opacity: 1, transform: 'none', maxHeight: detail.scrollHeight + 40 + 'px' }], { duration: 300, easing: 'cubic-bezier(.22,.8,.3,1)' });
      }
    } else {
      btn.setAttribute('aria-expanded', 'false'); delete openRows[key];
      if (calm || !detail.animate) { row.classList.remove('open'); return; }
      row.classList.add('closing');
      detail.animate([{ opacity: 1, maxHeight: detail.offsetHeight + 'px' },
                      { opacity: 0, transform: 'translateY(-6px)', maxHeight: '0px', marginBottom: '0px', paddingTop: '0px', paddingBottom: '0px' }],
                     { duration: 220, easing: 'cubic-bezier(.4,0,.6,1)' }).onfinish = function () { row.classList.remove('open', 'closing'); };
    }
  });

  // ---- 내 할 일 · 완료한 할 일 ----
  var todoList = document.getElementById('todoList'), doneList = document.getElementById('doneList');
  var upTodo = document.getElementById('upTodo'), upDone = document.getElementById('upDone');
  function dueLabel(t) {
    if (t.due_date === data.today) return { text: '오늘 마감', urgent: true };
    return { text: md(t.due_date) + ' 마감', urgent: t.due_date < data.today };
  }
  function renderTodos(fresh) {
    var mine = data.my_todos;
    var open = mine.filter(function (t) { return !t.done_on; });
    var done = mine.filter(function (t) { return t.done_on; }).sort(function (a, b) { return a.done_on < b.done_on ? 1 : -1; });
    clear(todoList);
    open.forEach(function (t) {
      var li = el('li', 'todo' + (t.task_id === fresh ? ' fresh' : ''));
      li.setAttribute('data-id', t.task_id);
      var cb = el('button', 'cb'); cb.type = 'button';
      cb.setAttribute('aria-label', '완료하기: ' + t.title);
      cb.appendChild(tick());
      li.appendChild(cb);
      var box = el('div', 't');
      box.appendChild(el('b', null, t.title));
      var due = dueLabel(t), sm = el('small', due.urgent ? 'urgent' : null, due.text);
      box.appendChild(sm);
      li.appendChild(box);
      todoList.appendChild(li);
    });
    clear(doneList);
    done.forEach(function (t) {
      var li = el('li', 'done-item' + (t.task_id === fresh ? ' fresh' : ''));
      var b = el('button', 'done-btn'); b.type = 'button';
      b.setAttribute('data-id', t.task_id);
      b.setAttribute('aria-label', '완료 취소: ' + t.title);
      b.title = '누르면 내 할 일로 돌아가요';
      b.appendChild(el('span', 'title', t.title));
      b.appendChild(el('time', null, md(t.done_on)));
      b.appendChild(el('span', 'undo', '되돌리기'));
      var tk = el('span', 'tick'); tk.setAttribute('aria-hidden', 'true'); tk.appendChild(tick());
      b.appendChild(tk);
      li.appendChild(b);
      doneList.appendChild(li);
    });
    document.getElementById('todoCount').textContent = open.length;
    document.getElementById('doneCount').textContent = done.length;
    var empty = document.getElementById('todoEmpty');
    empty.textContent = mine.length ? '남은 할 일이 없어요. 수고했어요!' : '역할과 할 일을 확정하면 여기에 보여요.';
    empty.style.display = open.length ? 'none' : 'block';
    document.getElementById('chatText').textContent = mine.length ? 'AI와 역할·할 일 다시 나누기' : '내 AI와 대화 시작하기';
    // 올리기 폼의 할 일 목록
    var keep = upTodo.value;
    clear(upTodo);
    open.forEach(function (t) { var o = el('option', null, t.title); o.value = t.task_id; upTodo.appendChild(o); });
    var etc = el('option', null, '기타'); etc.value = 'etc'; upTodo.appendChild(etc);
    if (keep && upTodo.querySelector('option[value="' + CSS.escape(keep) + '"]')) upTodo.value = keep;
    syncDoneOption();
  }
  function byTask(id) { for (var i = 0; i < data.my_todos.length; i++) if (data.my_todos[i].task_id === id) return data.my_todos[i]; }

  todoList.addEventListener('click', function (e) {
    var btn = e.target.closest('.cb');
    if (!btn || btn.classList.contains('checked')) return;
    var li = btn.closest('.todo'), t = byTask(li.getAttribute('data-id'));
    if (!t) return;
    btn.classList.add('checked');
    var fd = new FormData();
    fd.append('action_id', action());
    fd.append('task_id', t.task_id);
    fd.append('confirmation_revision', String(t.confirmation_revision));
    fd.append('marks_done', 'true');
    var sent = request('POST', base + '/records', fd);
    setTimeout(function () { li.classList.add('leaving'); }, 450);
    setTimeout(function () {
      sent.then(function (r) {
        if (!r.ok) { showMsg(r.message); }
        load(t.task_id);
      });
    }, 750);
  });
  doneList.addEventListener('click', function (e) {
    var btn = e.target.closest('.done-btn');
    if (!btn) return;
    var t = byTask(btn.getAttribute('data-id'));
    if (!t || !t.record_id) return;
    if (t.has_files && !window.confirm(MSG_DELETE)) return; // 취소하면 변경 없음 (D-D1 D5)
    request('POST', base + '/records/' + encodeURIComponent(t.record_id) + '/undo', { confirm_delete: !!t.has_files }).then(function (r) {
      if (!r.ok && r.status !== 404) showMsg(r.message);
      load(t.task_id);
    });
  });

  // ---- 파일 첨부 → 올리기 ----
  var drop = document.getElementById('drop'), input = document.getElementById('fileInput');
  var stagedBox = document.getElementById('staged'), upBtn = document.getElementById('upBtn');
  var upMemo = document.getElementById('upMemo'), upTitle = document.getElementById('upTitle'), upMsg = document.getElementById('upMsg');
  var staged = [], busy = false;
  function size(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + 'MB' : Math.max(1, Math.round(n / 1024)) + 'KB'; }
  function ext(name) { var m = /\.([a-z0-9]{1,4})$/i.exec(name); return m ? m[1] : 'file'; }
  function renderStaged() {
    clear(stagedBox);
    staged.forEach(function (f, i) {
      var row = el('div', 'sfile'); row.setAttribute('data-i', i);
      row.appendChild(el('span', 'ext', ext(f.name)));
      var info = el('span', 'info');
      info.appendChild(el('span', 'name', f.name));
      info.appendChild(el('span', 'meta', size(f.size)));
      var pb = el('span', 'pbar'); pb.appendChild(el('span'));
      info.appendChild(pb);
      row.appendChild(info);
      var ok = el('span', 'ok tick'); ok.setAttribute('aria-label', '업로드 완료'); ok.appendChild(tick());
      row.appendChild(ok);
      var rm = el('button', 'rm'); rm.type = 'button'; rm.setAttribute('aria-label', f.name + ' 첨부 취소');
      rm.appendChild(svg(['M6 6l12 12M18 6L6 18'], 14, 2.2));
      row.appendChild(rm);
      stagedBox.appendChild(row);
    });
    syncBtn();
  }
  function addFiles(list) {
    if (busy) return;
    Array.prototype.forEach.call(list, function (f) { staged.push(f); });
    upMsg.classList.remove('show');
    renderStaged();
  }
  function syncDoneOption() {
    var etc = upTodo.value === 'etc';
    upDone.disabled = etc;
    upDone.parentElement.style.opacity = etc ? .4 : 1;
  }
  upTodo.addEventListener('change', syncDoneOption);
  stagedBox.addEventListener('click', function (e) {
    var rm = e.target.closest('.rm');
    if (!rm || busy) return;
    staged.splice(Number(rm.closest('.sfile').getAttribute('data-i')), 1);
    renderStaged();
  });
  input.addEventListener('change', function () { addFiles(input.files); input.value = ''; });
  ['dragenter', 'dragover'].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
  ['dragleave', 'drop'].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
  drop.addEventListener('drop', function (e) { if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files); });
  function syncBtn() {
    if (busy) return;
    upBtn.disabled = !staged.length && !upMemo.value.trim() && !upTitle.value.trim();
    upBtn.textContent = staged.length ? '첨부하기 (파일 ' + staged.length + '개)' : '첨부하기';
  }
  upMemo.addEventListener('input', syncBtn);
  upTitle.addEventListener('input', syncBtn);

  function showMsg(text, ok, retry) {
    clear(upMsg);
    if (ok) { var t = el('span', 'tick'); t.setAttribute('aria-hidden', 'true'); t.appendChild(tick()); upMsg.appendChild(t); }
    upMsg.appendChild(el('span', null, text));
    if (retry) upMsg.appendChild(retry);
    upMsg.classList.add('show');
  }

  upBtn.addEventListener('click', function () {
    if (busy || upBtn.disabled || !data) return;
    var todo = upTodo.value && upTodo.value !== 'etc' ? byTask(upTodo.value) : null;
    busy = true; upBtn.disabled = true;
    var fd = new FormData();
    fd.append('action_id', action());
    if (todo) {
      fd.append('task_id', todo.task_id);
      fd.append('confirmation_revision', String(todo.confirmation_revision));
      fd.append('marks_done', upDone.checked ? 'true' : 'false');
    }
    fd.append('title', upTitle.value.trim());
    fd.append('memo', upMemo.value.trim());
    staged.forEach(function (f) { fd.append('files', f, f.name); });
    var rows = stagedBox.querySelectorAll('.sfile');
    rows.forEach(function (r) { r.classList.add('uploading'); });
    upBtn.textContent = staged.length ? '첨부하는 중 0%' : '첨부하는 중…';
    var xhr = new XMLHttpRequest();
    xhr.open('POST', base + '/records');
    xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable || !staged.length) return;
      var p = Math.min(100, Math.round(e.loaded / e.total * 100));
      rows.forEach(function (r) {
        r.querySelector('.pbar span').style.width = p + '%';
        r.querySelector('.meta').textContent = p >= 100 ? '업로드 완료' : p + '% 올리는 중';
      });
      upBtn.textContent = p >= 100 ? '완료!' : '첨부하는 중 ' + p + '%';
    };
    xhr.onload = function () {
      var d = null; try { d = JSON.parse(xhr.responseText); } catch (err) { d = null; }
      if (xhr.status === 200 || xhr.status === 201) {
        rows.forEach(function (r) { r.classList.remove('uploading'); r.classList.add('done'); });
        setTimeout(function () { finish(d, todo); }, 450);
      } else fail((d && d.error && d.error.message) || '첨부하지 못했어요. 잠시 후 다시 시도해주세요.');
    };
    xhr.onerror = function () { fail('네트워크에 연결할 수 없어요. 잠시 후 다시 시도해주세요.'); };
    xhr.send(fd);
  });
  function fail(message) {
    busy = false;
    stagedBox.querySelectorAll('.sfile').forEach(function (r) { r.classList.remove('uploading', 'done'); });
    showMsg(message);
    syncBtn();
  }
  function finish(rec, todo) {
    var base1 = todo && rec.marks_done ? '첨부했어요! \'' + todo.title + '\' 할 일도 완료로 표시했어요.' : '첨부했어요! 팀원 완료 기록에서 볼 수 있어요.';
    lastRecord = rec.id;
    if (rec.review) {
      showMsg(base1 + ' ' + rec.review.line, true);
      if (rec.review.state === 'PENDING' || rec.review.state === 'RUNNING') poll(rec.id);
    } else showMsg(base1, true);
    staged = []; upMemo.value = ''; upTitle.value = ''; busy = false;
    renderStaged();
    load(rec.id);
  }

  // ---- AI 확인 결과 (본인 기록만) ----
  function paintReview(recordId, r) {
    var p = feed.querySelector('[data-review="' + CSS.escape(recordId) + '"]');
    if (p) fillReview(p, r, recordId);
    if (recordId === lastRecord) {
      var b = null;
      if (r.can_retry) {
        b = el('button', 'goal-retry', '다시 시도'); b.type = 'button';
        b.addEventListener('click', function () { retryReview(recordId, r.attempt, b); });
      }
      showMsg(r.line, r.state === 'SUCCEEDED' && !b, b);
    }
  }
  function poll(recordId) {
    if (polls[recordId]) return;
    polls[recordId] = setTimeout(function step() {
      fetch(base + '/records/' + encodeURIComponent(recordId) + '/review', { credentials: 'same-origin' })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (r) {
          if (!r) { delete polls[recordId]; return; }
          if (r.state === 'PENDING' || r.state === 'RUNNING') { polls[recordId] = setTimeout(step, 2500); return; }
          delete polls[recordId];
          paintReview(recordId, r);
          load();
        }, function () { polls[recordId] = setTimeout(step, 5000); });
    }, 2500);
  }
  function retryReview(recordId, attempt, btn) {
    if (btn.disabled) return;
    btn.disabled = true;
    lastRecord = recordId;
    request('POST', base + '/records/' + encodeURIComponent(recordId) + '/review/retry', { expected_attempt: attempt }).then(function (r) {
      if (r.ok) { paintReview(recordId, r.data); poll(recordId); return; }
      btn.disabled = false;
      showMsg(r.message);
    });
  }

  load();
})();
