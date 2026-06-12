// Tiny vanilla-JS controller for the AI Datacenters table.

const statusEl = document.getElementById("status");
const discoverBtn = document.getElementById("discoverBtn");
const tbody = document.querySelector("#projectsTable tbody");
const ownerFilter = document.getElementById("ownerFilter");
const locationFilter = document.getElementById("locationFilter");
const filterCount = document.getElementById("filterCount");

function setStatus(msg, kind) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function fmtMW(v) {
  if (v === null || v === undefined || v === "") return "";
  return Math.round(v).toLocaleString();
}

function fmtDate(v) {
  return (v || "").slice(0, 10);
}

function rowHtml(p) {
  return `
    <td class="name">${escapeHtml(p.name)}</td>
    <td>${escapeHtml(p.owner || "")}</td>
    <td>${escapeHtml(p.location || "")}</td>
    <td class="num">${fmtMW(p.claimed_mw)}</td>
    <td class="status-cell">${escapeHtml(p.last_known_status || "")}</td>
    <td class="ts-cell">${fmtDate(p.first_announcement_date)}</td>
    <td class="ts-cell live-cell">${fmtDate(p.live_date)}</td>
    <td class="summary-cell">${escapeHtml(p.latest_summary || "")}</td>
    <td class="ts-cell">${fmtDate(p.last_checked_at)}</td>
    <td><button class="checkBtn" data-slug="${p.slug}">Check now</button></td>
  `;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function renderProjects(projects) {
  tbody.innerHTML = "";
  if (!projects || projects.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    tr.innerHTML = `<td colspan="10">No projects yet. Click <strong>Discover projects</strong>.</td>`;
    tbody.appendChild(tr);
    refreshFilterOptions();
    applyFilters();
    return;
  }
  for (const p of projects) {
    const tr = document.createElement("tr");
    tr.dataset.slug = p.slug;
    tr.dataset.owner = p.owner || "";
    tr.dataset.location = p.location || "";
    tr.innerHTML = rowHtml(p);
    tbody.appendChild(tr);
  }
  refreshFilterOptions();
  applyFilters();
}

function updateRow(p) {
  let tr = tbody.querySelector(`tr[data-slug="${CSS.escape(p.slug)}"]`);
  if (!tr) {
    tr = document.createElement("tr");
    tr.dataset.slug = p.slug;
    tbody.appendChild(tr);
  }
  tr.dataset.owner = p.owner || "";
  tr.dataset.location = p.location || "";
  tr.innerHTML = rowHtml(p);
  tr.classList.remove("updated");
  void tr.offsetWidth;
  tr.classList.add("updated");
  refreshFilterOptions();
  applyFilters();
}

function refreshFilterOptions() {
  const owners = new Set();
  const locations = new Set();
  for (const tr of tbody.querySelectorAll("tr[data-slug]")) {
    const o = tr.dataset.owner;
    const l = tr.dataset.location;
    if (o) owners.add(o);
    if (l) locations.add(l);
  }
  syncSelect(ownerFilter, [...owners].sort((a, b) => a.localeCompare(b)));
  syncSelect(locationFilter, [...locations].sort((a, b) => a.localeCompare(b)));
}

function syncSelect(sel, values) {
  if (!sel) return;
  const current = sel.value;
  const placeholder = sel.querySelector('option[value=""]');
  const placeholderText = placeholder ? placeholder.textContent : "All";
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = placeholderText;
  sel.appendChild(all);
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  }
  if (values.includes(current)) sel.value = current;
}

function applyFilters() {
  if (!ownerFilter || !locationFilter) return;
  const o = ownerFilter.value;
  const l = locationFilter.value;
  let shown = 0;
  let total = 0;
  for (const tr of tbody.querySelectorAll("tr[data-slug]")) {
    total += 1;
    const matchOwner = !o || tr.dataset.owner === o;
    const matchLoc = !l || tr.dataset.location === l;
    const match = matchOwner && matchLoc;
    tr.hidden = !match;
    if (match) shown += 1;
  }
  if (filterCount) {
    if (total === 0) {
      filterCount.textContent = "";
    } else if (shown === total) {
      filterCount.textContent = `${total} project${total === 1 ? "" : "s"}`;
    } else {
      filterCount.textContent = `${shown} of ${total} projects`;
    }
  }
}

if (ownerFilter) ownerFilter.addEventListener("change", applyFilters);
if (locationFilter) locationFilter.addEventListener("change", applyFilters);
refreshFilterOptions();
applyFilters();

discoverBtn.addEventListener("click", async () => {
  if (!confirm("Ask Grok to discover the largest AI datacenters? This will use ~$0.05-0.20 of credits.")) return;
  discoverBtn.disabled = true;
  setStatus("Asking Grok to find the biggest projects (web search enabled, ~30-60s)...");
  try {
    const r = await fetch("/api/discover", { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    renderProjects(data.projects);
    setStatus(
      `Added ${data.added} new project(s). Total tracked: ${data.total}. ` +
      `Cost: $${(data.cost_usd || 0).toFixed(4)}.`,
      "ok"
    );
  } catch (e) {
    setStatus("Discover failed: " + e.message, "err");
  } finally {
    discoverBtn.disabled = false;
  }
});

tbody.addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".checkBtn");
  if (!btn) return;
  const slug = btn.dataset.slug;
  btn.disabled = true;
  btn.textContent = "Checking...";
  setStatus(`Asking Grok about "${slug}" (~15-40s)...`);
  try {
    const r = await fetch(`/api/check/${encodeURIComponent(slug)}`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    updateRow(data.project);
    const citCount = (data.citations || []).length;
    setStatus(
      `Updated "${slug}". ${citCount} citation(s). Cost: $${(data.cost_usd || 0).toFixed(4)}.`,
      "ok"
    );
  } catch (e) {
    setStatus(`Check failed for ${slug}: ${e.message}`, "err");
    btn.disabled = false;
    btn.textContent = "Check now";
  }
});
