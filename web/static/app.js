// Tiny vanilla-JS controller for the AI Datacenters table.

const statusEl = document.getElementById("status");
const discoverBtn = document.getElementById("discoverBtn");
const tbody = document.querySelector("#projectsTable tbody");

function setStatus(msg, kind) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function fmtMW(v) {
  if (v === null || v === undefined || v === "") return "";
  return Math.round(v).toLocaleString();
}

function rowHtml(p) {
  return `
    <td class="name">${escapeHtml(p.name)}</td>
    <td>${escapeHtml(p.owner || "")}</td>
    <td>${escapeHtml(p.location || "")}</td>
    <td class="num">${fmtMW(p.claimed_mw)}</td>
    <td class="status-cell">${escapeHtml(p.last_known_status || "")}</td>
    <td class="summary-cell">${escapeHtml(p.latest_summary || "")}</td>
    <td class="ts-cell">${(p.last_checked_at || "").slice(0, 10)}</td>
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
    tr.innerHTML = `<td colspan="8">No projects yet. Click <strong>Discover projects</strong>.</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const p of projects) {
    const tr = document.createElement("tr");
    tr.dataset.slug = p.slug;
    tr.innerHTML = rowHtml(p);
    tbody.appendChild(tr);
  }
}

function updateRow(p) {
  let tr = tbody.querySelector(`tr[data-slug="${CSS.escape(p.slug)}"]`);
  if (!tr) {
    tr = document.createElement("tr");
    tr.dataset.slug = p.slug;
    tbody.appendChild(tr);
  }
  tr.innerHTML = rowHtml(p);
  tr.classList.remove("updated");
  void tr.offsetWidth;
  tr.classList.add("updated");
}

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
