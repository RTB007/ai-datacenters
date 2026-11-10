// Dashboard above the table: stat tiles + four small charts, plain SVG.
// Renders from window.PROJECTS and follows the table filters (renderCharts(slugs)).

(function () {
  const NS = "http://www.w3.org/2000/svg";

  // Permit verdict = a status, so status colours (good / warning / critical) plus a
  // light blue for "in process" and a neutral grey for "nothing found". Checked with
  // the dataviz palette validator (adjacent pairs clear the CVD and normal-vision floors).
  const VERDICTS = [
    ["fully_permitted", "Fully permitted", "#0ca30c"],
    ["partially_permitted", "Partially permitted", "#fab219"],
    ["applied", "Applied / pending", "#5598e7"],
    ["none_found", "No permits found", "#898781"],
    ["blocked", "Blocked / denied", "#d03b3b"],
  ];
  const PERMIT_TYPES = [
    ["zoning", "Zoning / land use"],
    ["building", "Building"],
    ["power", "Power / grid"],
    ["air", "Air quality"],
    ["water", "Water"],
    ["environmental", "Environmental"],
  ];
  const TYPE_STATUS = [
    ["granted", "Granted", "#0ca30c"],
    ["pending", "Pending", "#fab219"],
    ["denied", "Denied", "#d03b3b"],
    ["not_found", "Not found / not reported", "#898781"],
  ];
  const SERIES = "#2a78d6";

  // Developer groups: first match wins (a builder before its tenant).
  const OWNER_GROUPS = [
    [/openai|stargate/i, "OpenAI / Stargate"], [/crusoe/i, "Crusoe"], [/nscale/i, "Nscale"],
    [/iren/i, "IREN"], [/coreweave/i, "CoreWeave"], [/applied digital/i, "Applied Digital"],
    [/nebius/i, "Nebius"], [/lambda/i, "Lambda"], [/xai/i, "xAI"], [/google/i, "Google"],
    [/meta/i, "Meta"], [/microsoft/i, "Microsoft"], [/amazon|aws/i, "Amazon"],
  ];

  function ownerGroup(o) {
    for (const [re, name] of OWNER_GROUPS) if (re.test(o || "")) return name;
    return o || "Unknown";
  }

  function region(loc) {
    const parts = (loc || "").split(",").map(s => s.trim()).filter(Boolean);
    if (!parts.length) return "Unknown";
    const country = parts[parts.length - 1];
    if (/^(usa|us|united states)$/i.test(country)) return parts.length > 1 ? parts[parts.length - 2] : "USA (unspecified)";
    return country;
  }

  function verdictOf(p) {
    return VERDICTS.some(v => v[0] === p.permit_status) ? p.permit_status : null;
  }

  function fmtCap(mw) {
    if (!mw) return "0 MW";
    return mw >= 1000 ? (mw / 1000).toFixed(mw >= 10000 ? 0 : 1) + " GW" : Math.round(mw) + " MW";
  }

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function txt(tag, cls, text, parent) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  // Rect with a 4px rounded data-end on the right, square at the baseline.
  function barPath(x, y, w, h, round) {
    const r = round ? Math.min(4, w, h / 2) : 0;
    return `M${x},${y}h${w - r}` + (r ? `a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}` : `v${h}`) + `h${-(w - r)}z`;
  }
  function colPath(x, y, w, h) {   // vertical: rounded top
    const r = Math.min(4, w / 2, h);
    return `M${x},${y + h}v${-(h - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z`;
  }

  // ── tooltip ────────────────────────────────────────────────────────────────
  const tip = document.createElement("div");
  tip.className = "viz-tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  function showTip(ev, title, rows) {
    tip.replaceChildren();
    txt("div", "viz-tip-title", title, tip);
    for (const [value, label, color] of rows) {
      const r = txt("div", "viz-tip-row", "", tip);
      const key = txt("span", "viz-tip-key", "", r);
      key.style.background = color || "transparent";
      txt("strong", "", value, r);
      txt("span", "viz-tip-label", label, r);
    }
    tip.hidden = false;
    const b = ev.currentTarget.getBoundingClientRect();
    const x = ev.clientX !== undefined && ev.type !== "focus" ? ev.clientX : b.left + b.width / 2;
    const y = ev.clientY !== undefined && ev.type !== "focus" ? ev.clientY : b.top;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - tw - 8, x + 12)) + "px";
    tip.style.top = Math.max(8, y - th - 12) + "px";
  }
  function hideTip() { tip.hidden = true; }

  function hover(target, title, rows) {
    target.setAttribute("tabindex", "0");
    target.setAttribute("aria-label", title + ": " + rows.map(r => r[1] + " " + r[0]).join(", "));
    const on = ev => showTip(ev, title, rows);
    target.addEventListener("pointermove", on);
    target.addEventListener("focus", on);
    target.addEventListener("pointerleave", hideTip);
    target.addEventListener("blur", hideTip);
  }

  function legend(parent, items) {
    const lg = txt("div", "viz-legend", "", parent);
    for (const [label, color, extra] of items) {
      const it = txt("span", "viz-legend-item", "", lg);
      txt("span", "viz-swatch", "", it).style.background = color;
      txt("span", "", label, it);
      if (extra) txt("span", "viz-legend-extra", extra, it);
    }
  }

  // ── stacked horizontal bars ─────────────────────────────────────────────────
  // rows: [{label, segs: {key: value}, total, count?}]; keys: [[key, label, color]]
  function hbars(box, rows, keys, fmt, unitLabel) {
    const W = box.clientWidth || 360;
    const labelW = Math.min(150, Math.max(100, W * 0.32));
    const valueW = 72, rowH = 28, barH = 18, gap = 2;
    const plotW = Math.max(60, W - labelW - valueW);
    const max = Math.max(1, ...rows.map(r => r.total));
    const svg = el("svg", { width: W, height: rows.length * rowH + 4, role: "img" }, box);
    rows.forEach((r, i) => {
      const y = i * rowH + (rowH - barH) / 2;
      const g = el("g", { class: "viz-row" }, svg);
      const lab = el("text", { x: labelW - 8, y: y + barH / 2, class: "viz-cat", "text-anchor": "end", "dominant-baseline": "middle" }, g);
      lab.textContent = r.label.length > 22 ? r.label.slice(0, 21) + "…" : r.label;
      const present = keys.filter(k => (r.segs[k[0]] || 0) > 0);
      let x = labelW;
      present.forEach((k, j) => {
        const w = (r.segs[k[0]] / max) * plotW;
        const last = j === present.length - 1;
        const drawW = Math.max(0.5, last ? w : w - gap);
        el("path", { d: barPath(x, y, drawW, barH, last), fill: k[2], class: "viz-mark" }, g);
        x += w;
      });
      const v = el("text", { x: x + 6, y: y + barH / 2, class: "viz-val", "dominant-baseline": "middle" }, g);
      v.textContent = r.valueLabel || fmt(r.total);
      // the hit target is the whole row, not just the painted pixels
      const hit = el("rect", { x: 0, y: i * rowH, width: W, height: rowH, fill: "transparent", class: "viz-hit" }, g);
      hover(hit, r.label, present.map(k => [fmt(r.segs[k[0]]), k[1], k[2]])
        .concat([[fmt(r.total), "total" + (r.count !== undefined ? ` · ${r.count} ${unitLabel}` : ""), null]]));
    });
  }

  // ── columns ────────────────────────────────────────────────────────────────
  function columns(box, cols, fmt) {
    const W = box.clientWidth || 360, H = 170, padL = 44, padB = 22, padT = 18;
    const max = Math.max(1, ...cols.map(c => c.value));
    const step = niceStep(max);
    const top = Math.ceil(max / step) * step;
    const svg = el("svg", { width: W, height: H, role: "img" }, box);
    const plotH = H - padB - padT, plotW = W - padL - 8;
    for (let t = 0; t <= top; t += step) {
      const y = padT + plotH - (t / top) * plotH;
      el("line", { x1: padL, x2: W - 8, y1: y, y2: y, class: t ? "viz-gridline" : "viz-axis" }, svg);
      const lt = el("text", { x: padL - 6, y, class: "viz-tick", "text-anchor": "end", "dominant-baseline": "middle" }, svg);
      lt.textContent = fmt(t);
    }
    const band = plotW / cols.length, w = Math.min(24, band * 0.6);
    cols.forEach((c, i) => {
      const x = padL + i * band + (band - w) / 2;
      const h = (c.value / top) * plotH;
      if (h > 0) el("path", { d: colPath(x, padT + plotH - h, w, h), fill: SERIES, class: "viz-mark" }, svg);
      const lx = el("text", { x: x + w / 2, y: H - 6, class: "viz-tick", "text-anchor": "middle" }, svg);
      lx.textContent = c.label;
      if (h > 0) {
        const lv = el("text", { x: x + w / 2, y: padT + plotH - h - 5, class: "viz-val", "text-anchor": "middle" }, svg);
        lv.textContent = fmt(c.value);
      }
      const hit = el("rect", { x: padL + i * band, y: padT, width: band, height: plotH + padB, fill: "transparent", class: "viz-hit" }, svg);
      hover(hit, c.label, [[fmt(c.value), `announced · ${c.count} project${c.count === 1 ? "" : "s"}`, SERIES]]);
    });
  }

  function niceStep(max) {
    const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
    return 10 * mag;
  }

  // ── grouping helpers ───────────────────────────────────────────────────────
  function groupByVerdict(projects, keyFn, limit) {
    const m = new Map();
    for (const p of projects) {
      const k = keyFn(p);
      const r = m.get(k) || { label: k, segs: {}, total: 0, count: 0 };
      const v = verdictOf(p) || "none_found";
      const mw = p.claimed_mw || 0;
      r.segs[v] = (r.segs[v] || 0) + mw;
      r.total += mw;
      r.count += 1;
      m.set(k, r);
    }
    let rows = [...m.values()].sort((a, b) => b.total - a.total);
    if (rows.length > limit) {
      const rest = rows.slice(limit - 1);
      const other = { label: `Other (${rest.length})`, segs: {}, total: 0, count: 0 };
      for (const r of rest) {
        other.total += r.total; other.count += r.count;
        for (const k in r.segs) other.segs[k] = (other.segs[k] || 0) + r.segs[k];
      }
      rows = rows.slice(0, limit - 1).concat([other]);
    }
    return rows;
  }

  function card(grid, title, sub, wide) {
    const c = txt("section", "viz-card" + (wide ? " wide" : ""), "", grid);
    txt("h2", "", title, c);
    if (sub) txt("p", "viz-sub", sub, c);
    return c;
  }

  function tile(parent, label, value, note) {
    const t = txt("div", "viz-tile", "", parent);
    txt("div", "viz-tile-label", label, t);
    txt("div", "viz-tile-value", value, t);
    if (note) txt("div", "viz-tile-note", note, t);
  }

  // ── render ─────────────────────────────────────────────────────────────────
  let lastSlugs = null;

  function render(slugs) {
    lastSlugs = slugs || null;
    const root = document.getElementById("dashboard");
    if (!root) return;
    const all = window.PROJECTS || [];
    const projects = slugs ? all.filter(p => slugs.has(p.slug)) : all;
    root.replaceChildren();
    if (!projects.length) {
      txt("p", "viz-sub", "No projects match the filters.", root);
      return;
    }
    const checked = projects.filter(p => p.permit_checked_at && p.permit_status !== "not_applicable");
    const totalMW = projects.reduce((s, p) => s + (p.claimed_mw || 0), 0);
    const fullyMW = projects.filter(p => p.permit_status === "fully_permitted").reduce((s, p) => s + (p.claimed_mw || 0), 0);
    const withIssues = projects.filter(p => (p.permit_issue_count || 0) > 0).length;

    const tiles = txt("div", "viz-tiles", "", root);
    tile(tiles, "Projects tracked", String(projects.length), `${checked.length} checked for permits`);
    tile(tiles, "Announced capacity", fmtCap(totalMW), "sum of claimed MW");
    tile(tiles, "Fully permitted", fmtCap(fullyMW),
      totalMW ? `${Math.round((fullyMW / totalMW) * 100)}% of announced capacity` : "");
    tile(tiles, "With open issues", String(withIssues), "lawsuits, appeals, moratoria or enforcement");

    const grid = txt("div", "viz-grid", "", root);

    // 1. capacity by verdict (one stacked bar)
    const c1 = card(grid, "Announced capacity by permit status", "Share of claimed MW, by the permit verdict of each project.", true);
    const by = {};
    for (const p of projects) {
      const v = verdictOf(p) || "none_found";
      by[v] = by[v] || { mw: 0, n: 0 };
      by[v].mw += p.claimed_mw || 0;
      by[v].n += 1;
    }
    const box1 = txt("div", "viz-chart", "", c1);
    const keys = VERDICTS.filter(v => by[v[0]]);
    const W = box1.clientWidth || 600, barH = 24;
    const svg = el("svg", { width: W, height: barH, role: "img" }, box1);
    let x = 0;
    keys.forEach((k, j) => {
      const w = (by[k[0]].mw / Math.max(1, totalMW)) * W;
      const last = j === keys.length - 1;
      const seg = el("path", { d: barPath(x, 0, Math.max(0.5, last ? w : w - 2), barH, last), fill: k[2], class: "viz-mark" }, svg);
      hover(seg, k[1], [[fmtCap(by[k[0]].mw), `${Math.round((by[k[0]].mw / totalMW) * 100)}% · ${by[k[0]].n} projects`, k[2]]]);
      x += w;
    });
    legend(c1, keys.map(k => [k[1], k[2], `${fmtCap(by[k[0]].mw)} · ${by[k[0]].n}`]));

    // 2. developers
    const c2 = card(grid, "Capacity by developer", "Claimed capacity per developer, split by permit status.");
    hbars(txt("div", "viz-chart", "", c2), groupByVerdict(projects, p => ownerGroup(p.owner), 9), VERDICTS, fmtCap, "projects");
    legend(c2, VERDICTS.filter(v => by[v[0]]).map(v => [v[1], v[2]]));

    // 3. regions
    const c3 = card(grid, "Capacity by state / country", "US projects by state; elsewhere by country.");
    hbars(txt("div", "viz-chart", "", c3), groupByVerdict(projects, p => region(p.location), 9), VERDICTS, fmtCap, "projects");
    legend(c3, VERDICTS.filter(v => by[v[0]]).map(v => [v[1], v[2]]));

    // 4. permit types
    const c4 = card(grid, "Which permits are in place", `Number of projects per permit type (${checked.length} checked; a granted permit under appeal counts as granted).`);
    const typeRows = PERMIT_TYPES.map(([t, label]) => {
      const segs = { granted: 0, pending: 0, denied: 0, not_found: 0 };
      for (const p of checked) {
        let s = "not_found";
        try { s = (JSON.parse(p.permit_types_json || "{}")[t]) || "not_found"; } catch (e) { /* leave not_found */ }
        if (s === "challenged") s = "granted";
        segs[segs[s] !== undefined ? s : "not_found"] += 1;
      }
      return { label, segs, total: checked.length, valueLabel: `${segs.granted} granted` };
    });
    hbars(txt("div", "viz-chart", "", c4), typeRows, TYPE_STATUS, n => String(Math.round(n)), "");
    legend(c4, TYPE_STATUS.map(s => [s[1], s[2]]));

    // 5. announcements per year
    const c5 = card(grid, "Capacity by year of first announcement", "Claimed MW of projects, by the year they were first announced.");
    const yrs = new Map();
    let unknown = 0;
    for (const p of projects) {
      const y = /^(\d{4})/.exec(p.first_announcement_date || "");
      if (!y) { unknown += 1; continue; }
      const r = yrs.get(y[1]) || { label: y[1], value: 0, count: 0 };
      r.value += p.claimed_mw || 0;
      r.count += 1;
      yrs.set(y[1], r);
    }
    const cols = [...yrs.values()].sort((a, b) => a.label.localeCompare(b.label));
    if (cols.length) columns(txt("div", "viz-chart", "", c5), cols, fmtCap);
    if (unknown) txt("p", "viz-sub", `${unknown} project${unknown === 1 ? "" : "s"} without a known announcement date not shown.`, c5);
  }

  window.renderCharts = render;
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => render(lastSlugs), 150); });
})();
