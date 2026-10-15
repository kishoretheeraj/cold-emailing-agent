// A module worker (no importScripts in module workers, so the probe is inlined).
const origin = self.location.origin;
try {
  const ws = new WebSocket(origin.replace('http', 'ws') + '/ws');
  ws.onopen = () => postMessage('ws-open');
} catch (e) { postMessage('ws-throw'); }
fetch(origin + '/post', {method: 'POST', body: 'pii', mode: 'no-cors'})
  .then(() => postMessage('post-ok'), () => postMessage('post-fail'));
