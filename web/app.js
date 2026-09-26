// SnapSort UI. Live feed over SSE plus plain fetch calls. No build step.
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const label = (s) => String(s ?? "").replace(/_/g, " ");
let STATE = { folders: [] };
let currentTab = "all";

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}
const post = (path, data = {}) => api(path, { method: "POST", body: JSON.stringify(data) });

const KIND = { bill: "Bill", bank_statement: "Bank statement", payment_receipt: "Payment receipt", salary_slip: "Salary slip",
  prescription: "Prescription", lab_report: "Lab report", insurance_card: "Insurance card", id_document: "ID document",
  credential: "Password or code", travel_ticket: "Travel ticket", personal_photo: "Photo", other: "Other document" };
const kindOf = (t) => KIND[t] || "Not identified yet";

// ---------- live feed ----------
const STEP_LABEL = { sense: "Read", triage: "Identify", extract: "Details", verify: "Check", plan: "Plan", policy: "Privacy rules",
  act: "Action", done: "Done", handoff: "Needs you", human: "You", learn: "Rule", recover: "Recovery", model: "Model",
  retry: "Retry", error: "Problem", undo: "Undo" };

function addEvent(ev, fresh = false) {
  const li = document.createElement("li");
  li.className = `ev lvl-${ev.level} st-${ev.stage}${fresh ? " fresh" : ""}`;
  const time = new Date(ev.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  li.innerHTML = `<div class="meta"><span class="step">${esc(STEP_LABEL[ev.stage] || ev.stage)}</span>` +
    `<span>${ev.file_id ? `<button class="fid link" data-id="${ev.file_id}">file ${ev.file_id}</button> ` : ""}${time}</span></div>` +
    `<div class="msg">${esc(ev.message)}</div>`;
  const feed = $("#feed");
  feed.prepend(li);
  while (feed.children.length > 300) feed.lastChild.remove();
}

async function startFeed() {
  const recent = await api("/api/events/recent");
  recent.forEach((e) => addEvent(e));
  const last = recent.length ? recent[recent.length - 1].id : 0;
  const es = new EventSource(`/api/events?after=${last}`);
  es.onmessage = (m) => { addEvent(JSON.parse(m.data), true); scheduleRefresh(); };
}

let refreshTimer;
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshAll, 400); }
function refreshAll() { return Promise.allSettled([refreshState(), refreshLibrary(), refreshInbox(), refreshReminders(), refreshRules(), refreshExpenses()]); }

// ---------- header ----------
async function refreshState() {
  STATE = await api("/api/state");
  const { network, models, tasks, stats } = STATE;
  const offline = network.internet === false;
  const net = $("#pill-net");
  net.textContent = offline ? `Offline, ${network.outbound.length} outside connections` : `Online, ${network.outbound.length} outside connections from SnapSort`;
  net.className = `pill ${network.outbound.length ? "bad" : offline ? "good" : "warn"}`;
  const model = $("#pill-model");
  model.textContent = models.available ? `Gemma 4 running on this laptop` : "Gemma isn't running. Files will wait.";
  model.title = `${models.triage} identifies files, ${models.work} reads details and answers`;
  model.className = `pill ${models.available ? "good" : "bad"}`;
  const waiting = (tasks.queued || 0) + (tasks.running || 0);
  const q = $("#pill-queue");
  q.textContent = waiting ? `Working on ${waiting} file${waiting === 1 ? "" : "s"}` : "All caught up";
  q.className = `pill ${waiting ? "warn" : "good"}`;
  const s = stats.by_status || {};
  $("#summary").textContent = `${stats.files} files looked after. ${s.done || 0} handled on their own, ${stats.inbox_open} waiting for you` +
    (stats.avg_seconds ? `, about ${stats.avg_seconds}s each.` : ".");
}

// ---------- library ----------
const TABS = { all: "All", bill: "Bills", bank_statement: "Statements", payment_receipt: "Receipts", salary_slip: "Salary",
  prescription: "Prescriptions", lab_report: "Lab reports", insurance_card: "Insurance", id_document: "IDs",
  credential: "Passwords", travel_ticket: "Travel", personal_photo: "Photos", other: "Other" };

function stateOf(f) {
  if (f.status === "needs_human") return ["Waiting for you", "wait"];
  if (f.status === "queued" || f.status === "running") return ["Working on it", "wait"];
  if (f.status === "failed") return ["Couldn't process this", "bad"];
  if (f.vaulted) return ["Locked in the vault", "ok"];
  if (f.duplicate_of) return [`Copy of file ${f.duplicate_of}, left in place`, "ok"];
  if (f.location.startsWith("library/")) return [`Filed in ${f.location.split("/").slice(1, -1).join("/")}`, "ok"];
  return ["Indexed so you can search it", "ok"];
}

async function refreshLibrary() {
  const files = await api("/api/files");
  const counts = {};
  files.forEach((f) => { counts[f.doc_type] = (counts[f.doc_type] || 0) + 1; });
  $("#tabs").innerHTML = Object.entries(TABS)
    .filter(([k]) => k === "all" || counts[k])
    .map(([k, name]) => `<button class="tab ${k === currentTab ? "on" : ""}" data-tab="${k}" aria-pressed="${k === currentTab}">${name} <small>${k === "all" ? files.length : counts[k]}</small></button>`)
    .join("");
  const shown = files.filter((f) => currentTab === "all" || f.doc_type === currentTab);
  $("#library").innerHTML = shown.map(card).join("") ||
    `<p class="empty">Drop a file into mock/Downloads, mock/Screenshots or mock/Desktop and it will show up here.</p>`;
}

function card(f) {
  const [state, cls] = stateOf(f);
  const kv = Object.entries(f.fields || {}).filter(([k, v]) => v && typeof v !== "object" && k !== "description").slice(0, 3)
    .map(([k, v]) => [k, String(v).length > 48 ? String(v).slice(0, 46) + "…" : v]);
  return `<article class="card st-${f.status} sens-${f.sensitivity || "low"}" data-id="${f.id}" tabindex="0" role="button" aria-label="Open ${esc(f.title || f.name)}">
    <div class="kind">${esc(kindOf(f.doc_type))}</div>
    <h3>${esc(f.title || f.name)}</h3>
    ${f.summary ? `<p class="sum">${esc(f.summary)}</p>` : ""}
    ${kv.length ? `<dl class="kv">${kv.map(([k, v]) => `<dt>${esc(label(k))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    <div class="state ${cls}">${esc(state)}</div>
  </article>`;
}

// ---------- needs you ----------
function describeAction(a) {
  if (a.tool === "file_document") return `File it in ${a.args.folder} as ${a.args.name}`;
  if (a.tool === "create_reminder") return `Remind you: ${a.args.title}, by ${a.args.due_date}`;
  if (a.tool === "vault") return "Encrypt it and move it to the vault";
  if (a.tool === "mark_duplicate") return `Mark it as a copy of file ${a.args.of_file_id}`;
  if (a.tool === "flag_for_review") return `Flag for you: ${a.args.reason || ""}`;
  return `${label(a.tool)} (not allowed, blocked)`;
}

async function refreshInbox() {
  const items = await api("/api/inbox");
  $("#inbox-count").textContent = items.length || "";
  $("#inbox").innerHTML = items.map((it) => {
    const fileAct = it.actions.find((a) => a.tool === "file_document");
    const remind = it.actions.find((a) => a.tool === "create_reminder");
    const doable = it.actions.some((a) => a.tool !== "flag_for_review");
    const folders = fileAct ? [...new Set([fileAct.args.folder, ...STATE.folders])] : [];
    return `<div class="inbox-item" data-id="${it.id}">
      <div class="who"><button class="fid link" data-id="${it.file_id}">${esc(it.title || it.name)}</button></div>
      <div class="kind">${esc(kindOf(it.doc_type))}</div>
      <p class="reason">${esc(it.reason.charAt(0).toUpperCase() + it.reason.slice(1))}</p>
      ${it.actions.length ? `<p class="kind">If you approve, I will:</p><ul class="acts">${it.actions.map((a) => `<li>${esc(describeAction(a))}</li>`).join("")}</ul>` : ""}
      ${fileAct ? `<label>Folder <select class="folder">${folders.map((f) => `<option ${f === fileAct.args.folder ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>` : ""}
      ${remind ? `<label>Due date <input type="date" class="due" value="${esc(remind.args.due_date)}"></label>` : ""}
      <div class="btns">
        <button class="approve primary">${doable ? "Approve" : "Got it"}</button>
        ${doable ? `<button class="reject">Leave it as is</button><label class="remember"><input type="checkbox" class="learn" checked> Remember this choice</label>` : ""}
      </div>
    </div>`;
  }).join("") || `<p class="empty">Nothing needs you right now.</p>`;
}

async function approveItem(el) {
  const body = {};
  const folder = $(".folder", el);
  const due = $(".due", el);
  const original = folder && [...folder.options].find((o) => o.defaultSelected)?.value;
  if (folder && folder.value !== original) body.folder = folder.value;
  if (due && due.value && due.value !== due.defaultValue) body.due_date = due.value;
  await post(`/api/inbox/${el.dataset.id}/approve`, body);
  scheduleRefresh();
}

async function rejectItem(el) {
  await post(`/api/inbox/${el.dataset.id}/reject`, { learn: $(".learn", el)?.checked ?? true });
  scheduleRefresh();
}

// ---------- reminders & rules ----------
function daysText(d) {
  if (d === null) return "";
  if (d < 0) return "overdue";
  if (d === 0) return "today";
  return `in ${d} day${d === 1 ? "" : "s"}`;
}

async function refreshReminders() {
  const rs = await api("/api/reminders");
  $("#reminders").innerHTML = rs.map((r) => `<li class="${r.days_left !== null && r.days_left <= 3 ? "soon" : ""}">
      <button class="fid link" data-id="${r.file_id}">${esc(r.title)}</button>
      <span class="when">${esc(r.due_date)}, ${daysText(r.days_left)} <button class="done-reminder small" data-id="${r.id}">Mark paid</button></span></li>`).join("")
    || `<li class="empty">No payments or renewals coming up.</li>`;
}

async function refreshRules() {
  const rs = await api("/api/rules");
  $("#rules").innerHTML = rs.map((r) => `<li><span>${esc(r.text[0].toUpperCase() + r.text.slice(1))} <span class="when">used ${r.hits} time${r.hits === 1 ? "" : "s"}</span></span>
      <button class="forget small" data-id="${r.id}">Forget</button></li>`).join("")
    || `<li class="empty">Move a file to a different folder and I'll remember where that kind of file goes.</li>`;
}

// ---------- export expenses ----------
const iso = (d) => d.toISOString().slice(0, 10);
function setRange(kind) {
  const now = new Date();
  const start = kind === "month" ? new Date(now.getFullYear(), now.getMonth(), 1)
    : kind === "quarter" ? new Date(now.getFullYear(), now.getMonth() - 2, 1) : new Date(now.getFullYear(), 0, 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 2, 0);  // include bills due next month
  $("#exp-start").value = iso(new Date(start.getTime() - start.getTimezoneOffset() * 60000));
  $("#exp-end").value = iso(new Date(end.getTime() - end.getTimezoneOffset() * 60000));
  refreshExpenses();
}

async function refreshExpenses() {
  const q = new URLSearchParams();
  if ($("#exp-start").value) q.set("start", $("#exp-start").value);
  if ($("#exp-end").value) q.set("end", $("#exp-end").value);
  const r = await api(`/api/expenses?${q}`);
  $("#exp-list").innerHTML = r.rows.map((x) => `<li><span><button class="fid link" data-id="${x.file_id}">${esc(x.paid_to)}</button>
      <span class="when">${esc(x.date)}, ${esc(x.type.toLowerCase())}${x.verified ? "" : ", not verified"}</span></span><span>₹${x.amount.toLocaleString("en-IN", { minimumFractionDigits: 2 })}</span></li>`).join("")
    || `<li class="empty">No bills or payments in these dates.</li>`;
  $("#exp-total").textContent = r.rows.length ? `Total ₹${r.total.toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "";
  const dl = $("#exp-download");
  q.set("format", "csv");
  dl.href = `/api/expenses?${q}`;
  dl.setAttribute("aria-disabled", r.rows.length ? "false" : "true");
  dl.textContent = r.rows.length ? `Download ${r.rows.length} as CSV` : "Download CSV";
}
document.addEventListener("change", (e) => { if (e.target.id === "exp-start" || e.target.id === "exp-end") refreshExpenses(); });

// ---------- the pipeline for one file ----------
const STEPS = [
  { id: "sense", name: "Noticed and read the file" },
  { id: "triage", name: "Worked out what it is" },
  { id: "extract", name: "Pulled out the details" },
  { id: "check", name: "Checked the details against the document" },
  { id: "plan", name: "Decided what to do" },
  { id: "gate", name: "Applied your privacy rules" },
  { id: "act", name: "Took action" },
  { id: "confirm", name: "Confirmed the changes on disk" },
  { id: "outcome", name: "Result" },
];

function buildPipeline(d) {
  const steps = Object.fromEntries(STEPS.map((s) => [s.id, { ...s, events: [], notes: [] }]));
  let current = "sense";
  let afterHandoff = false;
  for (const e of d.timeline) {
    let id = null;
    if (e.stage === "sense") id = "sense";
    else if (e.stage === "triage") id = "triage";
    else if (e.stage === "extract") id = "extract";
    else if (e.stage === "verify") id = /confirmed on disk/.test(e.message) ? "confirm" : "check";
    else if (e.stage === "plan" || e.stage === "learn") id = "plan";
    else if (e.stage === "policy") id = "gate";
    else if (e.stage === "act") id = afterHandoff ? "outcome" : "act";
    else if (["handoff", "human", "done", "undo"].includes(e.stage)) { id = "outcome"; if (e.stage === "handoff") afterHandoff = true; }
    if (id) { steps[id].events.push(e); current = id; } else steps[current].notes.push(e);  // recovery, retries, model problems
  }
  const handoff = d.timeline.some((e) => e.stage === "handoff");
  const blocked = steps.gate.events.length > 0;
  if (!blocked && steps.plan.events.length) {
    steps.gate.synthetic = handoff ? "Some actions need your OK, so I held them for you." : "Everything in the plan is allowed to run on its own.";
  }
  let prevEnd = d.timeline.length ? d.timeline[0].ts : 0;
  return STEPS.map((s) => {
    const st = steps[s.id];
    const all = [...st.events, ...st.notes];
    let status = st.events.length || st.synthetic ? "ok" : "skip";
    if (all.some((e) => e.level === "warn")) status = "warn";
    if (all.some((e) => e.level === "error")) status = "bad";
    let secs = null;
    if (st.events.length) {
      const end = Math.max(...st.events.map((e) => e.ts));
      secs = Math.max(0, end - prevEnd);
      prevEnd = end;
    }
    return { ...st, status, secs };
  });
}

function stepBody(step, d) {
  if (step.status === "skip") {
    const why = { act: "Nothing ran on its own for this file.", confirm: "Nothing to confirm.", extract: "Skipped for this file.",
      triage: "Skipped for this file.", check: "Skipped for this file.", plan: "Skipped for this file." };
    return `<p>${why[step.id] || "Not reached yet."}</p>`;
  }
  const lines = step.events.map((e) => {
    const model = (e.message.match(/gemma\d[\w.]*:[a-z0-9]+/i) || [])[0];
    const text = e.message.replace(/ sure, gemma\d[\w.]*:[a-z0-9]+\)/i, " sure)").replace(/ with gemma\d[\w.]*:[a-z0-9]+/i, "");
    return `<p>${esc(text)}${model ? `<span class="model">${esc(model)}</span>` : ""}</p>`;
  });
  if (step.synthetic) lines.push(`<p>${esc(step.synthetic)}</p>`);
  if (step.id === "check" && d.checks.length) {
    lines.push(`<ul class="checks">${d.checks.map((c) => `<li class="${c.ok ? "ok" : "fail"}">${esc(c.detail)}</li>`).join("")}</ul>`);
  }
  step.notes.forEach((n) => lines.push(`<p class="note">${esc(n.message)}</p>`));
  return lines.join("");
}

function fmtSecs(s) {
  if (s === null) return "";
  return s < 1 ? "under 1s" : `${s.toFixed(1)}s`;
}

// ---------- drawer ----------
function previewTag(url, name) {
  const ext = name.split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif"].includes(ext)) return `<img class="preview" src="${url}" alt="Preview of ${esc(name)}">`;
  if (["pdf", "txt", "md", "csv"].includes(ext)) return `<iframe class="preview" src="${url}" title="Preview of ${esc(name)}"></iframe>`;
  return `<div class="locked">No preview for this kind of file.</div>`;
}

async function openDrawer(id, reveal = false) {
  const d = reveal ? await post(`/api/files/${id}/reveal`) : await api(`/api/files/${id}`);
  const preview = d.vaulted
    ? (reveal ? previewTag(`/api/files/${id}/preview?reveal=true`, d.original_name) : `<div class="locked">Encrypted in the vault. Reveal it to view.</div>`)
    : previewTag(`/api/files/${id}/preview`, d.name);
  const masked = d.vaulted || JSON.stringify(d.fields).includes("•");
  const [state, cls] = stateOf(d);
  const pipeline = buildPipeline(d);
  const total = d.timeline.length > 1 ? d.timeline[d.timeline.length - 1].ts - d.timeline[0].ts : 0;
  $("#drawer").innerHTML = `<button class="close" aria-label="Close">Close</button>
    <div class="kind">${esc(kindOf(d.doc_type))}${d.confidence != null ? `, ${Math.round(d.confidence * 100)}% sure` : ""}</div>
    <h2 class="title">${esc(d.title || d.name)}</h2>
    <div class="card-state state ${cls}">${esc(state)}</div>
    ${d.summary ? `<p>${esc(d.summary)}</p>` : ""}

    <h3>How I handled it${total ? ` <span class="step-time">${fmtSecs(total)} in total</span>` : ""}</h3>
    <ol class="pipeline">${pipeline.map((s) => `<li class="${s.status}">
        <div class="step-head"><span class="step-name">${esc(s.name)}</span><span class="step-time">${fmtSecs(s.secs)}</span></div>
        <div class="step-body">${stepBody(s, d)}</div></li>`).join("")}</ol>

    <h3>The file</h3>
    ${preview}
    ${Object.keys(d.fields).length ? `<dl class="kv" style="margin-top:12px">${Object.entries(d.fields).map(([k, v]) => `<dt>${esc(label(k))}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl>` : ""}
    ${!reveal && masked ? `<button class="reveal" data-id="${id}">Reveal protected values</button>` : ""}
    <p class="where">Now at <b>${esc(d.location)}</b><br>Originally ${esc(d.original_location)}</p>
    ${d.vaulted ? "" : `<div class="row"><label>Move to <select class="refile"><option value="" selected disabled>Choose a folder</option>${STATE.folders.map((f) => `<option>${esc(f)}</option>`).join("")}</select></label>
      <button class="do-refile small" data-id="${id}">Move</button></div>`}
    ${d.journal.length ? `<h3>Changes I made</h3><ul class="list">${d.journal.map((j) => `<li><span>${esc(describeJournal(j))} <span class="when">${esc(j.status === "applied" ? "done" : j.status)}</span></span>${j.status === "applied" ? `<button class="undo small" data-id="${j.id}">Undo</button>` : ""}</li>`).join("")}</ul>` : ""}`;
  $("#drawer").classList.remove("hidden");
  $("#scrim").classList.remove("hidden");
  $(".close", $("#drawer")).focus();
}

function describeJournal(j) {
  if (j.tool === "file_document") return `Moved to ${j.args.dest.split("/library/").pop()}`;
  if (j.tool === "vault") return "Encrypted into the vault";
  if (j.tool === "create_reminder") return `Reminder: ${j.args.title}, by ${j.args.due_date}`;
  if (j.tool === "mark_duplicate") return `Marked as a copy of file ${j.args.of_file_id}`;
  return label(j.tool);
}

const closeDrawer = () => { $("#drawer").classList.add("hidden"); $("#scrim").classList.add("hidden"); };

// ---------- ask ----------
$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-q").value.trim();
  if (!q) return;
  const box = $("#answer");
  box.classList.remove("hidden");
  box.innerHTML = `<p class="hint">Looking through your files on this laptop…</p>`;
  try {
    const r = await post("/api/ask", { question: q });
    box.innerHTML = `<div class="ans ${r.sensitive ? "blur" : ""}" title="${r.sensitive ? "Click to reveal" : ""}">${esc(r.answer)}</div>
      ${r.sensitive ? `<p class="hint">This answer contains a secret. Click it to reveal.</p>` : ""}
      ${r.grounded === false ? `<p class="hint">I couldn't point to the exact file for this, so check the sources below.</p>` : ""}
      ${r.files && r.files.length ? `<div class="cites">From: ${r.files.map((f) => `<button class="fid link" data-id="${f.id}">${esc(f.title || f.name)}</button>`).join(" ")}</div>` : ""}`;
  } catch (err) {
    box.innerHTML = `<p class="hint">${esc(err.message)}</p>`;
  }
});

// ---------- clicks & keys ----------
document.addEventListener("click", async (e) => {
  if (e.target.id === "scrim") return closeDrawer();
  const t = e.target.closest("button, .card, .blur");
  if (!t) return;
  const has = (c) => t.classList.contains(c);
  try {
    if (has("blur")) return t.classList.remove("blur");
    if (has("fid") || has("card")) return openDrawer(t.dataset.id);
    if (has("preset")) return setRange(t.dataset.range);
    if (has("tab")) { currentTab = t.dataset.tab; return refreshLibrary(); }
    if (has("approve")) return approveItem(t.closest(".inbox-item"));
    if (has("reject")) return rejectItem(t.closest(".inbox-item"));
    if (has("done-reminder")) { await post(`/api/reminders/${t.dataset.id}/done`); return scheduleRefresh(); }
    if (has("forget")) { await api(`/api/rules/${t.dataset.id}`, { method: "DELETE" }); return scheduleRefresh(); }
    if (has("close")) return closeDrawer();
    if (has("reveal")) return openDrawer(t.dataset.id, true);
    if (has("undo")) { await post(`/api/journal/${t.dataset.id}/undo`); closeDrawer(); return scheduleRefresh(); }
    if (has("do-refile")) {
      const folder = $(".refile").value;
      if (!folder) return alert("Choose a folder first.");
      await post(`/api/files/${t.dataset.id}/refile`, { folder });
      closeDrawer();
      return scheduleRefresh();
    }
  } catch (err) {
    alert(err.message);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
  if (e.key === "Enter" && e.target.classList?.contains("card")) openDrawer(e.target.dataset.id);
});

setRange("quarter");
refreshAll();
startFeed();
setInterval(refreshState, 3000);
