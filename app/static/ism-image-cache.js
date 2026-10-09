(function () {
  'use strict';

  let current = null;
  let heartbeatTimer = null;
  let opening = false;

  function postJson(url, payload, keepalive) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      keepalive: !!keepalive,
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify(payload || {})
    });
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function release() {
    stopHeartbeat();
    const lease = current;
    current = null;
    if (!lease || !lease.pageId) return;
    const url = '/image-cache/release/' + encodeURIComponent(lease.pageId);
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([''], {type: 'text/plain'}));
        return;
      }
    } catch (_) {}
    try { postJson(url, {}, true).catch(function () {}); } catch (_) {}
  }

  async function openLease(scope, key) {
    if (opening) return;
    opening = true;
    try {
      const response = await postJson('/image-cache/open', {scope: scope, key: String(key)}, false);
      if (!response.ok) return;
      const data = await response.json();
      if (!data || !data.ok || !data.page_id) return;
      current = {scope: scope, key: String(key), pageId: data.page_id};
      stopHeartbeat();
      const every = Math.max(30, Number(data.heartbeat || 60)) * 1000;
      heartbeatTimer = setInterval(function () {
        if (!current || !current.pageId || document.visibilityState === 'hidden') return;
        postJson('/image-cache/heartbeat/' + encodeURIComponent(current.pageId), {}, false)
          .then(function (r) {
            if (r.status === 404 && current) {
              const old = current;
              current = null;
              stopHeartbeat();
              openLease(old.scope, old.key);
            }
          })
          .catch(function () {});
      }, every);
    } catch (_) {
      // Cache prefetch must never affect normal detail-page use.
    } finally {
      opening = false;
    }
  }

  function start(scope, key) {
    if (!scope || key === undefined || key === null || key === '') return;
    if (current) release();
    openLease(scope, key);
  }

  window.addEventListener('pagehide', release, {capture: true});
  window.addEventListener('pageshow', function (event) {
    if (event.persisted && !current && window.ISMImageCache && window.ISMImageCache._last) {
      const last = window.ISMImageCache._last;
      start(last.scope, last.key);
    }
  });

  window.ISMImageCache = {
    start: function (scope, key) {
      this._last = {scope: scope, key: String(key)};
      start(scope, key);
    },
    release: release,
    _last: null
  };
})();
