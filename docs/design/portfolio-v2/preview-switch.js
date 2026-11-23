// 미리보기 전용: 주소 해시로 상태 전환 (구현본에는 들어가지 않음)
function show() {
  var all = document.querySelectorAll('section[data-state]');
  var s = location.hash.slice(1) || all[0].getAttribute('data-state');
  all.forEach(function (el) { el.hidden = el.getAttribute('data-state') !== s; });
}
window.addEventListener('hashchange', show);
show();
