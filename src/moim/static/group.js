/* Group header: the team goal sentence. It shows the state of the CURRENT ProjectBrief revision only:
   a generating notice, the sentence, an empty-content notice, or an error with a retry for the leader. */
(function () {
  var el = document.getElementById('goal');
  if (!el) return;
  var gid = el.getAttribute('data-group');
  var text = document.getElementById('goalText');
  var retry = document.getElementById('goalRetry');
  var state = el.getAttribute('data-state');
  var rev = Number(el.getAttribute('data-revision'));
  var timer = null;

  var COPY = {
    PENDING: '팀 목표를 정리하고 있어요…',
    RUNNING: '팀 목표를 정리하고 있어요…',
    EMPTY: '프로젝트 내용을 입력하면 팀 목표가 여기에 표시돼요.'
  };

  function schedule() {
    clearTimeout(timer);
    if (state === 'PENDING' || state === 'RUNNING') timer = setTimeout(poll, 2500);
  }

  function paint(v) {
    state = v.state;
    rev = v.brief_revision;
    el.setAttribute('data-state', state);
    el.setAttribute('data-revision', String(rev));
    if (state === 'SUCCEEDED') {
      text.textContent = v.text;
      text.classList.remove('quiet');
    } else {
      text.classList.add('quiet');
      text.textContent = COPY[state] || v.error_message || '팀 목표를 만들지 못했어요. 다시 시도해주세요.';
    }
    retry.hidden = !v.can_retry;
    retry.disabled = false;
    schedule();
  }

  function poll() {
    fetch('/api/groups/' + encodeURIComponent(gid) + '/goal', { credentials: 'same-origin' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (v) { if (v) paint(v); else schedule(); }, schedule);
  }

  retry.addEventListener('click', function () {
    if (retry.disabled) return;
    retry.disabled = true;
    MoimAuth.post('/api/groups/' + encodeURIComponent(gid) + '/goal/retry', { brief_revision: rev }).then(function (r) {
      if (r.ok) { paint(r.data); return; }
      text.textContent = r.message;
      retry.disabled = false;
    });
  });

  if (state === 'FAILED') poll(); // learn whether this viewer may retry
  else schedule();
})();
