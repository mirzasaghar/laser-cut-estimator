import { connect, call } from "./engine.js?v=99ba1d1f43";

const $ = (s) => document.querySelector(s);
const money = (v) => "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const mins = (m) => { const s = Math.round(m * 60); return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`; };

const BASES = ["Rectangle the part needs", "Full sheets used", "Net part area"];
const CHARGES = [
  ["shop_name", "Shop name on the quote", "text", "wide"],
  ["machine_rate_hr", "Machine rate ($/hr)", 5], ["motion_allowance_pct", "Head travel allowance (%)", 1],
  ["setup_per_job", "Setup per job ($)", 5], ["minimum_charge", "Minimum job charge ($)", 5],
  ["handling_per_part", "Handling per part ($)", 0.25], ["material_markup_pct", "Material markup (%)", 1],
  ["part_spacing_in", "Spacing between parts (in)", 0.05], ["material_basis", "Charge material by", "basis"],
  ["sheet_w_in", "Sheet width (in)", 12], ["sheet_h_in", "Sheet length (in)", 12],
  ["valid_days", "Quote valid for (days)", 5], ["lead_days", "Lead time (working days)", 1],
];

const state = {
  rates: [], settings: { material_basis: BASES[0], shop_name: "", sheet_margin_in: 0.5 },
  parts: [], selected: null, total: null, nextNo: 1, ready: false,
};

/* ---------- start-up ---------- */
async function start() {
  bindStatic();
  showView(location.hash.slice(1) || "work");
  try {
    await connect((msg) => { $("#boot-step").textContent = msg; });
    const [rates, qno] = await Promise.all([call("default_rates"), call("default_quote_no")]);
    state.rates = rates.rows;
    Object.assign(state.settings, rates.charges);
    $("#quote-no").value = qno;
    state.ready = true;
    $("#boot").classList.add("done");
    renderAll();
  } catch (err) {
    $("#boot").classList.add("error");
    $("#boot").innerHTML = `<div><b>The estimator couldn't start.</b><span>${esc(err.message)}. Reload the page to try again.</span></div>`;
  }
}

function bindStatic() {
  document.querySelectorAll("[data-view]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showView(a.dataset.view); }));
  window.addEventListener("hashchange", () => showView(location.hash.slice(1) || "work"));
  const input = $("#file-input");
  input.addEventListener("change", () => { addFiles([...input.files]); input.value = ""; });
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]); });
  $("#try-samples").addEventListener("click", trySamples);
  ["#customer", "#reference", "#quote-no"].forEach((s) => $(s).addEventListener("input", renderQuote));
  $("#download-pdf").addEventListener("click", downloadPdf);
  $("#download-pdf-2").addEventListener("click", downloadPdf);
  $("#download-csv").addEventListener("click", downloadCsv);
  $("#add-rate").addEventListener("click", () => { const last = state.rates[state.rates.length - 1] || {}; state.rates.push({ ...last, thickness: "", thickness_in: 0 }); renderRates(); });
  $("#rates-file").addEventListener("change", loadWorkbook);
}

function showView(v) {
  if (!["work", "quote", "rates", "help"].includes(v)) v = "work";
  for (const id of ["work", "quote", "rates", "help"]) $(`#view-${id}`).hidden = id !== v;
  document.querySelectorAll(".links a").forEach((a) => {
    if (a.dataset.view === v) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (location.hash.slice(1) !== v) history.replaceState(null, "", `#${v}`);
  if (v === "help") renderHelp();
}

/* ---------- parts ---------- */
async function fetchSample(name) {
  const r = await fetch(`app/samples/${name}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i += 8192) s += String.fromCharCode(...buf.subarray(i, i + 8192));
  return btoa(s);
}

const readB64 = (file) => new Promise((res, rej) => {
  const r = new FileReader();
  r.onload = () => res(r.result.split(",")[1]);
  r.onerror = () => rej(r.error);
  r.readAsDataURL(file);
});

async function addFiles(files) {
  if (!state.ready) return;
  const ok = files.filter((f) => /\.(dxf|pdf|step|stp)$/i.test(f.name));
  for (const f of ok) addPart(f.name, await readB64(f));
}

// the sample plate is quoted in stainless so the quote shows more than one material
const SAMPLE_MATERIAL = { "mounting-plate-3d.step": "Stainless 304" };

async function trySamples() {
  if (!state.ready) return;
  const names = await call("sample_files");
  names.forEach((n) => addPart(n, null, SAMPLE_MATERIAL[n]));
}

async function addPart(name, b64, material = null) {
  const no = state.nextNo++;
  const part = { id: `P${no}`, no, name, b64, qty: 10, units: "auto", pdfWidth: 0, material: null, thickness: null, geo: null, est: null };
  state.parts.push(part);
  if (!state.selected) state.selected = part.id;
  renderParts();
  await analyze(part);
  const mats = materials();
  part.material = mats.includes(material) ? material : mats[0];
  part.thickness = defaultThickness(part);
  if (state.selected === part.id) renderSelected();
  await reprice();
}

async function analyze(part) {
  if (!part.b64 && part.units === "auto" && !part.pdfWidth) {
    part.geo = await call("analyze_sample", { pid: part.id, name: part.name });
    return;
  }
  if (!part.b64) part.b64 = await fetchSample(part.name);
  part.geo = await call("analyze", { pid: part.id, name: part.name, b64: part.b64, units: part.units, pdf_width: part.pdfWidth || null });
}

function materials() { return [...new Set(state.rates.map((r) => r.material))]; }
function thicknesses(mat) { return state.rates.filter((r) => r.material === mat); }
function defaultThickness(part) {
  const opts = thicknesses(part.material);
  if (!opts.length) return null;
  if (part.geo && part.geo.model_thickness) {
    return opts.reduce((a, b) => (Math.abs(b.thickness_in - part.geo.model_thickness) < Math.abs(a.thickness_in - part.geo.model_thickness) ? b : a)).thickness;
  }
  return opts[Math.min(3, opts.length - 1)].thickness;
}

async function reprice() {
  const ready = state.parts.filter((p) => p.geo);
  const res = await call("price", {
    job: { parts: ready.map((p) => ({ id: p.id, material: p.material, thickness: p.thickness, qty: p.qty })), rates: state.rates, settings: state.settings },
  });
  for (const r of res.parts) { const p = state.parts.find((x) => x.id === r.id); if (p) p.est = r; }
  state.total = res.total;
  renderParts();
  renderFigures();
  renderTotal();
  renderQuote();
}

/* ---------- rendering: workspace ---------- */
function renderAll() { renderParts(); renderSelected(); renderTotal(); renderQuote(); renderRates(); }

function renderParts() {
  $("#part-count").textContent = state.parts.length ? `(${state.parts.length})` : "";
  $("#part-list").innerHTML = state.parts.map((p) => {
    const g = p.geo, e = p.est;
    let sub = "Reading the file", amt = "", cls = "loading";
    if (g) {
      cls = "";
      if (g.blocked) { sub = g.source === "STEP" ? "Needs a flat pattern" : "Can't be priced as it is"; }
      else if (e && e.priced) { sub = `${p.material} ${p.thickness} · ${p.qty} pcs`; amt = money(e.each * e.qty); }
      else sub = `${p.material || ""} ${p.thickness || ""}`;
    }
    const checks = g && !g.blocked ? (g.warnings || []).length + ((e && e.notes) || []).length : 0;
    return `<li class="part ${cls}" data-id="${p.id}" aria-selected="${p.id === state.selected}" tabindex="0">
      <span class="no">${p.id}</span>
      <span class="text"><span class="name" title="${esc(p.name)}">${esc(p.name)}</span><span class="sub${g && g.blocked ? " flag" : ""}">${esc(sub)}</span>${checks ? `<span class="chk">${plural(checks, "thing", "things")} to check</span>` : ""}</span>
      <span class="amt">${amt || (g && g.blocked ? "–" : "")}</span></li>`;
  }).join("");
  document.querySelectorAll(".part").forEach((li) => {
    const pick = () => { state.selected = li.dataset.id; renderParts(); renderSelected(); };
    li.addEventListener("click", pick);
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
  $("#download-pdf").disabled = !(state.total && state.parts.some((p) => p.est && p.est.priced));
}

const selectedPart = () => state.parts.find((p) => p.id === state.selected);

function renderSelected() {
  const p = selectedPart();
  const sheet = $("#sheet"), legend = $("#legend");
  if (!p) {
    $("#draw-title").textContent = "No part selected";
    $("#draw-meta").textContent = "";
    sheet.innerHTML = `<div class="empty"><b>Add the customer's drawings</b>Drop DXF flat patterns, PDF prints or STEP models on the left. Each part is drawn here as it will be cut.
      <br><button class="btn" id="try-2">Try the sample files</button></div>`;
    const t2 = $("#try-2"); if (t2) t2.addEventListener("click", trySamples);
    legend.innerHTML = "";
    $("#est-title").textContent = "Estimate";
    $("#est-body").innerHTML = `<p class="units">Pick a part to set its material, thickness and quantity.</p>`;
    return;
  }
  const g = p.geo;
  $("#draw-title").textContent = `${p.id} · ${p.name}`;
  $("#est-title").textContent = `Estimate for ${p.id}`;
  if (!g) { $("#draw-meta").textContent = ""; sheet.innerHTML = `<div class="empty">Reading ${esc(p.name)}…</div>`; legend.innerHTML = ""; $("#est-body").innerHTML = ""; return; }
  $("#draw-meta").textContent = g.blocked ? g.source : `${g.source} · ${g.width.toFixed(3)} × ${g.height.toFixed(3)} in · ${g.cut_length.toFixed(2)} in cut · ${plural(g.pierces, "pierce")}`;
  if (g.blocked) {
    sheet.innerHTML = `<div class="blocked"><b>Can't price this file as it is</b>${esc(g.blocked)}</div>`;
    legend.innerHTML = "";
  } else {
    sheet.innerHTML = drawPart(g);
    const items = [`<span><svg viewBox="0 0 22 12"><rect x="1" y="1" width="20" height="10" fill="#d6d9dc" stroke="#111417"/></svg>part</span>`];
    if (g.reference_lines.length) items.push(`<span><svg viewBox="0 0 22 12"><line x1="1" y1="6" x2="21" y2="6" stroke="#0b8794" stroke-width="2" stroke-dasharray="4 3"/></svg>bend or etch line, not cut</span>`);
    if (g.contours.some((c) => c.role === "open")) items.push(`<span><svg viewBox="0 0 22 12"><line x1="1" y1="6" x2="21" y2="6" stroke="#c4161c" stroke-width="2"/></svg>open outline</span>`);
    legend.innerHTML = items.join("");
  }
  renderControls(p);
  renderFigures();
}

function drawPart(g) {
  const pts = g.contours.flatMap((c) => c.points);
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(g.width, 1e-6), h = Math.max(g.height, 1e-6);
  const pad = Math.max(w, h) * 0.05, sw = Math.max(w, h) / 450;
  const d = (ps, close) => ps.map((q, i) => `${i ? "L" : "M"}${q[0].toFixed(4)},${(-q[1]).toFixed(4)}`).join(" ") + (close ? " Z" : "");
  const closed = g.contours.filter((c) => c.closed).map((c) => d(c.points, true)).join(" ");
  const refs = g.reference_lines.map((r) => `<path d="${d(r)}" fill="none" stroke="#0b8794" stroke-width="${sw * 1.4}" stroke-dasharray="${sw * 7} ${sw * 5}"/>`).join("");
  const open = g.contours.filter((c) => !c.closed).map((c) => `<path d="${d(c.points)}" fill="none" stroke="#c4161c" stroke-width="${sw * 2}"/>`).join("");
  return `<svg class="part-svg" viewBox="${x0 - pad} ${-(y0 + h) - pad} ${w + 2 * pad} ${h + 2 * pad}" role="img" aria-label="Flat pattern of ${esc(g.name)}">
    ${closed ? `<path d="${closed}" fill="#d6d9dc" fill-rule="evenodd" stroke="#111417" stroke-width="${sw}" stroke-linejoin="round"/>` : ""}${refs}${open}</svg>`;
}

function renderControls(p) {
  const g = p.geo, body = $("#est-body");
  if (g.blocked) {
    body.innerHTML = `<p class="units">${g.source === "STEP" ? "Ask the customer for the flat pattern as a DXF. If you have the CAD, unfold it yourself and drop the DXF in." : "Ask the customer for a DXF, or the PDF exported straight from their CAD."}</p>
      <div class="remove"><button class="btn danger" id="remove">Remove ${p.id} from the quote</button></div>`;
    $("#remove").addEventListener("click", () => removePart(p));
    return;
  }
  const mats = materials().map((m) => `<option${m === p.material ? " selected" : ""}>${esc(m)}</option>`).join("");
  const ths = thicknesses(p.material).map((r) => `<option${String(r.thickness) === String(p.thickness) ? " selected" : ""}>${esc(r.thickness)}</option>`).join("");
  let extra = "";
  if (g.source === "DXF") extra = `<label class="field"><span>Drawing units</span><select id="f-units">${["auto", "inches", "millimetres"].map((u) => `<option${u === p.units ? " selected" : ""}>${u}</option>`).join("")}</select></label>`;
  if (g.source === "PDF") extra = `<label class="field"><span>Real width (in), blank for 1:1</span><input id="f-width" type="number" min="0" step="0.125" value="${p.pdfWidth || ""}" placeholder="${g.width.toFixed(3)}"></label>`;
  body.innerHTML = `<div class="fields"><label class="field"><span>Material</span><select id="f-mat">${mats}</select></label>
    <label class="field"><span>Thickness</span><select id="f-thk">${ths}</select></label>
    <label class="field"><span>Qty</span><input id="f-qty" type="number" min="1" step="1" value="${p.qty}"></label></div>
    ${extra ? `<div class="fields four">${extra}</div>` : ""}
    <div id="figures"></div>
    <div class="remove"><button class="btn danger" id="remove">Remove ${p.id} from the quote</button></div>`;
  $("#f-mat").addEventListener("change", (e) => { p.material = e.target.value; p.thickness = defaultThickness(p); renderControls(p); renderSelected(); reprice(); });
  $("#f-thk").addEventListener("change", (e) => { p.thickness = e.target.value; reprice(); });
  $("#f-qty").addEventListener("input", (e) => { const v = parseInt(e.target.value, 10); if (v > 0) { p.qty = v; reprice(); } });
  const u = $("#f-units"); if (u) u.addEventListener("change", async (e) => { p.units = e.target.value; await analyze(p); renderSelected(); reprice(); });
  const w = $("#f-width"); if (w) w.addEventListener("change", async (e) => { p.pdfWidth = parseFloat(e.target.value) || 0; await analyze(p); renderSelected(); reprice(); });
  $("#remove").addEventListener("click", () => removePart(p));
}

function renderFigures() {
  const p = selectedPart(), box = $("#figures");
  if (!p || !p.geo || p.geo.blocked || !box) return;
  const g = p.geo, e = p.est;
  if (!e || !e.priced) { box.innerHTML = `<p class="units">${esc((e && e.reason) || "Working it out…")}</p>`; return; }
  const s = state.settings;
  const sheet = e.per_sheet ? `${e.per_sheet} per ${e.sheet[0]} × ${e.sheet[1]} sheet` : "doesn't fit the sheet";
  const notes = [...(g.warnings || []), ...(e.notes || [])];
  box.innerHTML = `<table class="figs"><caption>Cut</caption>
      <tr><td>Cut length</td><td>${g.cut_length.toFixed(2)} in</td></tr>
      <tr><td>Pierces<small>${plural(g.holes, "hole or cutout", "holes or cutouts")}</small></td><td>${g.pierces}</td></tr>
      <tr><td>Weight each</td><td>${e.weight_each.toFixed(2)} lb</td></tr>
      <tr><td>Sheet use<small>${sheet}</small></td><td>${plural(e.sheets, "sheet")}</td></tr>
      <tr class="sum"><td>Run time each</td><td>${mins(e.run_min_each)}</td></tr></table>
    <table class="figs"><caption>Price each</caption>
      <tr><td>Machine<small>at $${s.machine_rate_hr}/hr</small></td><td>${money(e.machine_each)}</td></tr>
      <tr><td>Material<small>+${s.material_markup_pct}%, ${esc(s.material_basis.toLowerCase())}</small></td><td>${money(e.material_each)}</td></tr>
      <tr><td>Handling</td><td>${money(s.handling_per_part)}</td></tr>
      <tr><td>Each</td><td>${money(e.each)}</td></tr>
      <tr class="sum"><td>${p.qty} pieces</td><td>${money(e.each * e.qty)}</td></tr></table>
    ${notes.length ? `<div class="checks"><b>Check before quoting</b><ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
    <p class="units">Units: ${esc(g.units_note)}</p>`;
}

function renderTotal() {
  const t = state.total, el = $("#job-total");
  const priced = state.parts.filter((p) => p.est && p.est.priced);
  if (!t || !priced.length) { el.innerHTML = `<span class="label">Job total</span><span class="big">$0.00</span><span class="note">Add drawings to start a quote.</span>`; return; }
  const blocked = state.parts.filter((p) => p.geo && p.geo.blocked).length;
  el.innerHTML = `<span class="label">Job total</span><span class="big">${money(t.total)}</span>
    <span class="note">${plural(priced.length, "part")} priced, ${mins(t.run_min)} of laser time, setup ${money(t.setup)}${t.minimum_applied ? ", minimum charge applied" : ""}${blocked ? `. ${blocked} not priced` : ""}.</span>
    <a class="link preview" href="#quote" data-view="quote">Preview the quote</a>`;
  el.querySelector(".preview").addEventListener("click", (e) => { e.preventDefault(); showView("quote"); });
}

async function removePart(p) {
  state.parts = state.parts.filter((x) => x !== p);
  await call("forget", { pid: p.id });
  if (state.selected === p.id) state.selected = state.parts.length ? state.parts[0].id : null;
  renderSelected();
  await reprice();
}

/* ---------- quote ---------- */
function quoteLines() {
  return state.parts.filter((p) => p.est && p.est.priced).map((p) => ({ p, e: p.est }));
}

function renderQuote() {
  const lines = quoteLines(), t = state.total, s = state.settings;
  const valid = new Date(Date.now() + (s.valid_days || 30) * 864e5).toLocaleDateString("en-US");
  const rows = lines.map(({ p, e }, i) => `<tr><td>${i + 1}</td><td>${esc(p.name)}<small>${esc(p.material)} ${esc(p.thickness)}, ${p.geo.cut_length.toFixed(1)} in cut, ${plural(p.geo.pierces, "pierce")}</small></td>
    <td class="r">${p.qty}</td><td class="r">${money(e.each)}</td><td class="r">${money(e.each * e.qty)}</td></tr>`).join("");
  $("#quote-sheet").innerHTML = `<div class="qtop"><b>${esc(s.shop_name || "Your shop name")}</b><span>QUOTATION</span></div>
    <div class="qmeta"><div><i>Customer</i>${esc($("#customer").value || "–")}</div><div><i>Reference</i>${esc($("#reference").value || "–")}</div>
    <div><i>Quote no.</i>${esc($("#quote-no").value)}</div><div><i>Valid until</i>${valid}</div></div>
    ${lines.length ? `<table><tr><th>Item</th><th>Part / description</th><th class="r">Qty</th><th class="r">Unit price</th><th class="r">Amount</th></tr>${rows}
      <tr class="t"><td colspan="4" class="r">Parts</td><td class="r">${money(t.parts)}</td></tr>
      <tr class="t"><td colspan="4" class="r">Setup</td><td class="r">${money(t.setup)}</td></tr>
      ${t.minimum_applied ? `<tr class="t"><td colspan="4" class="r">Minimum charge adjustment</td><td class="r">${money(t.total - t.subtotal)}</td></tr>` : ""}
      <tr class="g"><td colspan="4" class="r">Total</td><td class="r">${money(t.total)}</td></tr></table>` : `<p class="none">No priced parts yet. Add drawings on the Parts screen.</p>`}
    <p class="terms">Lead time ${s.lead_days || 5} working days ARO, once files are approved. Prices FOB our shop, sales tax extra where it applies.</p>`;
}

function jobPayload() {
  return {
    parts: state.parts.filter((p) => p.geo && !p.geo.blocked).map((p) => ({ id: p.id, material: p.material, thickness: p.thickness, qty: p.qty })),
    rates: state.rates, settings: state.settings, customer: $("#customer").value, reference: $("#reference").value, quote_no: $("#quote-no").value,
  };
}

function save(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function downloadPdf() {
  const b64 = await call("quote_pdf", { job: jobPayload() });
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  save(new Blob([bytes], { type: "application/pdf" }), `${$("#quote-no").value || "quote"}.pdf`);
}

function downloadCsv() {
  const head = ["Item", "Part", "Material", "Qty", "Cut length (in)", "Pierces", "Run time each (s)", "Unit price", "Amount"];
  const rows = quoteLines().map(({ p, e }, i) => [i + 1, p.name, `${p.material} ${p.thickness}`, p.qty, p.geo.cut_length.toFixed(2), p.geo.pierces, Math.round(e.run_min_each * 60), e.each.toFixed(2), (e.each * e.qty).toFixed(2)]);
  const csv = [head, ...rows].map((r) => r.map((c) => (/[",]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c)).join(",")).join("\r\n");
  save(new Blob([csv], { type: "text/csv" }), `${$("#quote-no").value || "quote"}.csv`);
}

/* ---------- rates ---------- */
const RATE_COLS = [["material", "Material", "text"], ["thickness", "Thickness", "text"], ["thickness_in", "Thickness (in)", 0.0001],
  ["feed_ipm", "Cut speed (in/min)", 1], ["pierce_sec", "Pierce time (s)", 0.1], ["density_lb_in3", "Density (lb/in³)", 0.0001], ["price_per_lb", "Material price ($/lb)", 0.01]];

function renderRates() {
  const t = $("#rates-table");
  t.innerHTML = `<tr>${RATE_COLS.map((c) => `<th>${c[1]}</th>`).join("")}<th></th></tr>` + state.rates.map((r, i) =>
    `<tr>${RATE_COLS.map(([k, , st]) => `<td><input data-i="${i}" data-k="${k}" ${st === "text" ? "" : `type="number" step="${st}"`} value="${esc(r[k] ?? "")}" aria-label="${k}"></td>`).join("")}
      <td><button class="del" data-del="${i}" aria-label="Delete row" title="Delete row"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6"/></svg></button></td></tr>`).join("");
  t.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", () => {
    const r = state.rates[+inp.dataset.i], k = inp.dataset.k;
    r[k] = inp.type === "number" ? parseFloat(inp.value) || 0 : inp.value;
    afterRatesChange();
  }));
  t.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { state.rates.splice(+b.dataset.del, 1); renderRates(); afterRatesChange(); }));

  const s = state.settings;
  $("#charges").innerHTML = CHARGES.map(([k, label, step, wide]) => {
    if (step === "basis") return `<label class="field"><span>${label}</span><select data-c="${k}">${BASES.map((b) => `<option${b === s[k] ? " selected" : ""}>${b}</option>`).join("")}</select></label>`;
    if (step === "text") return `<label class="field ${wide || ""}"><span>${label}</span><input data-c="${k}" value="${esc(s[k] || "")}" placeholder="Your shop name"></label>`;
    return `<label class="field"><span>${label}</span><input data-c="${k}" type="number" step="${step}" value="${s[k] ?? ""}"></label>`;
  }).join("");
  $("#charges").querySelectorAll("[data-c]").forEach((el) => el.addEventListener("change", () => {
    s[el.dataset.c] = el.type === "number" ? parseFloat(el.value) || 0 : el.value;
    if (state.ready) reprice();
  }));

}

function afterRatesChange() {
  for (const p of state.parts) {
    if (!thicknesses(p.material).some((r) => String(r.thickness) === String(p.thickness))) { p.material = materials()[0]; p.thickness = defaultThickness(p); }
  }
  renderSelected();
  if (state.ready) reprice();
}

async function loadWorkbook(e) {
  const f = e.target.files[0];
  if (!f) return;
  const res = await call("read_rates", { b64: await readB64(f) });
  state.rates = res.rows;
  Object.assign(state.settings, res.charges);
  e.target.value = "";
  renderRates();
  afterRatesChange();
}

/* ---------- help ---------- */
let helpDone = false;
async function renderHelp() {
  if (helpDone) return;
  const md = await (await fetch(`app/how_it_works.md?v=${document.documentElement.dataset.version}`)).text();
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const out = [];
  let list = false;
  for (const line of md.split(/\r?\n/)) {
    if (line.startsWith("- ")) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(line.slice(2))}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    if (line.startsWith("## ")) out.push(`<h2>${inline(line.slice(3))}</h2>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  $("#help").innerHTML = out.join("");
  helpDone = true;
}

start();
