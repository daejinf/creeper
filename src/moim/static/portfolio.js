/* Portfolio tab (stage 8D, D-I1 · D-I2; RAG_DESIGN 3.6, 10).
   - 개요: AI가 쓴 소개 문장·근거 역량·프로젝트 목록·전체 질문·이전 기록 가져오기.
   - 프로젝트 상세: 맡은 일·만든 결과·배운 점(문장마다 출처), 근거 자료 목록(삭제·다시 시도), 프로젝트 질문.
   - 답변: 문장마다 근거 번호, 서버가 자른 근거 원문. 근거가 없으면 지어내지 않고 거절해요.
   Only existing mypage markup and classes are used. Every user and AI string goes in with textContent; nothing is
   turned into a link. Nothing is stored in the browser; questions and answers live only on this page. */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var panel = $('tab-pf');
  if (!panel) return;
  var titleEl = $('pfTitle'), subEl = $('pfSub');
  var SVGNS = 'http://www.w3.org/2000/svg';
  var SUB = '투두에 첨부한 자료만 근거로 AI가 정리했어요. 모든 문장에 출처가 붙어요.';
  var MAX_Q = 300;
  var POLL_MS = 3000;
  var GROUPS = [['did', '맡은 일'], ['made', '만든 결과'], ['learned', '배운 점']];

  var view = null;          // {kind:'overview'} | {kind:'project', id}
  var shapes = {};          // project id -> dot shape from the overview
  var loaded = false, visible = false, pollT = 0, seq = 0;

  /* ---------- 요청 ---------- */
  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ''; }
  function call(method, url, body) {
    var opt = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') {
      opt.headers['Content-Type'] = 'application/json';
      opt.headers['X-CSRF-Token'] = csrf();   // the token may have been renewed by a password change
      opt.body = JSON.stringify(body || {});
    }
    return fetch(url, opt).then(function (res) {
      return res.json().then(function (d) { return d; }, function () { return null; }).then(function (data) {
        if (res.ok) return { ok: true, status: res.status, data: data };
        var e = (data && data.error) || {};
        if (res.status === 401) location.assign('/login?next=/mypage');
        return { ok: false, status: res.status, code: e.code || 'ERROR', message: e.message || '잠시 후 다시 시도해주세요.' };
      });
    }, function () {
      return { ok: false, status: 0, code: 'NETWORK', message: '네트워크에 연결할 수 없어요. 잠시 후 다시 시도해주세요.' };
    });
  }
  function actionId() {
    var a = new Uint8Array(18), s = '', chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    crypto.getRandomValues(a);
    for (var i = 0; i < a.length; i++) s += chars.charAt(a[i] & 63);
    return s;
  }
  var toastEl = $('toast'), toastT;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.classList.remove('on'); }, 2000);
  }

  /* ---------- 마크업 조각 (모두 기존 클래스) ---------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function gap(n) { n.setAttribute('style', 'margin-top:12px'); return n; }
  function button(cls, text, onClick, disabled) {
    var b = el('button', cls, text);
    b.type = 'button';
    b.disabled = !!disabled;
    if (onClick) b.addEventListener('click', function () { onClick(b); });
    return b;
  }
  function check() {
    var s = document.createElementNS(SVGNS, 'svg');
    [['width', '11'], ['height', '11'], ['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'],
     ['stroke-width', '3.4'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]
      .forEach(function (a) { s.setAttribute(a[0], a[1]); });
    var p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', 'M5 12.5l4.5 4.5L19 7.5'); s.appendChild(p);
    return s;
  }
  function rules(texts) {
    var ul = el('ul', 'rules');
    texts.forEach(function (t) { var li = el('li', 'ok'); li.appendChild(check()); li.appendChild(document.createTextNode(t)); ul.appendChild(li); });
    return ul;
  }
  // 줄: 왼쪽 b + small들, 오른쪽에 버튼이나 배지 하나
  function row(title, smalls, right) {
    var r = el('div', 'row'), left = el('div');
    left.appendChild(el('b', null, title));
    (smalls || []).forEach(function (s) { left.appendChild(typeof s === 'string' ? el('small', null, s) : s); });
    return add(r, left, right);
  }
  // 문장 한 줄 + 끝에 출처 (span.dd, .row 안이라 <b> 없음)
  function sentence(text, tags) {
    var sm = el('small', null, text);
    (tags || []).forEach(function (t) { sm.appendChild(document.createTextNode(' ')); sm.appendChild(el('span', 'dd', t)); });
    return sm;
  }
  function labels(citations) {
    var seen = {}, out = [];
    (citations || []).forEach(function (c) { if (!seen[c.label]) { seen[c.label] = 1; out.push(c.label); } });
    return out;
  }
  function oneLine(text) { return add(el('ul', 'groups'), add(el('li', 'grp'), add(el('div', 't'), el('b', null, text)))); }

  function clear() {
    clearTimeout(pollT);
    while (subEl.nextSibling) panel.removeChild(subEl.nextSibling);
  }
  function head(title, sub) { titleEl.textContent = title; subEl.textContent = sub; }

  function monthDay(iso) {
    try { return new Date(iso).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric' }); }
    catch (e) { return ''; }
  }
  function period(p) {
    if (!p || !p[0]) return '';
    var a = p[0].split('-'), b = (p[1] || p[0]).split('-');
    var am = +a[1], bm = +b[1];
    if (a[0] === b[0]) return a[0] + '년 ' + am + '월' + (am === bm ? '' : ' – ' + bm + '월');
    return a[0] + '년 ' + am + '월 – ' + b[0] + '년 ' + bm + '월';
  }
  function projectSub(d) {
    return [d.role, period(d.period), '완료 투두 ' + d.done + '개'].filter(Boolean).join(' · ');
  }

  /* ---------- 불러오기와 상태 ---------- */
  function load(v, keepInput) {
    view = v;
    var mine = ++seq;
    var url = v.kind === 'project' ? '/api/portfolio/projects/' + encodeURIComponent(v.id) : '/api/portfolio';
    var kept = keepInput ? keepQuestion() : null;
    return call('GET', url).then(function (r) {
      if (mine !== seq) return;                   // a newer view was asked for meanwhile
      if (!r.ok) {
        if (v.kind === 'project' && r.status === 404) { toast('찾을 수 없어요.'); load({ kind: 'overview' }); return; }
        renderError(v);
        return;
      }
      loaded = true;
      var running = v.kind === 'project' ? renderProject(r.data) : renderOverview(r.data);
      if (kept) restoreQuestion(kept);
      if (running) pollT = setTimeout(function () { if (visible && mine === seq) load(v, true); }, POLL_MS);
    });
  }
  function keepQuestion() {
    var q = $('pfQ');
    return q ? { value: q.value, focus: document.activeElement === q } : null;
  }
  function restoreQuestion(k) {
    var q = $('pfQ');
    if (!q) return;
    q.value = k.value;
    q.dispatchEvent(new Event('input'));
    if (k.focus) q.focus();
  }

  function renderError(v) {
    clear();
    head('포트폴리오', SUB);
    var f = el('div', 'field');
    f.appendChild(el('p', 'hint err', '포트폴리오를 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.'));
    panel.appendChild(f);
    panel.appendChild(add(el('div', 'actions'), button('btn ghost', '다시 시도', function () { load(v); })));
  }

  /* ---------- AI 정리 줄 (만들기 / 다시 만들기) ---------- */
  function summaryRow(sum, readyCount, doneText, start) {
    var has = sum && sum.has_content, state = sum ? sum.state : null, text, label = has ? '다시 만들기' : '만들기';
    var disabled = false;
    if (state === 'RUNNING') { text = has ? '자료를 다시 읽고 정리하는 중이에요…' : '자료를 읽고 정리하는 중이에요…'; disabled = true; }
    else if (!readyCount) { text = has ? '정리된 자료가 없어 다시 만들 수 없어요.' : '자료 정리가 끝나면 만들 수 있어요.'; disabled = true; }
    else if (state === 'FAILED' && has) text = '다시 만들지 못했어요. 이전 정리를 보여 드려요.';
    else if (state === 'FAILED') text = '정리하지 못했어요. 다시 시도해 주세요.';
    else if (has && sum.stale) text = '새 자료가 아직 반영되지 않았어요. 다시 만들면 반영돼요.';
    else if (has) text = doneText;
    else text = '정리된 자료 ' + readyCount + '개로 출처가 붙은 정리를 만들 수 있어요.';
    return row('AI 정리', [text], button('btn ghost', label, function (b) {
      b.disabled = true;
      call('POST', start, { action_id: actionId() }).then(function (r) {
        if (!r.ok) { b.disabled = false; toast(r.message); return; }
        toast('정리를 시작했어요');
        load(view, true);
      });
    }, disabled));
  }

  /* ---------- 질문 칸 ---------- */
  function questionField(label, placeholder, guide, value) {
    var f = el('div', 'field');
    var lb = el('label', 'flabel', label); lb.htmlFor = 'pfQ';
    var box = el('div', 'box'), inp = el('input');
    box.id = 'pfQBox';
    inp.id = 'pfQ'; inp.type = 'text'; inp.maxLength = MAX_Q; inp.placeholder = placeholder; inp.autocomplete = 'off';
    if (value) inp.value = value;
    var hint = el('p', 'hint'); hint.id = 'pfQHint';
    function count() { hint.classList.remove('err'); box.classList.remove('bad'); hint.textContent = guide || inp.value.length + '/' + MAX_Q; }
    inp.addEventListener('input', count);
    count();
    box.appendChild(inp);
    return add(f, lb, box, hint);
  }
  function questionHint(msg, bad) {
    var h = $('pfQHint'), box = $('pfQBox');
    if (!h) return;
    h.textContent = msg;
    h.classList.toggle('err', !!bad);
    box.classList.toggle('bad', !!bad);
  }
  function askActions(scope, suggestions, extra) {
    var a = gap(el('div', 'actions'));
    var go = button('btn sm', '질문하기', function () { ask(scope, $('pfQ').value, go); });
    a.appendChild(go);
    (suggestions || []).forEach(function (q) { a.appendChild(button('btn sm ghost', q, function (b) { $('pfQ').value = q; ask(scope, q, b); })); });
    (extra || []).forEach(function (b) { a.appendChild(b); });
    return a;
  }
  function bindEnter(scope) {
    $('pfQ').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); ask(scope, $('pfQ').value, null); }
    });
  }

  /* ---------- 개요 ---------- */
  function renderOverview(d) {
    clear();
    head('포트폴리오', SUB);
    (d.projects || []).forEach(function (p) { shapes[p.id] = p.shape; });
    var ps = d.portfolio_summary;
    var running = (d.projects || []).some(function (p) { return p.running > 0 || (p.summary && p.summary.state === 'RUNNING'); }) ||
      !!(ps && ps.state === 'RUNNING');

    if (!d.projects || !d.projects.length) {
      panel.appendChild(oneLine('아직 포트폴리오에 담긴 첨부가 없어요.'));
      panel.appendChild(gap(importRow(d.import, true)));
      panel.appendChild(row('앞으로 쌓이는 방식', ['투두를 완료할 때 자료를 첨부하면, AI가 읽고 정리해 여기에 자동으로 담아요.']));
      return running;
    }

    var bc = (ps && ps.basis_counts) || {};
    panel.appendChild(summaryRow(ps, d.ready_items,
      '프로젝트 ' + (bc.projects || 0) + '개, 자료 ' + (bc.items || 0) + '개를 읽고 ' + monthDay(ps && ps.generated_at) + '에 정리했어요.',
      '/api/portfolio/summary'));
    if (ps && ps.intro && ps.intro.length) {
      panel.appendChild(row('나를 소개하는 세 문장', ps.intro.map(function (e) { return sentence(e.text, labels(e.citations)); })));
    }
    if (ps && ps.skills && ps.skills.length) {
      panel.appendChild(el('span', 'flabel', '근거로 확인된 역량'));
      panel.appendChild(rules(ps.skills.map(function (k) { return k.name + ' · 근거 ' + k.documents; })));
    }

    panel.appendChild(gap(el('span', 'flabel', '프로젝트')));
    var ul = el('ul', 'groups');
    d.projects.forEach(function (p) { ul.appendChild(projectLine(p)); });
    panel.appendChild(ul);

    panel.appendChild(gap(questionField('내 포트폴리오 전체에 질문하기', '예: 데이터로 결정을 바꾼 경험이 있나요?',
      d.projects.length + '개 프로젝트의 자료를 모두 찾아보고, 찾은 곳을 함께 보여 드려요.')));
    bindEnter(null);
    panel.appendChild(askActions(null, d.suggestions));
    if (d.import && d.import.records) panel.appendChild(gap(importRow(d.import, false)));
    return running;
  }

  function projectLine(p) {
    var li = el('li', 'grp');
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.setAttribute('aria-label', p.name + ' 자세히 보기');
    li.appendChild(el('span', 'dot ' + (p.shape || 'c')));
    li.lastChild.setAttribute('aria-hidden', 'true');
    var t = el('div', 't'), prog = el('div', 'prog'), bar = el('div', 'bar'), fill = el('span');
    var pct = p.items ? Math.round(p.ready / p.items * 100) : 0;
    fill.setAttribute('style', 'width:' + pct + '%');
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', '첨부 ' + p.items + '개 중 ' + p.ready + '개 정리 완료');
    bar.appendChild(fill);
    var cnt = el('span', 'cnt', '자료 ');
    cnt.appendChild(el('b', null, p.ready + '/' + p.items));
    cnt.appendChild(document.createTextNode(' 정리됨'));
    add(prog, bar, cnt);
    if (p.running) prog.appendChild(el('span', 'hot', '정리 중'));
    if (p.summary && p.summary.state === 'RUNNING') prog.appendChild(el('span', 'hot', '요약 만드는 중'));
    else if (p.summary && p.summary.stale) prog.appendChild(el('span', 'hot', '요약 오래됨'));
    if (p.failed) { var h = el('span', 'hot', '실패 '); h.appendChild(el('b', null, String(p.failed))); prog.appendChild(h); }
    add(t, el('b', null, p.name), prog);
    li.appendChild(t);
    if (p.places) li.appendChild(el('span', 'meta', '근거 ' + p.places + '곳'));
    if (p.role) { var dd = el('span', 'dd', '역할 '); dd.appendChild(el('b', null, p.role)); li.appendChild(dd); }
    function open() { load({ kind: 'project', id: p.id }); panel.scrollIntoView({ block: 'start' }); }
    li.addEventListener('click', open);
    li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    return li;
  }

  function importRow(imp, empty) {
    imp = imp || { records: 0, allowed: false };
    if (!imp.records) return row('이전 기록 가져오기', [empty ? '가져올 수 있는 이전 완료 기록이 없어요.' : '']);
    if (!imp.allowed) {
      return row('이전 기록 가져오기', ['지난 완료 기록 ' + imp.records + '건은 한 번에 가져오기에는 많아요. 관리자 확인 뒤에 가져올 수 있어요.'],
        button('btn ghost', '가져오기', null, true));
    }
    return row('이전 기록 가져오기', ['첨부가 남아 있는 지난 완료 기록 ' + imp.records + '건을 포트폴리오에 담을 수 있어요.'],
      button('btn ghost', '가져오기', function (b) {
        b.disabled = true;
        call('POST', '/api/portfolio/import', { action_id: actionId() }).then(function (r) {
          if (!r.ok) { b.disabled = false; toast(r.message); return; }
          toast('이전 기록 ' + r.data.queued + '건을 정리하기 시작했어요');
          load({ kind: 'overview' }, true);
        });
      }));
  }

  /* ---------- 프로젝트 상세 ---------- */
  function renderProject(d) {
    clear();
    head(d.name, projectSub(d));
    view.data = d;
    var sum = d.summary, ready = d.items.filter(function (i) { return i.state === 'READY'; }).length;
    var running = d.items.some(function (i) { return i.state === 'RUNNING'; }) || !!(sum && sum.state === 'RUNNING');
    panel.appendChild(summaryRow(sum, ready,
      '자료 ' + ready + '개를 읽고 ' + monthDay(sum && sum.generated_at) + '에 정리했어요.',
      '/api/portfolio/projects/' + encodeURIComponent(d.id) + '/summary'));
    if (sum && sum.has_content) {
      GROUPS.forEach(function (g) {
        var list = sum[g[0]] || [];
        if (list.length) panel.appendChild(row(g[1], list.map(function (e) { return sentence(e.text, labels(e.citations)); })));
      });
    }

    panel.appendChild(gap(el('span', 'flabel', '근거 자료 ' + d.items.length + '개')));
    var ul = el('ul', 'groups');
    d.items.forEach(function (i) { ul.appendChild(itemLine(i, shapes[d.id] || 'c')); });
    if (!d.items.length) ul.appendChild(add(el('li', 'grp'), add(el('div', 't'), el('b', null, '이 프로젝트에 남은 자료가 없어요.'))));
    panel.appendChild(ul);

    panel.appendChild(gap(questionField('이 프로젝트에 질문하기', '예: 사용성 테스트에서 무엇을 고쳤나요?', null)));
    bindEnter(d.id);
    panel.appendChild(askActions(d.id, d.suggestions));
    panel.appendChild(add(el('div', 'actions'),
      button('btn ghost', '목록으로', function () { load({ kind: 'overview' }); }),
      button('btn ghost', '프로젝트 지우기', function (b) {
        if (!window.confirm('‘' + d.name + '’ 프로젝트와 그 자료 정리를 포트폴리오에서 지울까요? 되돌릴 수 없어요.')) return;
        b.disabled = true;
        call('DELETE', '/api/portfolio/projects/' + encodeURIComponent(d.id)).then(function (r) {
          if (!r.ok) { b.disabled = false; toast(r.message); return; }
          toast('프로젝트를 지웠어요');
          load({ kind: 'overview' });
        });
      })));
    return running;
  }

  function itemLine(i, shape) {
    var li = el('li', 'grp');
    li.appendChild(el('span', 'dot ' + shape));
    li.lastChild.setAttribute('aria-hidden', 'true');
    var t = el('div', 't'), prog = el('div', 'prog');
    var files = (i.files || []).map(function (f) { return f.name + ' · ' + f.kind; }).join(', ');
    prog.appendChild(el('span', 'cnt', files || '첨부 없음'));
    if (i.state === 'RUNNING') prog.appendChild(el('span', 'hot', '정리 중'));
    if (i.state === 'FAILED') { var h = el('span', 'hot', '정리 '); h.appendChild(el('b', null, '실패')); prog.appendChild(h); }
    add(t, el('b', null, i.title), prog);
    li.appendChild(t);
    if (i.cited) li.appendChild(el('span', 'meta', '인용 ' + i.cited + '곳'));
    if (i.state === 'FAILED') {
      li.appendChild(button('btn sm ghost', '다시 시도', function (b) {
        b.disabled = true;
        call('POST', '/api/portfolio/jobs/' + encodeURIComponent(i.job_id) + '/retry', { expected_revision: i.revision }).then(function (r) {
          if (!r.ok) { b.disabled = false; toast(r.message); load(view, true); return; }
          toast('다시 정리하기 시작했어요');
          load(view, true);
        });
      }));
    } else {
      li.appendChild(button('btn sm ghost', '삭제', function (b) {
        if (!window.confirm('‘' + i.title + '’ 자료와 그 정리를 포트폴리오에서 지울까요? 되돌릴 수 없어요.')) return;
        b.disabled = true;
        call('DELETE', '/api/portfolio/items/' + encodeURIComponent(i.id)).then(function (r) {
          if (!r.ok) { b.disabled = false; toast(r.message); return; }
          toast('자료를 지웠어요');
          load(view, true);
        });
      }, i.state === 'RUNNING'));
    }
    return li;
  }

  /* ---------- 질문과 답변 ---------- */
  var asking = false;
  function ask(scope, question, btn) {
    var q = (question || '').trim();
    if (asking) return;
    if (!q || q.length > MAX_Q) { questionHint('질문은 1~300자로 입력해 주세요.', true); return; }
    asking = true;
    clearTimeout(pollT);
    var buttons = Array.prototype.slice.call(panel.querySelectorAll('button'));
    buttons.forEach(function (b) { b.disabled = true; });
    var go = btn || null, before = go ? go.textContent : '';
    if (go) go.textContent = '찾는 중…';
    questionHint('내 자료에서 찾는 중이에요…', false);
    var back = view && view.kind === 'answer' ? view.back : view;
    var body = { question: q };
    if (scope) body.project_id = scope;
    call('POST', '/api/portfolio/query', body).then(function (r) {
      asking = false;
      if (!r.ok) {
        buttons.forEach(function (b) { b.disabled = false; });
        if (go) go.textContent = before;
        questionHint(r.message, true);
        return;
      }
      renderAnswer(scope, back, q, r.data);
    });
  }

  function renderAnswer(scope, back, q, d) {
    clear();
    var proj = scope && back && back.data;
    if (proj) head(proj.name, projectSub(proj)); else head('포트폴리오', SUB);
    seq++;                                        // no poll comes back over the answer
    view = { kind: 'answer', back: back };        // switching tabs keeps the answer on screen
    panel.appendChild(questionField(proj ? '이 프로젝트에 질문하기' : '내 포트폴리오 전체에 질문하기',
      '질문을 입력해 주세요', null, q));
    bindEnter(scope);
    var go = gap(el('div', 'actions'));
    var askBtn = button('btn sm', '질문하기', function () { ask(scope, $('pfQ').value, askBtn); });
    go.appendChild(askBtn);
    panel.appendChild(go);

    function backBtn() {
      return button('btn ghost', proj ? '프로젝트로' : '목록으로', function () { load(proj ? { kind: 'project', id: scope } : { kind: 'overview' }); });
    }
    function followUps() {
      var a = gap(el('div', 'actions'));
      (d.suggestions || []).forEach(function (s) { a.appendChild(button('btn sm ghost', s, function (b) { $('pfQ').value = s; ask(scope, s, b); })); });
      a.appendChild(backBtn());
      return a;
    }

    if (!d.sentences || !d.sentences.length) {
      questionHint((d.message || '포트폴리오 기록에서 찾지 못했어요').replace(/[.。]?\s*$/, '.') +
        ' 첨부한 자료에 있는 내용만 답하고, 없는 내용은 지어내지 않아요.', false);
      if (d.available && d.available.length) {
        panel.appendChild(gap(row('대신 이런 질문에는 답할 수 있어요',
          [(proj ? '이 프로젝트' : '내 포트폴리오') + ' 자료에는 ' + d.available.map(function (t) { return '‘' + t + '’'; }).join(', ') + ' 기록이 있어요.'])));
      }
      panel.appendChild(followUps());
      return;
    }

    var lines = d.sentences.map(function (s) { return sentence(s.text, s.cites.map(function (n) { return '근거 ' + n; })); });
    if (d.missing) lines.push(el('small', null, '자료로 확인하지 못한 부분: ' + d.missing));
    panel.appendChild(gap(row('답변', lines)));
    var all = d.sentences.every(function (s) { return s.cites && s.cites.length; });
    panel.appendChild(rules([all ? d.sentences.length + '문장 모두 근거 확인' : '근거가 있는 문장만 보여 드려요', '내 자료 ' + d.files + '개에서 찾음']));

    panel.appendChild(gap(el('span', 'flabel', '근거 원문')));
    d.evidence.forEach(function (e) {
      panel.appendChild(row(e.n + ' · ' + e.label, e.excerpt ? ['“' + e.excerpt + '”'] : [], el('span', 'lock', e.kind)));
    });
    panel.appendChild(followUps());
  }

  /* ---------- 탭 열기·닫기 (mypage.js가 알려 줘요) ---------- */
  window.MoimPortfolio = {
    shown: function (on) {
      visible = on;
      if (!on) { clearTimeout(pollT); return; }
      if (!loaded) load({ kind: 'overview' });
      else if (view && view.kind !== 'answer') load(view, true);
    }
  };
})();
