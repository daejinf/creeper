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
  var startId = null, lastAnswer = null, lastPainted = null, localErr = null;
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
    } else if (c.state === 'SUCCEEDED') {
      frag.appendChild(row(GREETING));
      if (d.phase === 'WAITING_ASSIGNMENT') frag.appendChild(row('순위를 제출했어요. 다른 팀원이 모두 제출하면 역할이 정해져요.'));
    } else if (c.state === 'FAILED') {
      frag.appendChild(row(c.error_message || '역할 후보를 만들지 못했어요. 다시 시도해주세요.', 'err'));
    } else {
      frag.appendChild(row('역할 후보를 만들고 있어요…'));
    }
    // repaint only when something changed, so polling does not replay the bubble animation
    var key = JSON.stringify([d.phase, d.role && d.role.interview, c.state]);
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
      var iv = d.role.interview;
      if (iv.failed) {
        quick.appendChild(chip('다시 시도', function () { retryTurn(iv.failed); }));
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
    if (roleOn) {
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
      var iv = d.role.interview, cov = d.role.coverage;
      return iv.busy || !iv.started || cov.state === 'PENDING' || cov.state === 'RUNNING';
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
    if (!startId) startId = newId();  // one action id per page: a resend can never open two first questions
    busy = true;
    MoimAuth.post(base + '/role-interview/start', { action_id: startId }).then(after);
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
