/* `그룹 나가기` in the original ⋯ group menu (stage 6, D-D1 D6, D-F1).
   The server first says what leaving does (preview); the browser's own confirm dialog shows it; only "확인" sends the
   leave with confirm: true. Cancelling changes nothing. Leaving cannot be undone. Plain strings only (no HTML). */
(function () {
  var btn = document.getElementById('leaveGroup');
  if (!btn) return;
  var gid = btn.getAttribute('data-group');
  var base = '/api/groups/' + encodeURIComponent(gid) + '/leave';
  var busy = false;

  function message(d) {
    var lines = ['‘' + d.group_name + '’ 그룹에서 나갈까요? 나가면 되돌릴 수 없어요.', ''];
    lines.push('· 이 그룹에서 나눈 내 AI 대화와 인터뷰 내용은 삭제돼요.');
    if (d.deletes_group) {
      lines.push('· 남는 팀원이 없어 그룹과 모든 기록·첨부 파일이 함께 삭제돼요.');
    } else {
      if (d.kept_records) lines.push('· 내 완료 기록 ' + d.kept_records + '개' + (d.kept_files ? '(첨부 ' + d.kept_files + '개)' : '') + '는 ‘나간 팀원’으로 팀에 남아요.');
      if (d.removed_todos) lines.push('· 끝내지 않은 내 할 일 ' + d.removed_todos + '개는 그룹 화면에서 빠져요.');
      if (d.successor_name) lines.push('· 그룹장은 ' + d.successor_name + '님에게 넘어가요.');
    }
    lines.push('· 나간 뒤에는 이 그룹 화면과 파일을 볼 수 없어요.');
    return lines.join('\n');
  }

  btn.addEventListener('click', function () {
    if (busy) return;
    busy = true;
    fetch(base, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }, function () { return { ok: false, data: null }; }); },
            function () { return { ok: false, data: null }; })
      .then(function (r) {
        if (!r.ok || !r.data || !r.data.impact) {
          busy = false;
          window.alert((r.data && r.data.error && r.data.error.message) || '잠시 후 다시 시도해주세요.');
          return;
        }
        if (!window.confirm(message(r.data.impact))) { busy = false; return; }   // 취소하면 변경 없음
        return MoimAuth.post(base, { confirm: true }).then(function (res) {
          if (res.ok) { location.assign('/dashboard'); return; }   // 나가기가 끝난 뒤에만 이동
          busy = false;
          window.alert(res.message);
        });
      });
  });
})();

/* `그룹 삭제` (D-H1): leader only. Same pattern as leaving: the server says what will be deleted, the browser's own
   confirm dialog shows it, and only "확인" deletes. Everything of the group goes for every member, permanently. */
(function () {
  var btn = document.getElementById('deleteGroup');
  if (!btn) return;
  var base = '/api/groups/' + encodeURIComponent(btn.getAttribute('data-group')) + '/delete';
  var busy = false;

  function message(d) {
    var lines = ['‘' + d.group_name + '’ 그룹을 삭제할까요? 삭제하면 되돌릴 수 없어요.', ''];
    lines.push('· 팀원 ' + d.members + '명 모두 이 그룹에 더 이상 들어올 수 없어요.');
    lines.push('· 모든 팀원의 AI 대화·역할·할 일과 완료 기록 ' + d.records + '개' + (d.files ? '(첨부 ' + d.files + '개)' : '') + '가 함께 삭제돼요.');
    return lines.join('\n');
  }

  btn.addEventListener('click', function () {
    if (busy) return;
    busy = true;
    fetch(base, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }, function () { return { ok: false, data: null }; }); },
            function () { return { ok: false, data: null }; })
      .then(function (r) {
        if (!r.ok || !r.data || !r.data.impact) {
          busy = false;
          window.alert((r.data && r.data.error && r.data.error.message) || '잠시 후 다시 시도해주세요.');
          return;
        }
        if (!window.confirm(message(r.data.impact))) { busy = false; return; }   // 취소하면 변경 없음
        return MoimAuth.post(base, { confirm: true }).then(function (res) {
          if (res.ok) { location.assign('/dashboard'); return; }
          busy = false;
          window.alert(res.message);
        });
      });
  });
})();
