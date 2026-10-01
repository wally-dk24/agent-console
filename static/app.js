"use strict";
const $ = (id) => document.getElementById(id);
const state = { path: "", history: [], ftype: "" };

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-json */ }
  return { ok: res.ok, status: res.status, data };
}
const get = (url) => api("GET", url);
const post = (url, body) => api("POST", url, body);

function show(id) {
  for (const v of ["view-setup", "view-enroll", "view-login", "view-app"])
    $(v).classList.toggle("hidden", v !== id);
}

/* ---------- 6-digit code boxes ---------- */
function buildCodeBoxes(el) {
  el.innerHTML = "";
  const inputs = [];
  for (let i = 0; i < 6; i++) {
    const inp = document.createElement("input");
    inp.inputMode = "numeric"; inp.maxLength = 1; inp.autocomplete = "one-time-code";
    inp.addEventListener("input", () => {
      inp.value = inp.value.replace(/\D/g, "").slice(0, 1);
      if (inp.value && i < 5) inputs[i + 1].focus();
      onCodeChange(el);
    });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !inp.value && i > 0) inputs[i - 1].focus();
    });
    inp.addEventListener("paste", (e) => {
      const t = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
      if (t) {
        e.preventDefault();
        t.split("").forEach((ch, j) => { if (inputs[j]) inputs[j].value = ch; });
        inputs[Math.min(t.length, 5)].focus();
        onCodeChange(el);
      }
    });
    el.appendChild(inp); inputs.push(inp);
  }
}
function codeValue(el) {
  return [...el.querySelectorAll("input")].map((i) => i.value).join("");
}
function onCodeChange(el) {
  if (el.dataset.target === "enroll")
    $("enroll-finish").disabled = codeValue(el).length !== 6;
}

/* ---------- enrollment ---------- */
async function boot() {
  buildCodeBoxes($("enroll-code"));
  buildCodeBoxes($("login-code"));
  const r = await get("/api/auth-state");
  if (!r.data.configured) show("view-setup");
  else show("view-login");
}

$("setup-begin").addEventListener("click", async () => {
  const pw = $("setup-password").value;
  $("setup-error").textContent = "";
  if (pw.length < 12) { $("setup-error").textContent = "Use at least 12 characters."; return; }
  const r = await post("/api/enroll", { password: pw });
  if (!r.ok) { $("setup-error").textContent = r.data.error || "Enrollment failed."; return; }
  $("enroll-key").textContent = r.data.setup_key;
  show("view-enroll");
});

$("enroll-copy").addEventListener("click", async () => {
  const key = $("enroll-key").textContent;
  try { await navigator.clipboard.writeText(key); }
  catch (e) {
    const ta = document.createElement("textarea");
    ta.value = key; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  $("enroll-copy").textContent = "✓";
});

$("enroll-finish").addEventListener("click", async () => {
  const code = codeValue($("enroll-code"));
  $("enroll-error").textContent = "";
  const r = await post("/api/enroll/verify", { code });
  if (!r.ok) { $("enroll-error").textContent = r.data.error || "Verification failed."; return; }
  enterApp();
});

/* ---------- login ---------- */
$("login-go").addEventListener("click", async () => {
  const r = await post("/api/login", {
    password: $("login-password").value,
    code: codeValue($("login-code")),
  });
  if (!r.ok) { $("login-error").textContent = r.data.error || "Sign in failed."; return; }
  $("login-password").value = "";
  enterApp();
});
$("logout").addEventListener("click", async () => {
  await post("/api/logout", {});
  location.reload();
});

/* ---------- tabs ---------- */
document.querySelectorAll("button.tab").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("button.tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    for (const t of ["files", "activity", "fleet"])
      $("tab-" + t).classList.toggle("hidden", t !== b.dataset.tab);
    if (b.dataset.tab === "activity") loadActivity();
    if (b.dataset.tab === "fleet") loadFleet();
  });
});

async function enterApp() {
  show("view-app");
  await loadTree("");
  renderRecents();
}

/* ---------- file tree ---------- */
function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function fmtTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
}

async function loadTree(path, push = true) {
  if (push && state.path !== path) state.history.push(state.path);
  state.path = path;
  const r = await get("/api/tree?path=" + encodeURIComponent(path));
  if (!r.ok) return;
  const d = r.data;
  const crumbs = $("crumbs");
  crumbs.innerHTML = "";
  d.breadcrumbs.forEach((c, i) => {
    if (i > 0) { const s = document.createElement("span"); s.className = "sep"; s.textContent = "/"; crumbs.appendChild(s); }
    const b = document.createElement("button");
    b.textContent = c.name;
    if (i === d.breadcrumbs.length - 1) b.classList.add("current");
    b.addEventListener("click", () => loadTree(c.path));
    crumbs.appendChild(b);
  });
  const ul = $("dir-list");
  ul.innerHTML = "";
  const addRow = (icon, name, sub, fn) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    const ic = document.createElement("span"); ic.textContent = icon;
    const nm = document.createElement("span"); nm.className = "fname mono"; nm.textContent = name;
    const mt = document.createElement("span"); mt.className = "fmeta"; mt.textContent = sub;
    b.append(ic, nm, mt); b.addEventListener("click", fn); li.appendChild(b); ul.appendChild(li);
  };
  d.dirs.forEach((x) => addRow("📁", x.name, "", () => loadTree(x.path)));
  d.files.forEach((x) => addRow("📄", x.name, fmtSize(x.size), () => openPreview(x.path)));
  $("nav-back").disabled = state.history.length === 0;
}

$("nav-back").addEventListener("click", () => {
  const prev = state.history.pop();
  if (prev !== undefined) loadTree(prev, false);
});
$("nav-up").addEventListener("click", () => {
  const parts = state.path.split("/").filter(Boolean);
  parts.pop();
  loadTree(parts.join("/"));
});

/* ---------- search ---------- */
let searchTimer = null;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 180);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("search")
      && !$("view-app").classList.contains("hidden")) {
    e.preventDefault(); $("search").focus();
  }
  if (e.key === "Escape") closePreview();
});
document.querySelectorAll("#typefilters .chip").forEach((c) => {
  c.addEventListener("click", () => {
    document.querySelectorAll("#typefilters .chip").forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
    state.ftype = c.dataset.type;
    runSearch();
  });
});

async function runSearch() {
  const q = $("search").value.trim();
  const box = $("search-results");
  if (!q) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const r = await get("/api/search?q=" + encodeURIComponent(q)
    + "&type=" + encodeURIComponent(state.ftype));
  if (!r.ok) return;
  box.innerHTML = "";
  const ul = document.createElement("ul");
  if (!r.data.results.length) {
    const li = document.createElement("li");
    li.innerHTML = '<div class="muted" style="padding:10px 12px">No matches.</div>';
    ul.appendChild(li);
  }
  r.data.results.forEach((x) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    const nm = document.createElement("span"); nm.className = "fname mono"; nm.textContent = x.path;
    const mt = document.createElement("span"); mt.className = "fmeta"; mt.textContent = fmtSize(x.size);
    b.append(nm, mt);
    b.addEventListener("click", () => {
      const dir = x.path.split("/").slice(0, -1).join("/");
      loadTree(dir); openPreview(x.path);
      box.classList.add("hidden");
    });
    li.appendChild(b); ul.appendChild(li);
  });
  box.appendChild(ul);
  box.classList.remove("hidden");
}

/* ---------- preview + recents ---------- */
function getRecents() {
  try { return JSON.parse(localStorage.getItem("ac_recent") || "[]"); } catch (e) { return []; }
}
function pushRecent(path) {
  const r = [path, ...getRecents().filter((p) => p !== path)].slice(0, 10);
  try { localStorage.setItem("ac_recent", JSON.stringify(r)); } catch (e) {}
  renderRecents();
}
function renderRecents() {
  const r = getRecents();
  $("recent").classList.toggle("hidden", r.length === 0);
  const ul = $("recent-list");
  ul.innerHTML = "";
  r.forEach((p) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    const nm = document.createElement("span"); nm.className = "fname mono"; nm.textContent = p;
    b.appendChild(nm);
    b.addEventListener("click", () => {
      const dir = p.split("/").slice(0, -1).join("/");
      loadTree(dir); openPreview(p);
    });
    li.appendChild(b); ul.appendChild(li);
  });
}

async function openPreview(path) {
  const r = await get("/api/file?path=" + encodeURIComponent(path));
  if (!r.ok) return;
  const d = r.data;
  $("preview-path").textContent = d.path;
  $("preview-path").title = d.path;
  $("preview-sub").textContent =
    [fmtSize(d.size), fmtTime(d.modified_at)].filter(Boolean).join(" · ")
    || "metadata unavailable";
  $("preview-body").textContent = d.preview_available
    ? d.content : "(preview not synced for this file)";
  $("preview").classList.remove("hidden");
  $("preview").dataset.path = d.path;
  pushRecent(d.path);
}
function closePreview() { $("preview").classList.add("hidden"); }
$("preview-back").addEventListener("click", closePreview);
$("preview-copy").addEventListener("click", async () => {
  const p = $("preview").dataset.path || "";
  try { await navigator.clipboard.writeText(p); } catch (e) {}
  $("preview-copy").textContent = "✓ path";
  setTimeout(() => { $("preview-copy").textContent = "⧉ path"; }, 1200);
});

/* ---------- activity + fleet ---------- */
async function loadActivity() {
  const r = await get("/api/activity");
  const ul = $("activity-list");
  ul.innerHTML = "";
  const events = r.ok ? r.data.events : [];
  if (!events.length)
    ul.innerHTML = '<li class="muted">No activity events yet.</li>';
  events.forEach((e) => {
    const li = document.createElement("li");
    const head = document.createElement("div");
    head.innerHTML = `<strong></strong> <span class="ekey"></span>`;
    head.querySelector("strong").textContent = e.summary || e.action || "event";
    head.querySelector(".ekey").textContent =
      [e.ts, e.category, e.outcome].filter(Boolean).join(" · ");
    li.appendChild(head);
    ul.appendChild(li);
  });
}

async function loadFleet() {
  const r = await get("/api/fleet");
  const box = $("fleet-cards");
  box.innerHTML = "";
  if (!r.ok || !r.data.available) {
    box.innerHTML = '<div class="muted">No fleet snapshot yet.</div>';
    return;
  }
  const d = r.data;
  const stats = [
    [d.cron_healthy + "/" + d.cron_total, "schedules healthy"],
    [(d.quota_used ?? "?") + "%", "quota used"],
    [d.active_workers ?? 0, "active workers"],
    [d.pending_approvals ?? 0, "pending approvals"],
  ];
  stats.forEach(([v, k]) => {
    const s = document.createElement("div");
    s.className = "stat";
    const vv = document.createElement("div"); vv.className = "v"; vv.textContent = v;
    const kk = document.createElement("div"); kk.className = "k"; kk.textContent = k;
    s.append(vv, kk); box.appendChild(s);
  });
  if (d.captured_at) {
    const c = document.createElement("div");
    c.className = "muted small";
    c.textContent = "Snapshot: " + fmtTime(d.captured_at);
    box.appendChild(c);
  }
}

boot();
