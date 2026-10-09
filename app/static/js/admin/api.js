// Lightweight admin API client attached to window.adminAPI
(function () {
  var DEFAULT_TIMEOUT = 15000;

  function _fetchJson(url, opts) {
    opts = opts || {};
    var signal = opts.signal || null;
    var timeout = typeof opts.timeout === 'number' ? opts.timeout : DEFAULT_TIMEOUT;

    var ac = new AbortController();
    var timer = setTimeout(function () { ac.abort(); }, timeout);
    var mergedSignal = ac.signal;
    if (signal) {
      // prefer provided signal; if it aborts, we still clear timeout
      signal.addEventListener('abort', function () { ac.abort(); });
    }

    var fetchOpts = Object.assign({}, opts, { signal: mergedSignal });
    return fetch(url, fetchOpts).then(function (resp) {
      clearTimeout(timer);
      if (resp.status === 401) {
        var err = new Error('Authentication required');
        err.status = 401;
        throw err;
      }
      return resp.text().then(function (text) {
        try {
          var json = text ? JSON.parse(text) : {};
          if (!resp.ok) {
            var e = new Error(json && (json.error || json.message) ? (json.error || json.message) : 'Request failed');
            e.response = resp;
            e.body = json;
            throw e;
          }
          return json;
        } catch (e) {
          if (e instanceof SyntaxError) {
            var se = new Error('Invalid JSON response');
            se.response = resp;
            throw se;
          }
          throw e;
        }
      });
    }).catch(function (err) {
      if (err.name === 'AbortError') {
        var ae = new Error('Request timed out');
        ae.name = 'TimeoutError';
        throw ae;
      }
      throw err;
    });
  }

  function fetchList(listUrl, params) {
    var qs = params ? ('?' + new URLSearchParams(params).toString()) : '';
    return _fetchJson(listUrl + qs, { method: 'GET' }).catch(function (err) {
      // normalize error into an object so callers can display inline messages
      return { success: false, status: err.status || 500, message: err.message || 'Request failed', body: err.body || null };
    });
  }

  function saveRows(saveUrl, payload) {
    return _fetchJson(saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      timeout: 60000
    }).catch(function (err) {
      return { success: false, status: err.status || 500, message: err.message || 'Request failed', body: err.body || null };
    });
  }

  function archiveDelivered(archiveUrl, payload) {
    return _fetchJson(archiveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      timeout: 60000
    }).catch(function (err) {
      return { success: false, status: err.status || 500, message: err.message || 'Request failed', body: err.body || null };
    });
  }

  function deletePod(rowId) {
    var url = '/admin/consignments/' + encodeURIComponent(rowId) + '/pod';
    return _fetchJson(url, { method: 'DELETE', headers: { 'Accept': 'application/json' } }).catch(function (err) {
      return { success: false, status: err.status || 500, message: err.message || 'Request failed', body: err.body || null };
    });
  }

  // Expose
  window.adminAPI = {
    fetchList: fetchList,
    saveRows: saveRows,
    archiveDelivered: archiveDelivered,
    deletePod: deletePod
  };
})();
