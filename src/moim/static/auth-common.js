/* Shared by the auth pages. Sends JSON with the CSRF token from the page's meta tag. Stores nothing in the browser. */
(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta ? meta.content : '' },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().then(function (d) { return d; }, function () { return null; }).then(function (data) {
        if (res.ok) return { ok: true, status: res.status, data: data };
        var e = (data && data.error) || {};
        return { ok: false, status: res.status, code: e.code || 'ERROR', field: e.field || null,
                 message: e.message || '잠시 후 다시 시도해주세요.' };
      });
    }, function () {
      return { ok: false, status: 0, code: 'NETWORK', field: null, message: '네트워크에 연결할 수 없어요. 잠시 후 다시 시도해주세요.' };
    });
  }
  function go(url) {
    var ok = typeof url === 'string' && url.charAt(0) === '/' && url.charAt(1) !== '/' && url.indexOf('\\') < 0;
    location.assign(ok ? url : '/dashboard');
  }
  window.MoimAuth = { post: post, go: go };
})();
