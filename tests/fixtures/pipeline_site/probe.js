// Shared by the test pages and workers: tries to open a WebSocket and send a POST to the page's own
// server, and reports what happened. The server counts what actually reached it; that count, not
// these reports, is what the tests assert (a routed socket looks "open" to the page but connects to
// nothing). Written for tests/test_job_context_browser.py; not copied from any site.
function probe(origin, report) {
  try {
    const ws = new WebSocket(origin.replace('http', 'ws') + '/ws');
    ws.onopen = () => report('ws-open');
    ws.onclose = () => report('ws-close');
  } catch (e) { report('ws-throw'); }
  try {
    fetch(origin + '/post', {method: 'POST', body: 'pii', mode: 'no-cors'})
      .then(() => report('post-ok'), () => report('post-fail'));
  } catch (e) { report('post-throw'); }
}
