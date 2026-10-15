// A dedicated worker: the vector that context.route_web_socket does not reach.
importScripts('/probe.js');
probe(self.location.origin, (e) => postMessage(e));
