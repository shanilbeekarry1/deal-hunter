"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gbp = (v) => (v == null || isNaN(v)) ? "—" : (v < 0 ? "−£" : "£") + Math.abs(Math.round(v)).toLocaleString("en-GB");
const pct = (v, d = 1) => (v == null || isNaN(v)) ? "—" : (v * 100).toFixed(d) + "%";

const STATUSES = ["New", "Shortlisted", "Agent called", "Viewing booked", "Offer made", "Under offer", "Rejected"];
const REASONS = ["", "Numbers don't work", "Condition / structural", "Location / street", "Lease or title issue",
  "Unmortgageable", "Tenants won't leave", "Sold before we acted", "Vendor won't negotiate", "Other"];
const SLIDERS = [
  ["target_margin", "Minimum profit on sale value", 5, 30, 1, "pct"],
  ["sale_discount", "Sell below market by", 0, 10, 1, "pct"],
  ["months", "Months held", 3, 18, 1, "months"],
  ["bridge_rate", "Bridging rate per month", 0.4, 1.5, 0.05, "pct2"],
  ["bridge_ltv", "Bridging loan to value", 0, 80, 5, "pct"],
  ["contingency", "Refurb contingency", 0, 30, 5, "pct"],
  ["holding_per_month", "Holding costs per month", 0, 1500, 50, "gbp"],
];

const S = { me: null, settings: null, allLists: [], deals: [], sel: { shortlist: null, pipeline: null },
  verdicts: new Set(["Go", "Review"]), area: "", hideRejected: true, tab: "shortlist" };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (r.status === 401) { showLogin(); throw new Error(data.error || "Sign in first."); }
  if (!r.ok) throw new Error(data.error || data.detail || `Request failed (${r.status})`);
  return data;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2500);
}
function banner(msg, kind = "") {
  const b = $("#banner"); b.className = "banner " + kind; b.textContent = msg; b.hidden = !msg;
}

/* ---------------------------------------------------------------- sign in */
function statusLine(st) {
  if (!st) return "";
  const p = st.present, miss = Object.keys(p).filter((k) => !p[k]);
  return `Version ${st.version} (${st.env}). ` + (miss.length ? `Missing settings: ${miss.join(", ")}.` : "All settings found.")
    + (miss.length && st.similar_names.length ? ` Found instead: ${st.similar_names.join(", ")}.` : "");
}
function showLogin() {
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-status").textContent = statusLine(S.me && S.me.status);
  $("#pw-wrap").hidden = S.me && S.me.password_required === false;
  $("#login-name").value = decodeURIComponent((document.cookie.match(/dh_name=([^;]+)/) || [])[1] || "");
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#login-err").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { name: $("#login-name").value, password: $("#login-pw").value } });
    await start();
  } catch (err) { $("#login-err").textContent = err.message; }
});

async function init() {
  S.me = await api("/api/me");
  if (!S.me.signed_in) return showLogin();
  await start();
}

async function start() {
  S.me = await api("/api/me");
  $("#login").hidden = true; $("#app").hidden = false;
  $("#who").textContent = S.me.name;
  const pill = $("#mode-pill");
  pill.className = "pill " + (S.me.live ? "live" : "demo");
  pill.textContent = S.me.live ? "Live PropertyData" : "Demo data";
  const sl = statusLine(S.me.status);
  if (!S.me.live || !S.me.persistent) banner((S.me.live ? "" : "Demo mode: invented listings. ") + sl, "");
  const st = await api("/api/settings");
  S.allLists = st.all_lists; S.settings = st;
  renderSettings();
  await loadDeals();
  if (!S.deals.length) { $("#settings").hidden = false; $("#btn-settings").setAttribute("aria-expanded", "true"); }
}

/* ---------------------------------------------------------------- settings */
function renderSettings() {
  const s = S.settings;
  $("#areas").innerHTML = (s.areas.length ? `<div class="area area-head"><span>Label</span><span>Postcode or town</span><span>Miles</span><span></span></div>` : "")
    + s.areas.map((a, i) => `
    <div class="area" data-i="${i}">
      <input aria-label="Label" data-k="name" value="${esc(a.name)}" placeholder="East Leeds">
      <input aria-label="Postcode or town" data-k="postcode" value="${esc(a.postcode)}" placeholder="LS9 or Leeds">
      <input aria-label="Radius in miles" data-k="radius" type="number" min="1" max="40" value="${esc(a.radius)}">
      <button type="button" aria-label="Remove area" data-rm="${i}">×</button>
    </div>`).join("") || `<p class="muted small">No areas yet.</p>`;
  $("#lists").innerHTML = S.allLists.map((l) =>
    `<button type="button" data-list="${esc(l)}" aria-pressed="${s.lists.includes(l)}">${esc(l.replace(/-properties$/, "").replace(/-/g, " "))}</button>`).join("");
  for (const k of ["min_price", "max_price"]) $("#bb-" + k).value = s.bb[k];
  $("#bb-exclude_flats").checked = !!s.bb.exclude_flats;
  $("#sliders").innerHTML = SLIDERS.map(([k, label, min, max, step, fmt]) => {
    const v = fmt.startsWith("pct") ? +(s.bb[k] * 100).toFixed(2) : s.bb[k];
    return `<div class="slider"><div class="row"><label for="sl-${k}" style="display:inline">${label}</label><output id="out-${k}"></output></div>
      <input id="sl-${k}" type="range" min="${min}" max="${max}" step="${step}" value="${v}" data-k="${k}" data-fmt="${fmt}"></div>`;
  }).join("");
  $$("#sliders input").forEach(showSlider);
  for (const b of ["light", "medium", "heavy"]) $("#rate-" + b).value = s.rates[b];
  $("#budget").value = s.budget;
}
function showSlider(inp) {
  const v = +inp.value, f = inp.dataset.fmt;
  $("#out-" + inp.dataset.k).textContent = f === "pct" ? v + "%" : f === "pct2" ? v.toFixed(2) + "%" : f === "months" ? v + " months" : gbp(v);
}
function collectSettings() {
  const s = S.settings;
  s.areas = $$("#areas .area").map((row) => ({
    name: $("[data-k=name]", row).value.trim(), postcode: $("[data-k=postcode]", row).value.trim().toUpperCase(),
    radius: +$("[data-k=radius]", row).value || 5 }));
  s.lists = $$("#lists button[aria-pressed=true]").map((b) => b.dataset.list);
  s.bb.min_price = +$("#bb-min_price").value || 0;
  s.bb.max_price = +$("#bb-max_price").value || 0;
  s.bb.exclude_flats = $("#bb-exclude_flats").checked;
  $$("#sliders input").forEach((i) => { s.bb[i.dataset.k] = i.dataset.fmt.startsWith("pct") ? +i.value / 100 : +i.value; });
  for (const b of ["light", "medium", "heavy"]) s.rates[b] = +$("#rate-" + b).value || 0;
  s.budget = +$("#budget").value || 50;
}
let saveTimer;
function settingsChanged() {
  collectSettings();
  $("#settings-saved").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const { areas, lists, bb, rates, budget } = S.settings;
      await api("/api/settings", { method: "PUT", body: { areas, lists, bb, rates, budget } });
      $("#settings-saved").textContent = "Saved";
      await loadDeals();
    } catch (e) { $("#settings-saved").textContent = e.message; }
  }, 450);
}
$("#settings").addEventListener("input", (e) => {
  if (e.target.matches("#sliders input")) showSlider(e.target);
  if (e.target.closest("#areas") && e.target.dataset.k === "postcode") return; // save postcode on change, not every key
  settingsChanged();
});
$("#settings").addEventListener("change", (e) => { if (e.target.dataset.k === "postcode") settingsChanged(); });
$("#settings").addEventListener("click", (e) => {
  const rm = e.target.closest("[data-rm]");
  if (rm) { collectSettings(); S.settings.areas.splice(+rm.dataset.rm, 1); renderSettings(); settingsChanged(); }
  const l = e.target.closest("[data-list]");
  if (l) {
    const on = l.getAttribute("aria-pressed") === "true";
    if (!on && $$("#lists button[aria-pressed=true]").length >= 5) return toast("Up to 5 lists per scan");
    l.setAttribute("aria-pressed", String(!on)); settingsChanged();
  }
});
$("#add-area").addEventListener("click", () => {
  collectSettings(); S.settings.areas.push({ name: "", postcode: "", radius: 5 }); renderSettings();
  $$("#areas .area input[data-k=name]").pop().focus();
});
$("#btn-settings").addEventListener("click", () => {
  const p = $("#settings"); p.hidden = !p.hidden; $("#btn-settings").setAttribute("aria-expanded", String(!p.hidden));
});
$("#btn-check-key").addEventListener("click", async () => {
  const out = $("#key-result"); out.hidden = false; out.textContent = "Checking…";
  try {
    const r = await api("/api/check-key", { method: "POST" });
    out.textContent = (r.status === "success" ? "Key works.\n" : "Key check failed.\n") + JSON.stringify(r, null, 2);
  } catch (e) { out.textContent = e.message; }
});

/* ---------------------------------------------------------------- scan */
$("#btn-scan").addEventListener("click", async () => {
  const b = $("#btn-scan"); b.disabled = true; b.textContent = "Scanning…";
  banner("Scanning. This can take up to 4 minutes on the first run; repeat scans are quicker.", "");
  try {
    const r = await api("/api/scan", { method: "POST" });
    const msg = `Scan done in ${r.seconds}s: ${r.found} listings found, ${r.listings} valued, ${r.credits} credits used.`
      + (r.stopped ? " " + r.stopped : "")
      + (r.errors.length ? ` Problem: ${r.errors[0]}` + (r.errors.length > 1 ? ` (+${r.errors.length - 1} more in Scan history)` : "") : "")
      + (!r.found && !r.errors.length ? " Nothing matched: try a wider radius, a higher max price or more sourcing lists." : "");
    banner(msg, r.stopped || r.errors.length ? "" : "ok");
    await loadDeals();
  } catch (e) { banner("Scan failed: " + e.message, "bad"); }
  b.disabled = false; b.textContent = "Run scan";
});

/* ---------------------------------------------------------------- data */
async function loadDeals() {
  const r = await api("/api/deals");
  S.deals = r.deals;
  const areas = [...new Set(S.deals.map((d) => d.area))].sort();
  const sel = $("#area-filter"); const cur = S.area;
  sel.innerHTML = `<option value="">All areas</option>` + areas.map((a) => `<option ${a === cur ? "selected" : ""}>${esc(a)}</option>`).join("");
  render();
}
const ok = () => S.deals.filter((d) => d.evaluation && d.evaluation.ok);

function render() {
  renderStats();
  if (S.tab === "shortlist") renderShortlist();
  if (S.tab === "pipeline") renderPipeline();
  if (S.tab === "history") renderHistory();
}

function renderStats() {
  const act = ok().filter((d) => d.log.status !== "Rejected");
  const n = (v) => act.filter((d) => d.evaluation.verdict === v).length;
  const cash = act.filter((d) => d.evaluation.verdict === "Go").reduce((a, d) => a + d.evaluation.base.cash_in, 0);
  const inPipe = S.deals.filter((d) => !["New", "Rejected"].includes(d.log.status)).length;
  $("#stats").innerHTML = `
    <div class="stat"><span>Listings checked</span><b class="num">${S.deals.length}</b></div>
    <div class="stat go"><span>Go</span><b class="num">${n("Go")}</b></div>
    <div class="stat review"><span>Review</span><b class="num">${n("Review")}</b></div>
    <div class="stat pass"><span>Pass</span><b class="num">${n("Pass")}</b></div>
    <div class="stat"><span>In pipeline</span><b class="num">${inPipe}</b></div>
    <div class="stat"><span>Cash to fund all Go</span><b class="num">${gbp(cash)}</b></div>`;
}

function dealButton(d, current) {
  const e = d.evaluation, l = d.data;
  const cut = l.reduced_pct ? ` · cut ${Math.round(l.reduced_pct)}%` : "";
  const dom = l.days_on_market ? ` · ${Math.round(l.days_on_market)} days` : "";
  return `<button class="deal ${e.verdict}" data-id="${esc(d.id)}" aria-current="${d.id === current}">
    <span class="t">${esc(l.address)}</span>
    <span class="s"><span class="num">${gbp(l.price)}</span> · ${esc(l.bedrooms ?? "?")} bed ${esc((l.type_std || l.type || "").toLowerCase())}${cut}${dom}</span>
    <span class="r"><span class="verdict ${e.verdict}">${e.verdict}</span>
      <span class="num small ${e.base.profit >= 0 ? "pos" : "neg"}">${gbp(e.base.profit)}</span>
      ${d.log.status !== "New" ? `<span class="status-tag">${esc(d.log.status)}</span>` : ""}</span>
  </button>`;
}

function renderShortlist() {
  const rows = ok().filter((d) => S.verdicts.has(d.evaluation.verdict) && (!S.area || d.area === S.area)
    && !(S.hideRejected && d.log.status === "Rejected"))
    .sort((a, b) => b.evaluation.rank_score - a.evaluation.rank_score);
  const skipped = S.deals.length - ok().length;
  if (!S.deals.length) {
    $("#list").innerHTML = `<div class="empty"><b>No listings yet.</b><br>Set your areas under <b>Buy box &amp; areas</b>, then press <b>Run scan</b>.</div>`;
    $("#sheet").innerHTML = ""; $("#sheet").hidden = true; return;
  }
  if (!rows.find((d) => d.id === S.sel.shortlist)) S.sel.shortlist = rows[0]?.id || null;
  $("#list").innerHTML = (rows.map((d) => dealButton(d, S.sel.shortlist)).join("")
    || `<div class="empty">Nothing matches these filters. Try showing Pass deals.</div>`)
    + (skipped ? `<p class="muted small">${skipped} listings skipped: no sold comparables found.</p>` : "");
  const d = S.deals.find((x) => x.id === S.sel.shortlist);
  $("#sheet").hidden = !d; if (d) renderSheet(d, $("#sheet"));
}

function renderPipeline() {
  const order = Object.fromEntries(STATUSES.map((s, i) => [s, i]));
  const rows = ok().filter((d) => d.log.status !== "New").sort((a, b) => order[a.log.status] - order[b.log.status]);
  if (!rows.find((d) => d.id === S.sel.pipeline)) S.sel.pipeline = rows[0]?.id || null;
  $("#pipe-list").innerHTML = rows.map((d) => dealButton(d, S.sel.pipeline)).join("")
    || `<div class="empty">No deals in the pipeline yet. Open a deal on the Shortlist and set its status.</div>`;
  const rej = rows.filter((d) => d.log.status === "Rejected" && d.log.reason);
  const counts = {}; rej.forEach((d) => (counts[d.log.reason] = (counts[d.log.reason] || 0) + 1));
  const max = Math.max(1, ...Object.values(counts));
  $("#reasons").innerHTML = rej.length ? `<h3>Why deals were rejected</h3><p class="muted small">Use this to tune the buy box and scoring.</p>
    <div class="bars">${Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
      `<div class="bar"><span>${esc(k)}</span><i style="width:${(v / max) * 100}%"></i><span class="num">${v}</span></div>`).join("")}</div>` : "";
  const d = S.deals.find((x) => x.id === S.sel.pipeline);
  $("#pipe-sheet").hidden = !d; if (d) renderSheet(d, $("#pipe-sheet"));
}

async function renderHistory() {
  const r = await api("/api/scans");
  $("#history").innerHTML = r.scans.length ? `<div class="histtable"><table>
    <tr><td><b>When (UTC)</b></td><td><b>Mode</b></td><td><b>Valued</b></td><td><b>Credits</b></td><td><b>Notes</b></td></tr>
    ${r.scans.map((s) => `<tr><td>${esc(s.at)}</td><td>${esc(s.mode)}</td><td>${s.listings}</td><td>${s.credits_used}</td><td class="small">${esc(s.notes)}</td></tr>`).join("")}
  </table></div>` : `<div class="empty">No scans yet.</div>`;
  const raw = r.raw_samples || {};
  $("#raw").innerHTML = Object.keys(raw).length ? `<h3>Raw API responses from the last live scan</h3>
    <p class="muted small">If a field looks empty in the app, check its name here.</p>
    ${Object.entries(raw).map(([k, v]) => `<details><summary>${esc(k)}</summary><pre>${esc(JSON.stringify(v, null, 2).slice(0, 8000))}</pre></details>`).join("")}` : "";
}

/* ---------------------------------------------------------------- deal sheet */
function renderSheet(d, el) {
  const l = d.data, e = d.evaluation, b = e.base, w = e.worst, best = e.best, bb = S.settings.bb, o = d.log.overrides || {};
  const c = e.comps || {};
  const lists = (l.lists || []).map((x) => `<span class="chip">${esc(x.replace(/-properties$/, "").replace(/-/g, " "))}</span>`).join("");
  const facts = [
    `${esc(l.bedrooms ?? "?")} bed ${esc((l.type_std || l.type || "").toLowerCase())}`,
    `${Math.round(e.sqft).toLocaleString("en-GB")} sq ft (${esc(e.sqft_source)})`,
    `${esc(e.band)} refurb`,
    l.reduced_pct && l.orig_price ? `reduced ${Math.round(l.reduced_pct)}% from ${gbp(l.orig_price)}` : "",
    l.days_on_market ? `${Math.round(l.days_on_market)} days on market` : "",
    l.portal ? esc(l.portal) : "",
  ].filter(Boolean).join(" · ");
  const lines = [
    ["Sale price", b.sale, `${pct(bb.sale_discount, 0)} below value after refurb`],
    ["Purchase", -b.price, ""],
    ["Stamp duty", -b.tax, "Company rate, 5% surcharge on every band"],
    ["Legal, survey, searches", -b.purchase_fees, ""],
    ["Refurb", -b.refurb, `${e.band} at £${S.settings.rates[e.band]}/sq ft + ${pct(bb.contingency, 0)} contingency`],
    ["Bridging interest", -b.interest, `${gbp(b.loan)} at ${pct(bb.bridge_rate, 2)}/month for ${b.months} months`],
    ["Bridging fees", -b.bridge_fees, "2% arrangement + £500 valuation"],
    ["Holding", -b.holding, "Council tax, utilities, insurance"],
    ["Sale costs", -b.sale_costs, "Agent fee + VAT, legal"],
  ];
  el.innerHTML = `
    <div class="sheet-head">
      <div><span class="muted small">${esc(l.postcode)} · ${esc(d.area)} · first seen ${esc(d.first_seen)}</span>
        <h2>${esc(l.precise_address ? titleCase(l.precise_address) : l.address)}</h2></div>
      <span class="verdict ${e.verdict}" style="font-size:13px;padding:4px 10px">${e.verdict}</span>
    </div>
    <div class="muted small">${facts}</div>
    ${lists ? `<div class="chips">${lists}</div>` : ""}
    ${l.url ? `<a class="btn ghost small" href="${esc(l.url)}" target="_blank" rel="noopener" style="justify-self:start">Open listing ↗</a>` : ""}

    <div class="kv">
      <div><span>Asking</span><strong>${gbp(b.price)}</strong></div>
      <div><span>As-is value</span><strong>${gbp(b.asis_value)}</strong></div>
      <div><span>Below value</span><strong class="${b.bmv >= 0.2 ? "pos" : ""}">${pct(b.bmv)}</strong></div>
      <div><span>Value after refurb</span><strong>${gbp(b.gdv)}</strong></div>
      <div class="hl"><span>Max offer for ${pct(bb.target_margin, 0)}</span><strong>${e.max_offer ? gbp(e.max_offer) : "None works"}</strong></div>
      <div><span>Profit</span><strong class="${b.profit >= 0 ? "pos" : "neg"}">${gbp(b.profit)}</strong></div>
      <div><span>Margin</span><strong>${pct(b.margin)}</strong></div>
      <div><span>Cash in · return</span><strong>${gbp(b.cash_in)} · ${pct(b.roi, 0)}</strong></div>
    </div>

    ${e.flags.length ? `<div><h3 style="margin:0 0 6px">Watch-outs</h3><ul class="flags">${e.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></div>` : ""}

    <div style="display:grid;gap:6px">
      <div class="conf"><h3 style="margin:0">Valuation confidence</h3><div class="meter"><i style="width:${e.conf_score}%"></i></div>
        <span class="num small">${esc(e.conf)} · ${e.conf_score}</span></div>
      ${e.suspect ? `<p class="err small" style="margin:0"><b>Price looks wrong for a whole house here.</b> Read the listing before trusting these numbers.</p>` : ""}
      ${c.ok ? `<p class="muted small" style="margin:0">${c.n} sold ${c.same_type ? "same-type " : ""}comparables in the ${esc((l.postcode || "").slice(0, -2).trim())} sector.
        As-is uses the lower quartile <span class="num">£${Math.round(e.asis_psf)}</span>/sq ft, after-refurb the upper quartile
        <span class="num">£${Math.round(e.renov_psf)}</span>/sq ft (median £${Math.round(c.median)}).</p>` : ""}
      ${e.conf_why.length ? `<ul>${e.conf_why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </div>

    <div><h3 style="margin:0 0 4px">Feasibility</h3><table>
      ${lines.map(([a, v, n]) => `<tr><td>${a}${n ? `<small>${esc(n)}</small>` : ""}</td><td>${gbp(v)}</td></tr>`).join("")}
      <tr class="total"><td>Profit before tax</td><td class="${b.profit >= 0 ? "pos" : "neg"}">${gbp(b.profit)}</td></tr>
    </table></div>

    <div class="scen">
      <div><small>Worst</small><b class="${w.profit >= 0 ? "pos" : "neg"}">${gbp(w.profit)}</b><small>Refurb +25%, value −7%, 3 months longer</small></div>
      <div><small>Base</small><b class="${b.profit >= 0 ? "pos" : "neg"}">${gbp(b.profit)}</b><small>Buy box as set</small></div>
      <div><small>Best</small><b class="${best.profit >= 0 ? "pos" : "neg"}">${gbp(best.profit)}</b><small>Refurb −10%, value +3%, 2 months quicker</small></div>
    </div>

    ${l.summary ? `<details><summary>Listing summary</summary><p>${esc(l.summary).replace(/\n/g, "<br>")}</p></details>` : ""}

    <form class="logform" data-id="${esc(d.id)}" data-default-band="${esc(d.default_band)}">
      <h3 style="margin:0">Decision and overrides</h3>
      <div class="form3">
        <label>Status<select name="status">${STATUSES.map((s) => `<option ${s === d.log.status ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label>Reason if rejected<select name="reason">${REASONS.map((s) => `<option value="${esc(s)}" ${s === d.log.reason ? "selected" : ""}>${s || "—"}</option>`).join("")}</select></label>
        <label>Refurb band<select name="band">${["light", "medium", "heavy"].map((s) => `<option ${s === e.band ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      </div>
      <div class="form3">
        <label>Floor area sq ft<input name="sqft" type="number" min="0" placeholder="${Math.round(e.sqft)}" value="${o.sqft || ""}"></label>
        <label>As-is £/sq ft<input name="asis_psf" type="number" min="0" placeholder="${Math.round(e.asis_psf)}" value="${o.asis_psf || ""}"></label>
        <label>After-refurb £/sq ft<input name="renov_psf" type="number" min="0" placeholder="${Math.round(e.renov_psf)}" value="${o.renov_psf || ""}"></label>
        <label>Price to model £<input name="price" type="number" min="0" step="500" placeholder="${Math.round(l.price)}" value="${o.price || ""}"></label>
      </div>
      <label>Notes<textarea name="notes" rows="3" placeholder="Agent says vendor wants a quick sale…">${esc(d.log.notes)}</textarea></label>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="submit">Save</button>
        <span class="muted small">${d.log.updated_at ? `Last updated by ${esc(d.log.updated_by)}, ${esc(d.log.updated_at)} UTC` : "Blank boxes use the data."}</span>
      </div>
    </form>`;
}
function titleCase(s) {
  return s.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase())
    .replace(/\b([a-z]{1,2}\d[a-z\d]?)\s+(\d[a-z]{2})\b/gi, (m, a, b2) => (a + " " + b2).toUpperCase());
}

document.addEventListener("submit", async (e) => {
  const f = e.target.closest("form.logform"); if (!f) return;
  e.preventDefault();
  const v = (n) => f.elements[n].value;
  const overrides = {};
  for (const k of ["sqft", "asis_psf", "renov_psf", "price"]) if (+v(k) > 0) overrides[k] = +v(k);
  if (v("band") !== f.dataset.defaultBand) overrides.band = v("band");
  try {
    await api(`/api/deals/${encodeURIComponent(f.dataset.id)}/log`, { method: "PUT",
      body: { status: v("status"), reason: v("reason"), notes: v("notes"), overrides } });
    toast("Saved");
    await loadDeals();
  } catch (err) { toast(err.message); }
});

/* ---------------------------------------------------------------- navigation */
document.addEventListener("click", (e) => {
  const deal = e.target.closest(".deal");
  if (deal) {
    S.sel[S.tab] = deal.dataset.id; render();
    if (window.matchMedia("(max-width:1100px)").matches) (S.tab === "pipeline" ? $("#pipe-sheet") : $("#sheet")).scrollIntoView({ behavior: "smooth" });
  }
  const tab = e.target.closest("[role=tab]");
  if (tab) {
    S.tab = tab.dataset.tab;
    $$("[role=tab]").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + S.tab));
    render();
  }
  const fc = e.target.closest(".fchip");
  if (fc) {
    const on = fc.getAttribute("aria-pressed") === "true"; fc.setAttribute("aria-pressed", String(!on));
    on ? S.verdicts.delete(fc.dataset.v) : S.verdicts.add(fc.dataset.v); render();
  }
});
$("#area-filter").addEventListener("change", (e) => { S.area = e.target.value; render(); });
$("#hide-rejected").addEventListener("change", (e) => { S.hideRejected = e.target.checked; render(); });
$("#btn-clear").addEventListener("click", async () => {
  const b = $("#btn-clear");
  if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "Click again to clear"; return; }
  b.dataset.confirm = ""; b.textContent = "Clear stored listings";
  await api("/api/clear-listings", { method: "POST" }); await loadDeals(); toast("Cleared");
});

init().catch((e) => banner(e.message, "bad"));
