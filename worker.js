// Runs the Python engine in the browser (Pyodide) off the main thread, so reading a big
// file never freezes the screen. Only used for the online demo; the shop PC uses server.py.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";
importScripts(PYODIDE + "pyodide.js");

const APP_FILES = ["api.py", "estimator.py", "step_reader.py", "quote_doc.py", "rates.xlsx",
  "samples/l-bracket-flat.dxf", "samples/flange-print.pdf", "samples/mounting-plate-3d.step", "samples/l-bracket-bent-3d.step"];

let ready = null;

async function boot(version) {
  postMessage({ status: "Loading Python" });
  const py = await loadPyodide({ indexURL: PYODIDE });
  postMessage({ status: "Loading the DXF, PDF and quote libraries" });
  await py.loadPackage(["micropip", "ssl"]);  // fpdf2 imports urllib.request, which needs ssl
  const micropip = py.pyimport("micropip");
  await micropip.install(["ezdxf==1.4.5", "pymupdf", "fpdf2==2.8.9", "openpyxl"]);
  postMessage({ status: "Loading the estimator" });
  py.FS.mkdirTree("/home/pyodide/app/samples");
  await Promise.all(APP_FILES.map(async (f) => {
    const res = await fetch(`app/${f}?v=${version}`);
    if (!res.ok) throw new Error(`couldn't fetch ${f}`);
    py.FS.writeFile(`/home/pyodide/app/${f}`, new Uint8Array(await res.arrayBuffer()));
  }));
  py.runPython("import sys, json; sys.path.insert(0, '/home/pyodide/app'); import api");
  return py;
}

onmessage = async (e) => {
  const { id, name, args, version } = e.data;
  try {
    if (!ready) ready = boot(version);
    const py = await ready;
    if (name === "__boot") return postMessage({ id, result: true });
    py.globals.set("__name", name);
    py.globals.set("__args", JSON.stringify(args || {}));
    const out = py.runPython("json.dumps(getattr(api, __name)(**json.loads(__args)))");
    postMessage({ id, result: JSON.parse(out) });
  } catch (err) {
    postMessage({ id, error: String(err && err.message || err) });
  }
};
