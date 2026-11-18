(function () {
  var btn = document.getElementById('logout');
  var msg = document.getElementById('logout-msg');
  btn.addEventListener('click', function () {
    if (btn.disabled) return;
    btn.disabled = true;
    msg.textContent = '';
    MoimAuth.post('/api/auth/logout', {}).then(function (r) {
      if (r.ok) { MoimAuth.go(r.data.redirect); return; }
      msg.textContent = r.message;
      btn.disabled = false;
    });
  });
})();
