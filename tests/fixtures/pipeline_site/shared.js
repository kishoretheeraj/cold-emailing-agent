// A shared worker: neither context.route nor context.route_web_socket reaches it.
importScripts('/probe.js');
onconnect = (c) => { const port = c.ports[0]; probe(self.location.origin, (e) => port.postMessage(e)); };
