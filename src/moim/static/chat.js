/* Chat screen. Before the assignment (D-B3): the group's shared role candidates (left) and this member's own ranking /
   waiting state (right). After the server assignment (stage 4, ROLE_TODOS): the server's fixed result as AI bubbles,
   then the AI-led interview (questions, quick reply buttons, the input) and the member's confirm step, all in the same chat.
   Both sides are painted from ONE server payload (GET /api/groups/{id}/roles), so they never disagree. Nothing is
   stored in the browser, the browser never talks to the AI provider, and server text is only written with textContent. */
(function () {
  var main = document.getElementById('main');
  var gid = main.getAttribute('data-group');
  var base = '/api/groups/' + encodeURIComponent(gid);
  function $(id) { return document.getElementById(id); }
  var log = $('log'), quick = $('quick'), form = $('form'), input = $('input'), send = $('send');
  var msg = $('msg'), emptyText = $('emptyText');
  var submitRank = $('submitRank'), retryCoverage = $('retryCoverage');
  var data = null, picks = [], pickRevision = null, timer = null, busy = false, lastRank = null;
  var startId = null, startRev = null, lastAnswer = null, lastPainted = null, localErr = null;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var GREETING = '안녕하세요! 아래 버튼에서 하고 싶은 역할 세 개를 1·2·3순위로 눌러 주세요.';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var a = new Uint8Array(16);
    crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function say(text, err) {
    msg.textContent = text || '';
    msg.classList.toggle('err', !!err);
  }

  // ---- left: messages ----
  function botFace() {
    var src = document.querySelector('.card-head .av.bot');
    return src ? src.cloneNode(true) : el('span', 'av bot');
  }
  function row(text, cls) {
    var r = el('div', 'row');
    var col = el('div', 'col');
    r.appendChild(botFace());
    col.appendChild(el('span', 'who', 'MOIM AI'));
    col.appendChild(el('div', 'bubble' + (cls ? ' ' + cls : ''), text));
    r.appendChild(col);
    return r;
  }
  function meRow(text) {
    var r = el('div', 'row me');
    var col = el('div', 'col');
    col.appendChild(el('div', 'bubble', text));
    r.appendChild(col);
    return r;
  }
  function typingRow() {
    var r = el('div', 'row');
    var col = el('div', 'col');
    r.appendChild(botFace());
    col.appendChild(el('span', 'who', 'MOIM AI'));
    var b = el('div', 'bubble typing');
    b.setAttribute('aria-label', 'AI가 답을 쓰고 있어요');
    b.appendChild(el('i'));
    b.appendChild(el('i'));
    b.appendChild(el('i'));
    col.appendChild(b);
    r.appendChild(col);
    return r;
  }
  function paintLog(d) {
    var c = d.candidates;
    var frag = document.createDocumentFragment();
    if (d.phase === 'ROLE_TODOS' && d.role) {
      var role = d.role, iv = role.interview;
      role.announcement.forEach(function (t) { frag.appendChild(row(t)); });
      iv.messages.forEach(function (m) { frag.appendChild(m.from === 'me' ? meRow(m.text) : row(m.text, m.err ? 'err' : '')); });
      if (iv.busy || (!iv.started && iv.state === 'ASKING')) frag.appendChild(typingRow());
      var tdv = role.todos;
      if (tdv) {
        tdv.messages.forEach(function (m) { frag.appendChild(m.from === 'me' ? meRow(m.text) : row(m.text, m.err ? 'err' : '')); });
        if (tdv.busy || (tdv.state === 'NONE' && tdv.can_start)) frag.appendChild(typingRow());
      }
    } else if (c.state === 'SUCCEEDED') {
      frag.appendChild(row(GREETING));
      if (d.phase === 'WAITING_ASSIGNMENT') frag.appendChild(row('순위를 제출했어요. 다른 팀원이 모두 제출하면 역할이 정해져요.'));
    } else if (c.state === 'FAILED') {
      frag.appendChild(row(c.error_message || '역할 후보를 만들지 못했어요. 다시 시도해주세요.', 'err'));
    } else {
      frag.appendChild(row('역할 후보를 만들고 있어요…'));
    }
    // repaint only when something changed, so polling does not replay the bubble animation
    var tk = d.role && d.role.todos ? [d.role.todos.messages, d.role.todos.busy, d.role.todos.state, d.role.todos.can_start] : null;
    var key = JSON.stringify([d.phase, d.role && d.role.interview, tk, c.state]);
    if (key === lastPainted) return;
    lastPainted = key;
    log.textContent = '';
    log.appendChild(frag);
    log.scrollTo({ top: log.scrollHeight, behavior: reduce ? 'auto' : 'smooth' });
  }

  // ---- left: chips ----
  function chip(label, onClick, pressed, hint) {
    var b = el('button', null, label);
    if (hint) b.title = hint;
    b.type = 'button';
    if (pressed !== undefined) b.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    b.addEventListener('click', onClick);
    return b;
  }
  function paintQuick(d) {
    quick.textContent = '';
    var c = d.candidates;
    if (d.phase === 'ROLE_TODOS' && d.role) {
      var iv = d.role.interview, tq = d.role.todos;
      if (iv.failed) {
        quick.appendChild(chip('다시 시도', function () { retryTurn(iv.failed); }));
      } else if (tq && tq.failed && !tq.busy) {
        quick.appendChild(chip('다시 시도', function () { retryTodo(tq.failed); }));
      } else if (iv.question && !iv.busy) {
        iv.question.options.forEach(function (o) {
          quick.appendChild(chip(o.label, function () { answerButton(iv.question.question_id, o.option_id); }));
        });
      }
      return;
    }
    if (d.phase === 'INTERVIEW' && c.state === 'FAILED' && c.can_retry) {
      quick.appendChild(chip('역할 후보 다시 만들기', function () { retryCandidates(c); }));
    }
    if (d.ranking_available) {
      quick.appendChild(el('span', 'pick-note', '1순위부터 순서대로 눌러 주세요. 버튼에 마우스를 올리면 설명이 보여요. 다시 누르면 선택이 풀려요.'));
      c.items.forEach(function (it) {
        var idx = picks.indexOf(it.id);
        quick.appendChild(chip(idx >= 0 ? (idx + 1) + '순위 · ' + it.name : it.name, function () { togglePick(it.id); }, idx >= 0, it.description));
      });
    }
  }

  function togglePick(id) {
    var i = picks.indexOf(id);
    if (i >= 0) picks.splice(i, 1);
    else if (picks.length < 3) picks.push(id);
    lastRank = null;
    paint(data);
  }

  // ---- right panel ----
  function rankRow(n, name) {
    var box = el('div', 'rolebox state');
    box.appendChild(el('span', 'rl', n + '순위'));
    box.appendChild(el('b', null, name));
    return box;
  }
  function paintGoal(d) {
    var g = d.group;
    $('grpName').textContent = g.name;
    var gb = $('goalbox');
    if (g.goal.state === 'SUCCEEDED' && g.goal.text) {
      $('dday').textContent = g.dday;
      $('goalText').textContent = g.goal.text;
      $('goalDue').textContent = g.end_date.replace(/^(\d+)-(\d+)-(\d+)$/, function (m, y, mo, da) { return y + '년 ' + Number(mo) + '월 ' + Number(da) + '일'; });
      gb.hidden = false;
    } else {
      gb.hidden = true;
    }
  }
  function paintPanel(d) {
    paintGoal(d);
    var roleOn = d.phase === 'ROLE_TODOS' && d.role;
    $('panelTitle').textContent = roleOn ? '내 투두리스트' : (d.phase === 'WAITING_ASSIGNMENT' ? '역할 배정 대기' : '역할 순위 선택');
    $('rolebox').hidden = !roleOn;
    if (roleOn) {
      $('roleName').textContent = d.role.role_name;
      $('roleRank').textContent = d.role.rank_text;
    }

    var rr = $('rankRows');
    rr.textContent = '';
    var ranks = null;
    if (d.phase === 'WAITING_ASSIGNMENT' && d.submitted_ranks) {
      ranks = d.submitted_ranks.map(function (r) { return [r.rank, r.name]; });
    } else if (d.ranking_available && picks.length) {
      var names = {};
      d.candidates.items.forEach(function (it) { names[it.id] = it.name; });
      ranks = picks.map(function (id, i) { return [i + 1, names[id] || '']; });
    }
    (ranks || []).forEach(function (r) { rr.appendChild(rankRow(r[0], r[1])); });
    $('rankHead').hidden = !(ranks && ranks.length);

    emptyText.textContent = '';
    var tv = roleOn ? d.role.todos : null;
    var hasTodos = !!(tv && tv.state === 'READY');
    $('empty').hidden = hasTodos;
    paintTodos(hasTodos ? tv : null);
    if (roleOn && tv && tv.state !== 'NONE' && !hasTodos) {
      emptyText.textContent = tv.state === 'FAILED' ? '투두 초안을 만들지 못했어요. 왼쪽에서 다시 시도해 주세요.'
        : '역할과 인터뷰 내용으로 투두 초안을 만들고 있어요…';
    } else if (roleOn) {
      var st = d.role.interview.state;
      emptyText.textContent = st === 'DONE' ? '인터뷰를 마쳤어요. 이 내용으로 투두 초안을 만들 거예요.'
        : st === 'CONFIRMING' ? '알려 주신 내용을 확인하면 투두 초안을 만들어요.'
        : 'AI의 질문 몇 개에 답하면 여기에 내 투두 초안이 만들어져요.';
    } else {
      if (d.phase === 'WAITING_ASSIGNMENT') {
        emptyText.appendChild(document.createTextNode('순위를 제출했어요. 다른 팀원을 기다리고 있어요'));
      } else {
        emptyText.textContent = '역할이 정해지면 여기에 내 투두가 만들어져요.';
      }
      if (d.progress) {
        emptyText.appendChild(document.createElement('br'));
        emptyText.appendChild(el('b', null, d.progress.submitted + '/' + d.progress.total + '명 제출 완료'));
      }
    }

    submitRank.hidden = !d.ranking_available;
    submitRank.disabled = busy || picks.length !== 3;
    var cov = roleOn ? d.role.coverage : null;
    retryCoverage.hidden = !(cov && cov.can_retry);
    retryCoverage.disabled = busy;
  }

  // ---- right panel: my todos (stage 4 session B) ----
  // Same rows as the original design (check box, title input, due-date pill + calendar, ×). The check box stays
  // inert until stage 5. Only the title, the due date and a draft-local key come from the server.
  var todosEl = $('todos'), confirmBtn = $('confirm'), addBtn = $('add'), addRow = document.querySelector('.addrow');
  var countEl = $('count'), pill = document.querySelector('.head .pill'), pillCount = $('pillCount');
  var cal = $('cal'), doneEl = $('done');
  var rowsKey = null, blank = false, pendingDone = false, calFor = null, calPick = null, calView = null, period = null;
  var SVGNS = 'http://www.w3.org/2000/svg';

  function svg(w, sw, paths, rect) {
    var s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('width', w); s.setAttribute('height', w); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', sw);
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    if (rect) {
      var r = document.createElementNS(SVGNS, 'rect');
      Object.keys(rect).forEach(function (k) { r.setAttribute(k, rect[k]); });
      s.appendChild(r);
    }
    paths.forEach(function (p) { var e = document.createElementNS(SVGNS, 'path'); e.setAttribute('d', p); s.appendChild(e); });
    return s;
  }
  function parse(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function pretty(s) { var d = parse(s); return (d.getMonth() + 1) + '월 ' + d.getDate() + '일(' + '일월화수목금토'[d.getDay()] + ')'; }
  function shortDate(s) { var d = parse(s); return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + '일월화수목금토'[d.getDay()]; }
  function daysLeft(s) { return Math.round((parse(s) - parse(period.today)) / 86400000); }

  function todoRow(t, editable) {
    var li = el('li', 'todo');
    var n = el('button', 'n');
    n.type = 'button';
    n.setAttribute('role', 'checkbox');
    n.setAttribute('aria-checked', 'false');
    n.setAttribute('aria-label', '완료 표시');
    n.disabled = true;  // completion is stage 5
    n.appendChild(svg(12, 3.4, ['M5 12.5l4.5 4.5L19 7.5']));
    var tx = el('input', 't');
    tx.type = 'text';
    tx.value = t.title || '';
    tx.maxLength = 60;
    tx.setAttribute('aria-label', '할 일');
    tx.readOnly = !editable;
    if (!t.key) tx.placeholder = '할 일을 적고 Enter';
    function commit() {
      var v = tx.value.trim();
      if (!t.key) {
        if (v) saveEdit('add', null, { title: v }); else { blank = false; rowsKey = null; paint(data); }
        return;
      }
      if (!v) { tx.value = t.title; return; }
      if (v !== t.title) saveEdit('update', t.key, { title: v });
    }
    tx.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); tx.blur(); }
      if (e.key === 'Escape') { tx.value = t.title || ''; tx.blur(); }
    });
    tx.addEventListener('blur', function () { if (editable) commit(); });

    var d = el('button', 'due');
    d.type = 'button';
    d.appendChild(svg(13, 2.2, ['M8 3v4M16 3v4M3.5 10h17'], { x: '3.5', y: '5', width: '17', height: '15.5', rx: '4' }));
    var dv = el('span', 'dv', t.due_date ? shortDate(t.due_date) : '기한 설정');
    var dd = el('span', 'dd');
    var left = t.due_date ? daysLeft(t.due_date) : null;
    dd.textContent = left === null ? '' : left === 0 ? 'D-DAY' : 'D-' + left;
    if (left === null) dd.style.display = 'none';
    d.appendChild(dv); d.appendChild(dd);
    d.classList.toggle('near', left !== null && left <= 3);
    d.classList.toggle('unset', !t.due_date);
    d.setAttribute('aria-label', '마감일: ' + (t.due_date ? pretty(t.due_date) : '정하지 않음') + (editable && t.key ? ' (눌러서 달력 열기)' : ''));
    d.disabled = !editable || !t.key;
    d.addEventListener('click', function (e) {
      e.stopPropagation();
      openCal(d, t.due_date, function (v) { if (v !== t.due_date) saveEdit('update', t.key, { due_date: v }); });
    });

    var x = el('button', 'x');
    x.type = 'button';
    x.setAttribute('aria-label', '삭제');
    x.disabled = !editable;
    x.appendChild(svg(16, 2.2, ['M6 6l12 12M18 6L6 18']));
    x.addEventListener('click', function () {
      if (!t.key) { blank = false; rowsKey = null; paint(data); return; }
      saveEdit('delete', t.key, {});
    });
    li.appendChild(n); li.appendChild(tx); li.appendChild(d); li.appendChild(x);
    return li;
  }

  function editableNow(tv) { return !!(tv && tv.mode === 'EDITING' && tv.input_open && !busy); }

  function paintTodos(tv) {
    var on = !!tv;
    var list = on ? tv.items.slice() : [];
    period = on ? tv.period : period;
    var editable = editableNow(tv);
    if (!editable) blank = false;
    if (blank) list.push({ key: null, title: '', due_date: null });
    countEl.hidden = !on; pill.hidden = !on;
    countEl.textContent = list.length; pillCount.textContent = list.length;
    addRow.hidden = !on || tv.mode !== 'EDITING';
    addBtn.disabled = !editable || blank;
    confirmBtn.hidden = !on;
    if (on) {
      confirmBtn.textContent = tv.mode === 'CONFIRMED' ? '확정 풀기' : (tv.confirming ? '확정하는 중…' : '투두 확정하기');
      confirmBtn.classList.toggle('primary', tv.mode !== 'CONFIRMED');
      confirmBtn.disabled = busy || tv.busy || tv.stale;
      if (pendingDone && tv.mode === 'CONFIRMED' && !tv.busy) { pendingDone = false; showDone(list.length); }
      if (pendingDone && tv.failed) pendingDone = false;
    }
    var key = JSON.stringify([list, editable, on && tv.mode]);
    if (key === rowsKey) return;
    if (todosEl.contains(document.activeElement) && document.activeElement.tagName === 'INPUT' && !blank) return;  // keep typing
    rowsKey = key;
    todosEl.textContent = '';
    list.forEach(function (t) { todosEl.appendChild(todoRow(t, editable)); });
    if (blank) { var inputs = todosEl.querySelectorAll('.t'); inputs[inputs.length - 1].focus(); }
  }

  function saveEdit(op, key, extra) {
    var tv = data && data.role && data.role.todos;
    if (busy || !tv) return;
    var body = { action_id: newId(), draft_revision: tv.draft_revision, op: op };
    if (key) body.key = key;
    Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
    busy = true;
    if (op === 'add') blank = false;
    rowsKey = null;
    MoimAuth.post(base + '/todos/edit', body).then(after);
  }

  addBtn.addEventListener('click', function () {
    var tv = data && data.role && data.role.todos;
    if (!editableNow(tv) || blank) return;
    if (tv.items.length >= tv.max_todos) { say('투두는 최대 ' + tv.max_todos + '개까지 만들 수 있어요.', true); return; }
    blank = true;
    rowsKey = null;
    paintTodos(tv);
  });

  confirmBtn.addEventListener('click', function () {
    var tv = data && data.role && data.role.todos;
    if (busy || !tv || tv.busy) return;
    busy = true;
    confirmBtn.disabled = true;
    if (tv.mode === 'CONFIRMED') { MoimAuth.post(base + '/todos/unlock', { action_id: newId() }).then(after); return; }
    MoimAuth.post(base + '/todos/confirm', { action_id: newId(), draft_revision: tv.draft_revision }).then(function (r) {
      if (r.ok) pendingDone = true;  // shown once the server says CONFIRMED (at once, or after the criteria step)
      after(r);
    });
  });

  function showDone(n) {
    var name = main.getAttribute('data-name') || '';
    $('doneText').textContent = name + '님의 투두 ' + n + '개가\n' + (data ? data.group.name : '') + ' 그룹에 저장됐어요.';
    doneEl.classList.add('show');
    doneEl.focus();
  }
  function goGroup() { MoimAuth.go('/groups/' + encodeURIComponent(gid)); }
  doneEl.addEventListener('click', goGroup);
  doneEl.addEventListener('keydown', function (e) { if (e.key === 'Escape' || e.key === 'Enter') { e.preventDefault(); goGroup(); } });

  // ---- calendar (same look as the original; built with DOM nodes) ----
  function openCal(anchor, value, onPick) {
    if (calFor === anchor && !cal.hidden) { closeCal(); return; }
    calFor = anchor; calPick = onPick;
    calView = parse(value || period.first_day); calView.setDate(1);
    drawCal(value);
    cal.hidden = false;
    var r = anchor.getBoundingClientRect(), w = cal.offsetWidth, h = cal.offsetHeight;
    var left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
    var top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
    cal.style.left = left + 'px';
    cal.style.top = top + 'px';
    var f = cal.querySelector('.cd.sel') || cal.querySelector('.cd.today') || cal.querySelector('.cd:not(:disabled)');
    if (f) f.focus({ preventScroll: true });
  }
  function closeCal() { cal.hidden = true; if (calFor) calFor.focus({ preventScroll: true }); calFor = null; }
  function navBtn(dir, disabled) {
    var b = el('button', 'cal-nav');
    b.type = 'button';
    b.setAttribute('aria-label', dir < 0 ? '이전 달' : '다음 달');
    b.disabled = disabled;
    b.appendChild(svg(16, 2.2, [dir < 0 ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6']));
    b.addEventListener('click', function (e) { e.stopPropagation(); calView.setMonth(calView.getMonth() + dir); drawCal(null); });
    return b;
  }
  function drawCal(selected) {
    var y = calView.getFullYear(), m = calView.getMonth();
    var first = new Date(y, m, 1).getDay(), days = new Date(y, m + 1, 0).getDate();
    var lo = period.first_day, hi = period.end_date, cur = iso(calView).slice(0, 7);
    cal.textContent = '';
    var head = el('div', 'cal-head');
    head.appendChild(navBtn(-1, cur <= lo.slice(0, 7)));
    head.appendChild(el('b', null, y + '년 ' + (m + 1) + '월'));
    head.appendChild(navBtn(1, cur >= hi.slice(0, 7)));
    cal.appendChild(head);
    var grid = el('div', 'cal-grid');
    '일월화수목금토'.split('').forEach(function (w) { grid.appendChild(el('span', 'cw', w)); });
    for (var i = 0; i < first; i++) grid.appendChild(el('span'));
    for (var dn = 1; dn <= days; dn++) {
      var v = iso(new Date(y, m, dn));
      var b = el('button', 'cd' + (v === period.today ? ' today' : '') + (v === selected ? ' sel' : '') + (v === hi ? ' end' : ''), String(dn));
      b.type = 'button';
      b.disabled = v < lo || v > hi;
      b.setAttribute('aria-label', pretty(v) + (v === hi ? ' 그룹 기한' : ''));
      (function (val) { b.addEventListener('click', function (e) { e.stopPropagation(); var pick = calPick; closeCal(); pick(val); }); })(v);
      grid.appendChild(b);
    }
    cal.appendChild(grid);
    var foot = el('p', 'cal-foot');
    foot.appendChild(el('i', 'dot-end'));
    foot.appendChild(document.createTextNode('그룹 기한 ' + pretty(hi)));
    cal.appendChild(foot);
  }
  cal.addEventListener('click', function (e) { e.stopPropagation(); });
  document.addEventListener('click', function () { if (!cal.hidden) { cal.hidden = true; calFor = null; } });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !cal.hidden) closeCal(); });
  window.addEventListener('resize', function () { if (!cal.hidden) { cal.hidden = true; calFor = null; } });

  function paintStatus(d) {
    var c = d.candidates;
    if (d.phase === 'ROLE_TODOS' && d.role) {
      var cov = d.role.coverage;
      if (cov.state === 'FAILED') say(cov.error_message || '팀 결과물 분담을 정하지 못했어요. 다시 시도해주세요.', true);
      else if (localErr) say(localErr, true);  // e.g. a meaningless answer: nothing was stored, the member types again
      else say('');
      return;
    }
    if (d.ranking_reset && d.phase === 'INTERVIEW') say('프로젝트 정보가 바뀌어 역할 후보가 새로 만들어졌어요. 순위를 다시 골라 주세요.', false);
    else if (d.phase === 'INTERVIEW' && (c.state === 'PENDING' || c.state === 'RUNNING')) say('역할 후보를 만들고 있어요…', false);
    else if (d.phase === 'INTERVIEW' && c.state === 'FAILED') say(c.error_message || '역할 후보를 만들지 못했어요. 다시 시도해주세요.', true);
    else say('');
  }

  function paintComposer(d) {
    if (d.phase === 'ROLE_TODOS' && d.role && d.role.todos && d.role.todos.state === 'READY') {
      var tc = d.role.todos;
      var topen = tc.input_open && !busy;
      input.disabled = !topen;
      send.disabled = !topen || !input.value.trim();
      input.placeholder = tc.mode === 'CONFIRMED' ? '확정된 투두예요. 고치려면 확정을 풀어 주세요'
        : tc.busy ? 'AI가 투두를 고치고 있어요' : '투두를 어떻게 바꿀지 말해 주세요 (예: 마감을 더 여유 있게 해줘)';
      return;
    }
    if (d.phase === 'ROLE_TODOS' && d.role) {
      var iv = d.role.interview;
      var open = iv.input_open && !iv.busy && !iv.failed && !busy;
      input.disabled = !open;
      send.disabled = !open || !input.value.trim();
      input.placeholder = !iv.input_open ? (iv.state === 'DONE' ? '인터뷰를 마쳤어요' : '아래 버튼에서 골라 주세요')
        : iv.editing ? '여기에 적어 주세요' : '버튼을 고르거나 직접 적어 주세요';
      return;
    }
    input.disabled = true;
    send.disabled = true;
    input.placeholder = d.phase === 'WAITING_ASSIGNMENT' ? '순위를 제출했어요' : '역할 순위를 골라 주세요';
  }

  function pending(d) {
    if (d.phase === 'ROLE_TODOS' && d.role) {
      var iv = d.role.interview, cov = d.role.coverage, t = d.role.todos;
      return iv.busy || !iv.started || cov.state === 'PENDING' || cov.state === 'RUNNING' || !!(t && (t.busy || t.state === 'GENERATING'));
    }
    return d.phase === 'INTERVIEW' && (d.candidates.state === 'PENDING' || d.candidates.state === 'RUNNING');
  }
  function schedule() {
    clearTimeout(timer);
    if (!data) return;
    if (pending(data)) timer = setTimeout(load, 1500);
    else timer = setTimeout(load, 6000);  // submitted count, the assignment, and a ProjectBrief change
  }

  function maybeStart(d) {
    if (d.phase !== 'ROLE_TODOS' || !d.role) return;
    var iv = d.role.interview;
    if (iv.started || iv.state !== 'ASKING' || busy) return;
    var rev = d.role.coverage.assignment_revision;
    // one action id per page and assignment revision: a resend can never open two first questions, and a new revision
    // (reassignment, ProjectBrief change) gets a new id
    if (!startId || startRev !== rev) { startId = newId(); startRev = rev; }
    busy = true;
    MoimAuth.post(base + '/role-interview/start', { action_id: startId }).then(after);
  }
  var todoStartId = null, todoStartRev = null;
  function maybeStartTodos(d) {
    if (d.phase !== 'ROLE_TODOS' || !d.role || !d.role.todos) return;
    var t = d.role.todos;
    if (t.state !== 'NONE' || !t.can_start || busy) return;
    var trev = d.role.coverage.assignment_revision;
    // the draft starts once per assignment revision, whatever the number of tabs or reloads
    if (!todoStartId || todoStartRev !== trev) { todoStartId = newId(); todoStartRev = trev; }
    busy = true;
    MoimAuth.post(base + '/todos/start', { action_id: todoStartId }).then(after);
  }
  function retryTodo(f) {
    if (busy) return;
    busy = true;
    MoimAuth.post(base + '/todos/retry', { action_id: newId(), seq: f.seq, expected_attempt: f.attempt }).then(after);
  }
  var lastRevise = null;
  function postRevise(text) {
    if (busy) return;
    if (!lastRevise || lastRevise.text !== text) lastRevise = { text: text, id: newId() };
    busy = true;
    paintComposer(data);
    MoimAuth.post(base + '/todos/revise', { action_id: lastRevise.id, message: text }).then(function (r) {
      if (r.ok) { lastRevise = null; input.value = ''; } else if (r.status === 422) lastRevise = null;
      after(r);
    });
  }

  function paint(d) {
    data = d;
    // a new candidate revision means new meaning behind the same ids: drop the unsent choice
    if (pickRevision !== d.candidates.candidate_revision) { picks = []; pickRevision = d.candidates.candidate_revision; lastRank = null; }
    if (d.ranking_available) {
      var ids = d.candidates.items.map(function (it) { return it.id; });
      picks = picks.filter(function (id) { return ids.indexOf(id) >= 0; });
    } else {
      picks = [];
    }
    paintLog(d);
    paintQuick(d);
    paintPanel(d);
    paintStatus(d);
    paintComposer(d);
    schedule();
    maybeStart(d);
    maybeStartTodos(d);
  }

  function fail(r) {
    localErr = r.message;
    say(r.message, true);
    busy = false;
    if (data) paintComposer(data);
    if (r.status === 404 || r.status === 401) setTimeout(function () { location.reload(); }, 1500);
    else load();
  }

  function load() {
    clearTimeout(timer);
    fetch(base + '/roles', { credentials: 'same-origin' })
      .then(function (res) { return res.ok ? res.json() : Promise.reject(res.status); })
      .then(function (d) { paint(d); }, function (status) {
        if (status === 401 || status === 404) { location.reload(); return; }
        say('화면을 불러오지 못했어요. 잠시 후 다시 시도해주세요.', true);
        timer = setTimeout(load, 4000);
      });
  }

  // ---- actions ----
  function after(r) {
    busy = false;
    if (r.ok) { localErr = null; say(''); paint(r.data); } else fail(r);
  }
  function retryCandidates(c) {
    if (busy) return;
    busy = true;
    MoimAuth.post(base + '/roles/candidates/retry', {
      action_id: newId(), candidate_revision: c.candidate_revision, expected_attempt: c.attempt
    }).then(after);
  }
  function retryTurn(f) {
    if (busy) return;
    busy = true;
    MoimAuth.post(base + '/role-interview/retry', { action_id: newId(), seq: f.seq, expected_attempt: f.attempt }).then(after);
  }
  // A resend of the same answer after a network failure reuses its action id, so it is never stored twice.
  function postAnswer(key, body) {
    if (busy) return;
    if (!lastAnswer || lastAnswer.key !== key) lastAnswer = { key: key, id: newId() };
    body.action_id = lastAnswer.id;
    busy = true;
    paintComposer(data);
    MoimAuth.post(base + '/role-interview/answer', body).then(function (r) {
      if (r.ok) { lastAnswer = null; if (body.message) input.value = ''; }
      else if (r.status === 422) lastAnswer = null;  // nothing was stored: the member types again
      after(r);
    });
  }
  function answerButton(qid, oid) {
    postAnswer('b:' + qid + ':' + oid, { question_id: qid, option_id: oid });
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!data || data.phase !== 'ROLE_TODOS' || !data.role || input.disabled) return;
    var text = input.value.trim();
    if (!text) return;
    if (data.role.todos && data.role.todos.state === 'READY') { postRevise(text); return; }
    var q = data.role.interview.question;
    postAnswer('t:' + (q ? q.question_id : '') + ':' + text, { message: text });
  });
  input.addEventListener('input', function () { if (data) paintComposer(data); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit')); }
  });
  retryCoverage.addEventListener('click', function () {
    if (busy || !data || !data.role) return;
    var cov = data.role.coverage;
    busy = true;
    MoimAuth.post(base + '/coverage/retry', {
      action_id: newId(), assignment_revision: cov.assignment_revision, expected_attempt: cov.attempt
    }).then(after);
  });
  submitRank.addEventListener('click', function () {
    if (busy || picks.length !== 3 || !data || !data.ranking_available) return;
    busy = true;
    submitRank.disabled = true;
    var key = picks.join(',') + '@' + data.candidates.candidate_revision;
    if (!lastRank || lastRank.key !== key) lastRank = { key: key, id: newId() };
    MoimAuth.post(base + '/preferences', {
      action_id: lastRank.id, candidate_revision: data.candidates.candidate_revision, ranks: picks.slice()
    }).then(function (r) {
      if (r.ok) { picks = []; lastRank = null; }
      after(r);
    });
  });

  load();
})();
