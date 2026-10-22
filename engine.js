// One way to call the engine whichever way the estimator is running: the local server on
// the shop PC (server.py) or Python in a background worker for the online demo.
const VERSION = document.documentElement.dataset.version || "dev";
let mode = null;
let worker = null;
let seq = 0;
const waiting = new Map();

export async function connect(onStatus) {
  // server.py marks the page when it serves it on the shop PC; anywhere else the engine runs in the browser
  if (document.documentElement.dataset.mode === "desktop") {
    mode = "desktop";
    return mode;
  }
  mode = "browser";
  worker = new Worker(`worker.js?v=${VERSION}`);
  worker.onmessage = (e) => {
    if (e.data.status) return onStatus && onStatus(e.data.status);
    const job = waiting.get(e.data.id);
    if (!job) return;
    waiting.delete(e.data.id);
    e.data.error ? job.reject(new Error(e.data.error)) : job.resolve(e.data.result);
  };
  await call("__boot");
  return mode;
}

export function call(name, args = {}) {
  if (mode === "desktop") return post(name, args, 3);
  return new Promise((resolve, reject) => {
    const id = ++seq;
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, name, args, version: VERSION });
  });
}

// a dropped local connection (antivirus scanning loopback traffic, a busy PC) is retried
// before the screen reports a problem; every call is safe to repeat
async function post(name, args, tries) {
  for (let attempt = 1; ; attempt++) {
    try {
      const r = await fetch(`/api/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
      const body = await r.json();
      if (!r.ok || body.error) throw Object.assign(new Error(body.error || `${name} failed`), { fromServer: true });
      return body.result;
    } catch (err) {
      if (err.fromServer || attempt >= tries) throw err;
      await new Promise((res) => setTimeout(res, 300 * attempt));
    }
  }
}
