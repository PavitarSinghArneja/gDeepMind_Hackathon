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

// ---------- live feed ----------
function addEvent(ev) {
  const li = document.createElement("li");
  li.className = `ev lvl-${ev.level} st-${ev.stage}`;
  const time = new Date(ev.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  li.innerHTML = `<span class="t">${time}</span><span class="stage">${esc(ev.stage)}</span><span class="msg">${esc(ev.message)}</span>` +
    (ev.file_id ? `<button class="fid link" data-id="${ev.file_id}">#${ev.file_id}</button>` : "");
  const feed = $("#feed");
  feed.prepend(li);
  while (feed.children.length > 400) feed.lastChild.remove();
}

async function startFeed() {
  const recent = await api("/api/events/recent");
  recent.forEach(addEvent);
  const last = recent.length ? recent[recent.length - 1].id : 0;
  const es = new EventSource(`/api/events?after=${last}`);
  es.onmessage = (m) => { addEvent(JSON.parse(m.data)); scheduleRefresh(); };
}

let refreshTimer;
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshAll, 350); }
function refreshAll() { return Promise.allSettled([refreshState(), refreshLibrary(), refreshInbox(), refreshReminders(), refreshRules()]); }

// ---------- header ----------
async function refreshState() {
  STATE = await api("/api/state");
  const { network, models, tasks, stats } = STATE;
  const offline = network.internet === false;
  const net = $("#pill-net");
  net.textContent = `${offline ? "Offline" : "Online"} · ${network.outbound.length} outbound connections`;
  net.className = `pill ${offline && network.outbound.length === 0 ? "good" : "warn"}`;
  const model = $("#pill-model");
  model.textContent = models.available ? `${models.triage} + ${models.work} on-device` : "Model not running. Files will wait.";
  model.className = `pill ${models.available ? "good" : "bad"}`;
  const waiting = (tasks.queued || 0) + (tasks.running || 0);
  $("#pill-queue").textContent = `${waiting} in queue · ${stats.files} files · ${stats.avg_seconds ?? "–"}s per file`;
}

// ---------- library ----------
const TABS = { all: "All", bill: "Bills", bank_statement: "Statements", payment_receipt: "Receipts", salary_slip: "Salary",
  prescription: "Prescriptions", lab_report: "Lab reports", insurance_card: "Insurance", id_document: "IDs",
  credential: "Secrets", travel_ticket: "Travel", personal_photo: "Photos", other: "Other" };

async function refreshLibrary() {
  const files = await api("/api/files");
  const counts = {};
  files.forEach((f) => { counts[f.doc_type] = (counts[f.doc_type] || 0) + 1; });
  $("#tabs").innerHTML = Object.entries(TABS)
    .filter(([k]) => k === "all" || counts[k])
    .map(([k, name]) => `<button class="tab ${k === currentTab ? "on" : ""}" data-tab="${k}">${name} <small>${k === "all" ? files.length : counts[k]}</small></button>`)
    .join("");
  const shown = files.filter((f) => currentTab === "all" || f.doc_type === currentTab);
  $("#library").innerHTML = shown.map(card).join("") || `<p class="empty">Nothing here yet.</p>`;
}

function card(f) {
  const badges = [
    f.status === "needs_human" && ["needs you", "warn"],
    f.status === "queued" && ["working…", ""],
    f.status === "failed" && ["couldn't read", "bad"],
    f.duplicate_of && [`duplicate of #${f.duplicate_of}`, ""],
    f.vaulted && ["in vault", "good"],
  ].filter(Boolean);
  const kv = Object.entries(f.fields || {}).filter(([, v]) => v && typeof v !== "object").slice(0, 3);
  return `<article class="card sens-${f.sensitivity || "low"}" data-id="${f.id}">
    <div class="type">${esc(label(f.doc_type) || "reading…")}</div>
    <h3>${esc(f.title || f.name)}</h3>
    ${f.summary ? `<p class="sum">${esc(f.summary)}</p>` : ""}
    ${kv.length ? `<dl>${kv.map(([k, v]) => `<dt>${esc(label(k))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    <div class="badges">${badges.map(([b, cls]) => `<span class="badge ${cls}">${esc(b)}</span>`).join("")}</div>
    <div class="path">${esc(f.location)}</div>
  </article>`;
}

// ---------- inbox ----------
function describeAction(a) {
  if (a.tool === "file_document") return `→ ${a.args.folder}/${a.args.name}`;
  if (a.tool === "create_reminder") return `“${a.args.title}” by ${a.args.due_date}`;
  if (a.tool === "vault") return "encrypt it and move it to the vault";
  if (a.tool === "mark_duplicate") return `same as #${a.args.of_file_id}`;
  if (a.tool === "flag_for_review") return a.args.reason || "";
  return "";
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
      <div class="who"><button class="fid link" data-id="${it.file_id}">${esc(it.title || it.name)}</button> <span class="type">${esc(label(it.doc_type))}</span></div>
      <p class="reason">${esc(it.reason)}</p>
      ${it.actions.length ? `<ul class="acts">${it.actions.map((a) => `<li><b>${esc(label(a.tool))}</b> ${esc(describeAction(a))}${a.why ? `<br><small>why: ${esc(a.why)}</small>` : ""}</li>`).join("")}</ul>` : ""}
      ${fileAct ? `<label>Folder <select class="folder">${folders.map((f) => `<option ${f === fileAct.args.folder ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>` : ""}
      ${remind ? `<label>Due <input type="date" class="due" value="${esc(remind.args.due_date)}"></label>` : ""}
      <div class="btns">
        <button class="approve primary">${doable ? "Approve" : "Got it"}</button>
        ${doable ? `<button class="reject">Leave it</button><label class="remember"><input type="checkbox" class="learn" checked> remember</label>` : ""}
      </div>
    </div>`;
  }).join("") || `<p class="empty">All clear. Nothing needs you.</p>`;
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
  return `${d} day${d === 1 ? "" : "s"}`;
}

async function refreshReminders() {
  const rs = await api("/api/reminders");
  $("#reminders").innerHTML = rs.map((r) => `<li class="${r.days_left !== null && r.days_left <= 3 ? "soon" : ""}">
      <button class="fid link" data-id="${r.file_id}">${esc(r.title)}</button>
      <span>${esc(r.due_date)} · ${daysText(r.days_left)}</span>
      <button class="done-reminder small" data-id="${r.id}">done</button></li>`).join("")
    || `<li class="empty">No upcoming payments.</li>`;
}

async function refreshRules() {
  const rs = await api("/api/rules");
  $("#rules").innerHTML = rs.map((r) => `<li><span>#${r.id} ${esc(r.text)} <small class="muted">used ${r.hits}×</small></span>
      <button class="forget small" data-id="${r.id}">forget</button></li>`).join("")
    || `<li class="empty">Correct one of my decisions and I'll remember it.</li>`;
}

// ---------- drawer ----------
function previewTag(url, name) {
  const ext = name.split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif"].includes(ext)) return `<img class="preview" src="${url}" alt="">`;
  if (["pdf", "txt", "md", "csv"].includes(ext)) return `<iframe class="preview" src="${url}"></iframe>`;
  return `<div class="locked">No preview for this file type.</div>`;
}

async function openDrawer(id, reveal = false) {
  const d = reveal ? await post(`/api/files/${id}/reveal`) : await api(`/api/files/${id}`);
  const preview = d.vaulted
    ? (reveal ? previewTag(`/api/files/${id}/preview?reveal=true`, d.original_name) : `<div class="locked">Encrypted in the vault.</div>`)
    : previewTag(`/api/files/${id}/preview`, d.name);
  const masked = d.vaulted || JSON.stringify(d.fields).includes("•");
  $("#drawer").innerHTML = `<button class="close">×</button>
    <div class="type">${esc(label(d.doc_type))} · ${esc(d.sensitivity || "")} sensitivity${d.confidence != null ? ` · ${Math.round(d.confidence * 100)}% sure` : ""}</div>
    <h2>${esc(d.title || d.name)}</h2>
    <p class="sum">${esc(d.summary || "")}</p>
    ${preview}
    ${Object.keys(d.fields).length ? `<h4>Details</h4><dl>${Object.entries(d.fields).map(([k, v]) => `<dt>${esc(label(k))}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl>` : ""}
    ${!reveal && masked ? `<button class="reveal" data-id="${id}">Reveal protected values</button>` : ""}
    ${d.checks.length ? `<h4>Checks</h4><ul class="checks">${d.checks.map((c) => `<li class="${c.ok ? "ok" : "fail"}">${c.ok ? "✓" : "✗"} ${esc(c.detail)}</li>`).join("")}</ul>` : ""}
    <h4>Where it is</h4><p class="path">${esc(d.location)}<br><small>originally ${esc(d.original_location)}</small></p>
    ${d.vaulted ? "" : `<label>Move to <select class="refile"><option value="" selected disabled>Choose a folder…</option>${STATE.folders.map((f) => `<option>${esc(f)}</option>`).join("")}</select></label> <button class="do-refile small" data-id="${id}">Move</button>`}
    ${d.journal.length ? `<h4>What I did</h4><ul class="rules">${d.journal.map((j) => `<li><span>${esc(label(j.tool))} · ${esc(j.status)}</span>${j.status === "applied" ? `<button class="undo small" data-id="${j.id}">undo</button>` : ""}</li>`).join("")}</ul>` : ""}
    <h4>Timeline</h4><ol class="timeline">${d.timeline.map((e) => `<li class="lvl-${e.level}"><span class="stage">${esc(e.stage)}</span> ${esc(e.message)}</li>`).join("")}</ol>`;
  $("#drawer").classList.remove("hidden");
}
const closeDrawer = () => $("#drawer").classList.add("hidden");

// ---------- ask ----------
$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-q").value.trim();
  if (!q) return;
  const box = $("#answer");
  box.classList.remove("hidden");
  box.innerHTML = `<p class="muted">Thinking on-device…</p>`;
  try {
    const r = await post("/api/ask", { question: q });
    box.innerHTML = `<div class="ans ${r.sensitive ? "blur" : ""}" title="${r.sensitive ? "Click to reveal" : ""}">${esc(r.answer)}</div>
      ${r.grounded === false ? `<p class="warn">I couldn't tie this to a specific file.</p>` : ""}
      ${r.files && r.files.length ? `<div class="cites">Sources: ${r.files.map((f) => `<button class="fid link" data-id="${f.id}">${esc(f.title || f.name)}</button>`).join(" ")}</div>` : ""}`;
  } catch (err) {
    box.innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
});

// ---------- clicks ----------
document.addEventListener("click", async (e) => {
  const t = e.target.closest("button, .card, .blur");
  if (!t) return;
  const has = (c) => t.classList.contains(c);
  try {
    if (has("blur")) return t.classList.remove("blur");
    if (has("fid") || has("card")) return openDrawer(t.dataset.id);
    if (has("tab")) { currentTab = t.dataset.tab; return refreshLibrary(); }
    if (has("approve")) return approveItem(t.closest(".inbox-item"));
    if (has("reject")) return rejectItem(t.closest(".inbox-item"));
    if (has("done-reminder")) { await post(`/api/reminders/${t.dataset.id}/done`); return scheduleRefresh(); }
    if (has("forget")) { await api(`/api/rules/${t.dataset.id}`, { method: "DELETE" }); return scheduleRefresh(); }
    if (has("close")) return closeDrawer();
    if (has("reveal")) return openDrawer(t.dataset.id, true);
    if (has("undo")) { await post(`/api/journal/${t.dataset.id}/undo`); closeDrawer(); return scheduleRefresh(); }
    if (has("do-refile")) { if (!$(".refile").value) return alert("Choose a folder first."); await post(`/api/files/${t.dataset.id}/refile`, { folder: $(".refile").value }); closeDrawer(); return scheduleRefresh(); }
  } catch (err) {
    alert(err.message);
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

refreshAll();
startFeed();
setInterval(refreshState, 3000);
