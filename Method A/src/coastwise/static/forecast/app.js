/* Area priority scores and deterioration forecast UI. Reads window.FORECAST_DATA (written by `python -m forecasting build`). */
(function () {
  "use strict";
  const D = window.FORECAST_DATA;
  if (!D) { document.body.innerHTML = "<p style='padding:24px'>forecast_data.js is missing. Run <code>python -m forecasting build</code> first.</p>"; return; }

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, attrs, children) => {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v; else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v); else node.setAttribute(k, v);
    }
    (children || []).forEach(c => node.append(c));
    return node;
  };
  const svgNS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs, children) => {
    const node = document.createElementNS(svgNS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "text") node.textContent = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    (children || []).forEach(c => node.append(c));
    return node;
  };
  const fmtP = p => (p == null ? "–" : p >= 0.995 ? ">99%" : p < 0.005 ? "<1%" : Math.round(p * 100) + "%");
  const fmtN = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d));
  const cap = str => (str ? str.charAt(0).toUpperCase() + str.slice(1) : "");
  const GRADE_COLORS = [0, 1, 2, 3, 4].map(i => `var(--grade-${i})`);
  const GRADES = D.grades;

  /* ---------------------------------------------------------------- tooltip */
  const tip = $("#tooltip");
  function showTip(evt, html) {
    tip.innerHTML = html; tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  const ttRows = rows => rows.map(([k, v]) => `<div class="tt-row"><span>${k}</span><span>${v}</span></div>`).join("");

  /* ----------------------------------------------------------------- header */
  const T = D.totals;
  // Each structure says where its defects come from: Method B rows (inspector's severity code, one
  // inspection) or the earlier Method A library (model-assigned grades, repeat inspections).
  const RS = D.rates_source;
  const num = n => Number(n).toLocaleString("en-US");
  $("#source-line").textContent = `${T.assets} structures · ${T.reports} inspection reports · ${num(T.current_defects)} current defects · built ${D.generated_at.slice(0, 16).replace("T", " ")} UTC`;
  $("#eyebrow").textContent = "Defects rolled up into areas of the structure";
  const SOURCE_TEXT = {
    method_b: {
      timeline: "Inspector's severity code on every Method B defect row, per report",
      links: "The same element and defect class seen in consecutive reports. This report holds one inspection, so there are none here; the training pairs come from other structures.",
      notice: `This report holds one inspection, so deterioration rates cannot be fitted on it. The rates were fitted on ${T.fit_pairs} linked pairs from ${RS.assets_with_repeat_inspections} other structures and are applied here unchanged. Read the repair years as a what-if under those rates, not as a calibrated forecast for this pier.`,
      defects: year => `Every Method B defect row with a severity code from the ${year} inspection.`,
    },
    method_a: {
      timeline: "Method A's model-assigned damage grade for every extracted defect, per report",
      links: "The same element (or named sub-structure) and defect class seen in consecutive reports. These pairs are part of the model's training data.",
      notice: "This is the one structure here with repeat inspections, so the same elements can be followed from year to year. Its defects and grades come from the earlier Method A extraction (model-assigned grades), not from Method B, and its own history is part of what the rates were fitted on.",
      defects: year => `Every defect Method A extracted from the ${year} inspection.`,
    },
  };
  const repeatAssets = D.assets.filter(a => a.coverage && a.coverage.multi_year_tracks > 0);
  const kpis = [
    ["Inspection reports", T.reports, `${T.assets} structures · ${repeatAssets.length} with repeat inspections shown`],
    ["Current defects", num(T.current_defects), `${num(T.method_b_defects)} from Method B rows (of ${num(D.source.defect_rows)})`],
    ["Areas scored", D.assets.reduce((n, a) => n + a.areas.length, 0),
      `${D.assets.reduce((n, a) => n + a.areas.filter(x => x.band_key === "immediate").length, 0)} critical now · ${D.assets.reduce((n, a) => n + a.areas.filter(x => x.next_band_key === "immediate").length, 0)} at the next inspection`],
    ["Linked transitions", T.fit_pairs, `used to fit the rates · ${T.pairs} pairs · ${T.pair_outcomes.worsened || 0} worsened, ${T.pair_outcomes.stable || 0} stable`],
    ["Years per grade step", fmtN(D.rates.pooled.median_years_per_step), `pooled median · ${RS ? RS.assets_with_repeat_inspections : repeatAssets.length} structures with repeat inspections`],
  ];
  $("#kpi-row").append(...kpis.map(([l, v, sub]) => el("div", { class: "tile" }, [
    el("div", { class: "label", text: l }), el("div", { class: "value", text: String(v) }), el("div", { class: "sub", text: sub })])));

  /* ----------------------------------------------------------- asset select */
  const select = $("#asset-select");
  const ordered = [...D.assets].sort((a, b) => {
    const ra = a.coverage?.multi_year_tracks || 0, rb = b.coverage?.multi_year_tracks || 0;
    if (rb !== ra) return rb - ra;
    return (b.coverage?.defects || 0) - (a.coverage?.defects || 0);
  });
  ordered.forEach(a => {
    const c = a.coverage || {};
    const label = `${a.name} — ${c.defects || 0} defects, ${(c.years || []).length} inspection${(c.years || []).length === 1 ? "" : "s"}${c.multi_year_tracks ? ", linked" : ""}`;
    select.append(el("option", { value: a.key, text: label }));
  });
  select.addEventListener("change", () => renderAsset(select.value));

  /* -------------------------------------------------------- grade legend */
  $("#grade-legend").append(...GRADES.map((g, i) => el("span", { class: "key" }, [
    el("span", { class: "swatch", style: `background:${GRADE_COLORS[i]}` }), el("span", { text: `${i} ${g}` })])));

  /* ------------------------------------------------------------ charts */
  function stackedTimeline(container, asset) {
    container.innerHTML = "";
    const reports = asset.reports.filter(r => r.n_defects > 0);
    if (!reports.length) { container.append(el("p", { class: "empty", text: "No extracted defects for this structure." })); return; }
    const W = 560, H = 240, m = { t: 14, r: 12, b: 44, l: 36 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const maxN = Math.max(...reports.map(r => r.n_defects));
    const unit = maxN > 100 ? 250 : maxN > 50 ? 25 : 5;  // keep the five gridlines on round numbers
    const yMax = Math.ceil(maxN / unit) * unit || 5;
    const y = v => m.t + ih - (v / yMax) * ih;
    const band = iw / reports.length, bw = Math.min(44, band * 0.62);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Stacked bars of defects by grade per inspection" });
    const grid = s("g", { class: "grid" });
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = (yMax / ticks) * i, yy = y(v);
      grid.append(s("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy }));
      svg.append(s("text", { x: m.l - 6, y: yy + 4, "text-anchor": "end", text: String(Math.round(v)) }));
    }
    svg.append(grid);
    reports.forEach((r, i) => {
      const cx = m.l + band * i + band / 2;
      let acc = 0;
      const g = s("g");
      r.grade_counts.forEach((n, gi) => {
        if (!n) return;
        const y1 = y(acc + n), y0 = y(acc);
        const h = Math.max(0, y0 - y1 - 2);
        g.append(s("rect", { x: cx - bw / 2, y: y1 + (acc ? 2 : 0), width: bw, height: acc ? h : y0 - y1, rx: 0, fill: GRADE_COLORS[gi] }));
        acc += n;
      });
      // rounded top on the whole bar via clip is overkill; mark the data-end with a small cap
      g.append(s("rect", { x: cx - bw / 2, y: y(acc), width: bw, height: 3, rx: 1.5, fill: GRADE_COLORS[Math.max(0, r.grade_counts.map((n, gi) => n ? gi : -1).reduce((a, b) => Math.max(a, b), 0))] }));
      svg.append(g);
      svg.append(s("text", { x: cx, y: y(acc) - 5, "text-anchor": "middle", class: "direct-label", text: String(r.n_defects) }));
      svg.append(s("text", { x: cx, y: H - m.b + 16, "text-anchor": "middle", class: "axis-label", text: String(r.inspection_year) }));
      if (r.repairs_since_previous === "yes" || r.repairs_since_previous === "partial") {
        svg.append(s("text", { x: cx, y: H - m.b + 30, "text-anchor": "middle", class: "annot", text: r.repairs_since_previous === "yes" ? "repairs ✓" : "partial ✓" }));
      }
      const human = r.human || {};
      const rows = GRADES.map((gname, gi) => [`${gi} ${gname}`, r.grade_counts[gi]]).filter(x => x[1]);
      rows.push(["mean grade", fmtN(r.mean_grade, 2)]);
      if (r.pipeline_action) rows.push(["pipeline action", r.pipeline_action]);
      const htmlTip = `<div class="tt-title">${r.inspection_year} · ${r.filename}</div>${ttRows(rows)}` +
        (human.rating_raw ? `<div class="tt-row" style="margin-top:6px"><span>Human: ${human.rating_raw.slice(0, 160)}${human.rating_raw.length > 160 ? "…" : ""}</span></div>` : "");
      svg.append(s("rect", { class: "hit", x: m.l + band * i, y: m.t, width: band, height: ih,
        onmousemove: e => showTip(e, htmlTip), onmouseleave: hideTip }));
    });
    svg.append(s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }));
    container.append(svg);
  }

  function outlookChart(container, heroes, asset) {
    container.innerHTML = ""; heroes.innerHTML = "";
    const c = asset.curve;
    if (!c || !c.n_defects) { container.append(el("p", { class: "empty", text: "No inspection-report defects to forecast from." })); return; }
    const y0 = asset.latest_inspection_year;
    const pct = v => `${Math.round((v / c.n_defects) * 100)}%`;
    const i3 = c.years.indexOf(3), i5 = c.years.indexOf(5), i10 = c.years.indexOf(10);
    heroes.append(
      el("div", { class: "hero" }, [el("div", { class: "n", text: `${c.n_now} of ${c.n_defects}` }), el("div", { class: "t", text: `need priority repair now (${y0} inspection)` })]),
      el("div", { class: "hero" }, [el("div", { class: "n", text: `${fmtN(c.mean[i3], 0)}` }), el("div", { class: "t", text: `expected by ${y0 + 3} · ${pct(c.mean[i3])}` })]),
      el("div", { class: "hero" }, [el("div", { class: "n", text: `${fmtN(c.mean[i10], 0)}` }), el("div", { class: "t", text: `expected by ${y0 + 10} · ${pct(c.mean[i10])}` })]),
    );
    const W = 560, H = 220, m = { t: 14, r: 16, b: 34, l: 40 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const top = Math.max(...c.q95), unit = top > 100 ? 250 : top > 50 ? 25 : 5;
    const yMax = Math.max(1, Math.ceil(top / unit) * unit);
    const x = t => m.l + (t / 10) * iw, y = v => m.t + ih - (v / yMax) * ih;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Expected defects needing priority repair over the next ten years" });
    const grid = s("g", { class: "grid" });
    for (let i = 0; i <= 5; i++) {
      const v = (yMax / 5) * i;
      grid.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      svg.append(s("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: String(Math.round(v)) }));
    }
    svg.append(grid);
    c.years.forEach(t => { if (t % 2 === 0) svg.append(s("text", { x: x(t), y: H - m.b + 16, "text-anchor": "middle", class: "axis-label", text: String(y0 + t) })); });
    const area = c.years.map((t, i) => `${i ? "L" : "M"}${x(t)},${y(c.q95[i])}`).join(" ") + " " +
      [...c.years].reverse().map((t, j) => { const i = c.years.length - 1 - j; return `L${x(t)},${y(c.q05[i])}`; }).join(" ") + " Z";
    svg.append(s("path", { d: area, fill: "var(--series-1-soft)" }));
    svg.append(s("path", { d: c.years.map((t, i) => `${i ? "L" : "M"}${x(t)},${y(c.mean[i])}`).join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2, "stroke-linejoin": "round" }));
    svg.append(s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }));
    const last = c.years.length - 1;
    svg.append(s("text", { x: x(10) - 4, y: y(c.mean[last]) - 8, "text-anchor": "end", class: "direct-label", text: `${fmtN(c.mean[last], 1)} expected` }));
    svg.append(s("text", { x: x(10) - 4, y: y(c.q95[last]) - 6, "text-anchor": "end", class: "annot", text: "90% band" }));
    // crosshair + tooltip
    const cross = s("line", { x1: 0, x2: 0, y1: m.t, y2: m.t + ih, stroke: "var(--axis)", "stroke-dasharray": "3 3", visibility: "hidden" });
    const dot = s("circle", { r: 4, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" });
    svg.append(cross, dot);
    svg.append(s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih, style: "fill:transparent",
      onmousemove: e => {
        const rect = svg.getBoundingClientRect();
        const px = ((e.clientX - rect.left) / rect.width) * W;
        const t = Math.max(0, Math.min(10, Math.round(((px - m.l) / iw) * 10)));
        const i = c.years.indexOf(t);
        cross.setAttribute("x1", x(t)); cross.setAttribute("x2", x(t)); cross.setAttribute("visibility", "visible");
        dot.setAttribute("cx", x(t)); dot.setAttribute("cy", y(c.mean[i])); dot.setAttribute("visibility", "visible");
        showTip(e, `<div class="tt-title">${y0 + t}${t ? ` (+${t} y)` : " (latest inspection)"}</div>` + ttRows([
          ["expected needing priority repair", `${fmtN(c.mean[i], 1)} of ${c.n_defects}`],
          ["90% band", `${fmtN(c.q05[i], 1)} – ${fmtN(c.q95[i], 1)}`],
          ["share", pct(c.mean[i])]]));
      },
      onmouseleave: () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); } }));
    container.append(svg);
  }

  function intervalChart(container, rows, opts) {
    container.innerHTML = "";
    if (!rows.length) { container.append(el("p", { class: "empty", text: "No data." })); return; }
    const W = 560, rowH = 30, m = { t: 12, r: 16, b: 30, l: opts.labelWidth || 170 };
    const H = m.t + rows.length * rowH + m.b, iw = W - m.l - m.r;
    const xMax = opts.xMax || Math.max(...rows.map(r => r.hi)) * 1.1;
    const x = v => m.l + (v / xMax) * iw;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "" });
    const grid = s("g", { class: "grid" });
    const nTicks = 5;
    for (let i = 0; i <= nTicks; i++) {
      const v = (xMax / nTicks) * i;
      grid.append(s("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b }));
      svg.append(s("text", { x: x(v), y: H - m.b + 16, "text-anchor": "middle", text: opts.fmt ? opts.fmt(v) : fmtN(v, 2) }));
    }
    svg.append(grid);
    if (opts.refLine != null) svg.append(s("line", { x1: x(opts.refLine), x2: x(opts.refLine), y1: m.t, y2: H - m.b, stroke: "var(--text-muted)", "stroke-dasharray": "4 3" }));
    rows.forEach((r, i) => {
      const cy = m.t + rowH * i + rowH / 2;
      svg.append(s("text", { x: m.l - 10, y: cy + 4, "text-anchor": "end", class: "axis-label", text: r.label }));
      svg.append(s("line", { x1: x(r.lo), x2: x(r.hi), y1: cy, y2: cy, stroke: "var(--series-1)", "stroke-width": 2, "stroke-linecap": "round" }));
      svg.append(s("circle", { cx: x(r.mid), cy, r: 5, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
      svg.append(s("text", { x: Math.min(x(r.hi) + 8, W - m.r - 40), y: cy + 4, class: "annot", text: r.note || "" }));
      svg.append(s("rect", { class: "hit", x: 0, y: cy - rowH / 2, width: W, height: rowH,
        onmousemove: e => showTip(e, `<div class="tt-title">${r.label}</div>${ttRows(r.tip)}`), onmouseleave: hideTip }));
    });
    container.append(svg);
  }

  /* ----------------------------------------------------------- tables */
  const gradeBadge = (g, label) => el("span", { class: "grade-badge" }, [el("span", { class: "dot", style: `background:${GRADE_COLORS[g]}` }), el("span", { text: `${g} ${label || GRADES[g]}` })]);
  const statusBadge = (key, label, win) => el("span", { class: `status ${key}` }, [el("span", { class: "ico", "aria-hidden": "true" }), el("span", { text: label }), win ? el("span", { class: "win", text: ` · ${win}` }) : ""]);
  const seq = points => {
    const wrap = el("span", { class: "seq" });
    points.forEach((p, i) => {
      if (i) wrap.append(el("span", { class: "yr", text: "→" }));
      wrap.append(el("span", { class: `g g${p.grade}`, style: `background:${GRADE_COLORS[p.grade]}`, title: `${p.year}: ${p.grade_label} (${p.location || "no location"})`, text: String(p.grade) }));
      wrap.append(el("span", { class: "yr", text: String(p.year).slice(2) }));
    });
    return wrap;
  };

  let showAll = false;
  function defectsTable(asset) {
    const tbody = $("#defects-table tbody"); tbody.innerHTML = "";
    const area = $("#filter-area").value, cls = $("#filter-class").value, minG = Number($("#filter-grade").value), q = $("#filter-search").value.trim().toLowerCase();
    const areaLabel = Object.fromEntries(asset.areas.map(a => [a.key, a.label]));
    let rows = asset.current_defects.filter(r => (!area || r.area_key === area) && (!cls || r.defect_class === cls) && r.grade >= minG &&
      (!q || [r.location, r.defect_type_raw, r.material, r.description, r.defect_class_label].join(" ").toLowerCase().includes(q)));
    const total = rows.length;
    if (!showAll) rows = rows.slice(0, 40);
    rows.forEach(r => {
      const likely = r.repair_now ? el("span", { text: "now" }) :
        el("span", {}, [document.createTextNode(r.median_years_to_repair == null ? "–" : String(Math.round(r.inspection_year + r.median_years_to_repair))),
          el("span", { class: "sub", text: r.q10_years == null ? "" : `10–90%: ${Math.round(r.inspection_year + r.q10_years)}–${Math.round(r.inspection_year + r.q90_years)}` })]);
      tbody.append(el("tr", {}, [
        el("td", { class: "loc" }, [document.createTextNode(r.location || "(location not stated)"), el("span", { class: "sub", text: r.component_key ? `element ${r.component_key}` : (r.system_key ? `sub-structure ${r.system_key}` : "") })]),
        el("td", { text: areaLabel[r.area_key] || "–" }),
        el("td", {}, [document.createTextNode(r.defect_type_raw || "(unnamed)"), el("span", { class: "sub", text: (r.measurement ? r.measurement + " · " : "") + (r.description || "").slice(0, 110) + ((r.description || "").length > 110 ? "…" : "") })]),
        el("td", {}, [document.createTextNode(r.defect_class_label), el("span", { class: "sub", text: `${cap(r.material)}${r.extent ? " · " + r.extent : ""}${r.model_confidence ? " · " + r.model_confidence + " confidence" : ""}` })]),
        el("td", {}, [gradeBadge(r.grade, r.grade_label), r.severity_raw ? el("span", { class: "sub", text: `report: ${r.severity_raw}` }) : ""]),
        el("td", {}, [statusBadge(r.action_key, r.action_now, r.action_window)]),
        el("td", { class: "num" }, r.grade >= 4 ? [el("span", { text: "4 severe" }), el("span", { class: "sub", text: "top grade" })] : [
          gradeBadge(r.next_grade_likely), el("span", { class: "sub", text: `${fmtP(r.p_worse_by_next)} worse than now · mean ${fmtN(r.next_grade_mean, 1)}` })]),
        el("td", { class: "num", text: fmtP(r.p_repair_3y) }),
        el("td", { class: "num", text: fmtP(r.p_repair_5y) }),
        el("td", { class: "num" }, [likely]),
        el("td", { text: (r.pages || []).join(", ") }),
      ]));
    });
    $("#rows-note").textContent = `${rows.length} of ${total} shown · ${asset.current_defects.length} current defects from the ${asset.latest_inspection_year ?? "–"} inspection`;
    $("#show-all").hidden = total <= 40;
    $("#show-all").textContent = showAll ? "Show fewer rows" : `Show all ${total} rows`;
  }
  $("#show-all").addEventListener("click", () => { showAll = !showAll; defectsTable(current()); });
  ["#filter-area", "#filter-class", "#filter-grade"].forEach(id => $(id).addEventListener("change", () => defectsTable(current())));
  $("#filter-search").addEventListener("input", () => defectsTable(current()));

  function tracksTable(asset) {
    const tbody = $("#tracks-table tbody"); tbody.innerHTML = "";
    const tracks = [...asset.tracks].sort((a, b) => b.n_inspections - a.n_inspections || (a.link_level === "element" ? -1 : 1));
    if (!tracks.length) { tbody.append(el("tr", {}, [el("td", { colspan: 4, class: "muted", text: "No element seen in more than one inspection for this structure. Forecasts use population rates." })])); return; }
    tracks.forEach(t => tbody.append(el("tr", {}, [
      el("td", { text: t.link_key }), el("td", { text: D.class_labels[t.defect_class] || t.defect_class }),
      el("td", {}, [seq(t.points)]), el("td", { class: "muted", text: t.link_level === "element" ? "numbered element" : "sub-structure" })])));
  }

  function humanTable(asset) {
    const tbody = $("#human-table tbody"); tbody.innerHTML = "";
    asset.reports.forEach(r => {
      const h = r.human || {};
      tbody.append(el("tr", {}, [
        el("td", {}, [document.createTextNode(String(r.inspection_year)), el("span", { class: "sub", text: r.report_role === "inspection" ? "" : r.report_role })]),
        el("td", {}, [document.createTextNode((h.rating_raw || "–").slice(0, 110) + ((h.rating_raw || "").length > 110 ? "…" : "")), h.rating_scale ? el("span", { class: "sub", text: h.rating_scale.slice(0, 80) }) : ""]),
        el("td", { text: (h.urgency || "–").slice(0, 110) + ((h.urgency || "").length > 110 ? "…" : "") }),
        el("td", {}, [r.pipeline_action_key ? statusBadge(r.pipeline_action_key, r.pipeline_action) : el("span", { class: "muted", text: "–" }), el("span", { class: "sub", text: `${r.n_defects} defects` })]),
        el("td", {}, [document.createTextNode(cap(r.repairs_since_previous)), h.repairs_since_previous ? el("span", { class: "sub", text: h.repairs_since_previous.slice(0, 90) + (h.repairs_since_previous.length > 90 ? "…" : "") }) : ""]),
      ]));
    });
  }

  /* ----------------------------------------------------------- model panel */
  intervalChart($("#grade-rates-chart"), D.rates.by_grade.map(r => ({
    label: `${r.grade} ${GRADES[r.grade]}`, lo: r.q05, hi: r.q95, mid: r.posterior_mean,
    note: `${r.pairs} pairs / ${Math.round(r.exposure_years)} y`,
    tip: [["posterior mean", `${fmtN(r.posterior_mean, 3)} / yr`], ["90% interval", `${fmtN(r.q05, 3)} – ${fmtN(r.q95, 3)}`],
      ["observed", `${r.events} advances in ${Math.round(r.exposure_years)} defect-years`], ["median years in grade", fmtN(r.median_years_in_grade, 1)],
      ["weight on prior", fmtP(r.shrinkage)]],
  })), { labelWidth: 110, fmt: v => fmtN(v, 2), aria: "Advance rate by current grade" });
  intervalChart($("#class-mult-chart"), D.rates.by_class.sort((a, b) => b.multiplier_mean - a.multiplier_mean).map(r => ({
    label: D.class_labels[r.value] || r.value, lo: r.q05, hi: r.q95, mid: r.multiplier_mean, note: `${r.pairs} pairs`,
    tip: [["multiplier", `×${fmtN(r.multiplier_mean, 2)}`], ["90% interval", `${fmtN(r.q05, 2)} – ${fmtN(r.q95, 2)}`],
      ["observed / expected advances", `${r.events} / ${fmtN(r.expected_events, 1)}`], ["weight on prior", fmtP(r.shrinkage)]],
  })), { labelWidth: 200, refLine: 1, xMax: 2.5, fmt: v => "×" + fmtN(v, 1), aria: "Defect class multipliers" });

  const bt = $("#backtest-table tbody");
  D.backtests.forEach(b => bt.append(el("tr", {}, [
    el("td", {}, [document.createTextNode(b.name), el("span", { class: "sub", text: `${b.n_train} training pairs` })]),
    el("td", { class: "num", text: String(b.n_test) }), el("td", { class: "num", text: fmtP(b.observed_rate) }),
    el("td", { class: "num", text: fmtP(b.mean_predicted) }), el("td", { class: "num", text: fmtN(b.brier, 3) }),
    el("td", { class: "num", text: fmtN(b.brier_pooled_rate, 3) }), el("td", { class: "num", text: fmtN(b.brier_climatology, 3) })])));
  if (!D.backtests.length) bt.append(el("tr", {}, [el("td", { colspan: 7, class: "muted", text: "Not enough linked pairs for a hold-out." })]));

  const rt = $("#rules-table tbody");
  D.action_rules.forEach(r => rt.append(el("tr", {}, [
    el("td", {}, [gradeBadge(r.grade)]), el("td", { text: r.extent }), el("td", {}, [statusBadge(r.action, r.label)]), el("td", { text: r.window })])));
  $("#method-notes").append(...D.method_notes.map(n => el("li", { text: n })));
  $("#footer").textContent = `Repair threshold: grade ${D.repair_threshold} (${GRADES[D.repair_threshold]}). Posterior draws: ${D.rates.draws}. Exclusions from fitting: ${Object.entries(T.exclusions).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "none"}.`;


  /* ------------------------------------------------------ area priority scores */
  const SC = D.scoring;
  const NEXT = SC.next_inspection_years, NEXT_I = SC.curve_years.indexOf(SC.next_inspection_years);
  const BAND_COLOR = { monitor: "var(--status-good)", routine: "var(--status-warning)", priority: "var(--status-serious)", immediate: "var(--status-critical)" };
  const W_KEYS = ["worst", "share", "mean"];
  const weightInputs = { worst: $("#w-worst"), share: $("#w-share"), mean: $("#w-mean") };
  const setWeights = w => W_KEYS.forEach(k => { weightInputs[k].value = w[k]; });
  setWeights(SC.weights);
  function weights() {
    const raw = W_KEYS.map(k => Math.max(0, Number(weightInputs[k].value) || 0));
    const sum = raw.reduce((a, b) => a + b, 0) || 1;
    return Object.fromEntries(W_KEYS.map((k, i) => [k, raw[i] / sum]));
  }
  const isDefault = () => W_KEYS.every(k => Math.abs(weights()[k] - SC.weights[k]) < 1e-6);
  const scoreOf = (parts, w) => 100 * (w.worst * parts[0] + w.share * parts[1] + w.mean * parts[2]);
  const bandOf = score => SC.bands.find(b => score >= b.min) || SC.bands[SC.bands.length - 1];
  function scored(area) {
    const w = weights(), def = isDefault();
    const now = def ? area.score_now : scoreOf(area.parts_now, w);
    const curve = def ? area.score_curve.mean : area.parts_curve.map(p => scoreOf(p, w));
    return { now, next: curve[NEXT_I], curve, band: def ? area.score_curve : null };
  }
  $("#band-legend").append(...[...SC.bands].reverse().map(b => el("span", { class: "key" }, [
    el("span", { class: "swatch", style: `background:${BAND_COLOR[b.key]}` }),
    el("span", { text: `${b.label}${b.min ? " ≥ " + b.min : ""}` })])));

  const scoreCell = (score, prev) => {
    const b = bandOf(score);
    const cell = el("div", { class: "score-cell" }, [
      el("span", { class: "score-bar" }, [el("span", { style: `width:${Math.min(100, score)}%;background:${BAND_COLOR[b.key]}` })]),
      el("span", { class: "score-n", text: Math.round(score) }),
      el("span", { class: `status ${b.key}` }, [el("span", { class: "ico", "aria-hidden": "true" }), el("span", { text: b.label })]),
    ]);
    if (prev != null && Math.round(score) > Math.round(prev)) cell.append(el("span", { class: "delta", text: `+${Math.round(score - prev)}` }));
    return cell;
  };
  const gradeStrip = counts => {
    const total = counts.reduce((a, b) => a + b, 0) || 1;
    const wrap = el("span", { class: "grade-strip", title: counts.map((n, g) => n ? `${g} ${GRADES[g]}: ${n}` : "").filter(Boolean).join(" · ") });
    counts.forEach((n, g) => { if (n) wrap.append(el("span", { style: `width:${(n / total) * 100}%;background:${GRADE_COLORS[g]}` })); });
    return wrap;
  };

  let selectedArea = null;
  function areasTable(asset) {
    const tbody = $("#areas-table tbody"); tbody.innerHTML = "";
    const y0 = asset.latest_inspection_year;
    const rows = asset.areas.map(a => ({ a, sc: scored(a) })).sort((p, q) => q.sc.now - p.sc.now || q.sc.next - p.sc.next);
    if (!rows.length) { tbody.append(el("tr", {}, [el("td", { colspan: 6, class: "muted", text: "No defects to score." })])); return rows; }
    rows.forEach(({ a, sc }, i) => {
      const tti = a.intervention_now ? el("span", { class: "status immediate" }, [el("span", { class: "ico" }), el("span", { text: "now" }),
          el("span", { class: "win", text: ` · ${a.units_at_threshold} of ${a.n_units} at grade 3+` })])
        : el("span", {}, [document.createTextNode(String(Math.round(y0 + a.years_to_intervention))),
          el("span", { class: "sub", text: `10–90%: ${Math.round(y0 + a.years_to_intervention_q10)}–${Math.round(y0 + a.years_to_intervention_q90)} · ${fmtP(a.p_new_repair_by_next)} by next inspection` })]);
      const tr = el("tr", { class: "clickable" + (a.key === selectedArea ? " selected" : ""), tabindex: "0",
        "aria-selected": String(a.key === selectedArea),
        onclick: () => selectArea(asset, a.key), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectArea(asset, a.key); } } }, [
        el("td", {}, [el("span", { class: "rank", text: String(i + 1) }), document.createTextNode(a.label),
        ]),
        el("td", { class: "num" }, [document.createTextNode(String(a.n_units)), el("span", { class: "sub", text: `${a.n_defects} rows` })]),
        el("td", {}, [gradeStrip(a.grade_counts), el("span", { class: "sub", text: `worst ${a.worst_grade} ${GRADES[a.worst_grade]}` })]),
        el("td", {}, [scoreCell(sc.now)]),
        el("td", {}, [scoreCell(sc.next, sc.now), sc.band ? el("span", { class: "sub", text: `90%: ${Math.round(a.score_next_q05)}–${Math.round(a.score_next_q95)} · ~${fmtN(a.expected_at_threshold_next, 0)} at grade 3+` }) : ""]),
        el("td", { class: "num" }, [tti]),
      ]);
      tbody.append(tr);
    });
    return rows;
  }

  function areaChart(asset, area) {
    const container = $("#area-chart"); container.innerHTML = "";
    if (!area) { container.append(el("p", { class: "empty", text: "No area selected." })); return; }
    const sc = scored(area), y0 = asset.latest_inspection_year, years = SC.curve_years;
    $("#area-chart-title").textContent = `Projected score · ${area.label}`;
    $("#area-chart-sub").textContent = (sc.band ? "Mean with 90% band" : "Mean (band shown at the default weights only)") +
      ` · no repairs assumed · next inspection taken as ${y0 + NEXT} · click an area to change`;
    const W = 560, H = 250, m = { t: 12, r: 16, b: 32, l: 36 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, tMax = years[years.length - 1];
    const x = t => m.l + (t / tMax) * iw, y = v => m.t + ih - (v / 100) * ih;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Projected priority score for ${area.label} over ten years` });
    // score bands as faint horizontal regions
    SC.bands.forEach((b, i) => {
      const hi = i ? SC.bands[i - 1].min : 100;
      svg.append(s("rect", { x: m.l, y: y(hi), width: iw, height: y(b.min) - y(hi), fill: BAND_COLOR[b.key], opacity: 0.08 }));
      svg.append(s("text", { x: m.l + 6, y: y(b.min) - 5, class: "annot", text: b.label }));
    });
    const grid = s("g", { class: "grid" });
    [0, 25, 50, 75, 100].forEach(v => { grid.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      svg.append(s("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: String(v) })); });
    svg.append(grid);
    years.forEach(t => { if (t % 2 === 0) svg.append(s("text", { x: x(t), y: H - m.b + 16, "text-anchor": "middle", class: "axis-label", text: String(y0 + t) })); });
    svg.append(s("line", { x1: x(NEXT), x2: x(NEXT), y1: m.t, y2: m.t + ih, stroke: "var(--text-muted)", "stroke-dasharray": "4 3" }));
    svg.append(s("text", { x: x(NEXT) + 4, y: m.t + ih - 6, class: "annot", text: "next inspection" }));
    if (sc.band) {
      const b = sc.band;
      const d = years.map((t, i) => `${i ? "L" : "M"}${x(t)},${y(b.q95[i])}`).join(" ") + " " +
        [...years].reverse().map((t, j) => `L${x(t)},${y(b.q05[years.length - 1 - j])}`).join(" ") + " Z";
      svg.append(s("path", { d, fill: "var(--series-1-soft)" }));
    }
    svg.append(s("path", { d: years.map((t, i) => `${i ? "L" : "M"}${x(t)},${y(sc.curve[i])}`).join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2.5, "stroke-linejoin": "round" }));
    [[0, sc.curve[0], "now"], [NEXT, sc.curve[NEXT_I], "next"]].forEach(([t, v, k]) => {
      svg.append(s("circle", { cx: x(t), cy: y(v), r: 5, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
      svg.append(s("text", { x: x(t) + (k === "now" ? 8 : -8), y: y(v) - 9, "text-anchor": k === "now" ? "start" : "end", class: "direct-label", text: `${Math.round(v)}` }));
    });
    svg.append(s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }));
    svg.append(s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih, style: "fill:transparent",
      onmousemove: e => {
        const rect = svg.getBoundingClientRect();
        const t = Math.max(0, Math.min(tMax, Math.round((((e.clientX - rect.left) / rect.width) * W - m.l) / iw * tMax)));
        const i = years.indexOf(t), v = sc.curve[i];
        showTip(e, `<div class="tt-title">${y0 + t}${t ? ` (+${t} y)` : " (latest inspection)"}</div>` + ttRows([
          ["score", `${fmtN(v, 0)} · ${bandOf(v).label}`], ...(sc.band ? [["90% band", `${fmtN(sc.band.q05[i], 0)} – ${fmtN(sc.band.q95[i], 0)}`]] : []),
          ["share of elements at grade 3+", fmtP(area.parts_curve[i][1])]]));
      }, onmouseleave: hideTip }));
    container.append(svg);
  }

  function renderAreas(asset) {
    const rows = areasTable(asset);
    if (!rows.length) { areaChart(asset, null); return; }
    if (!asset.areas.some(a => a.key === selectedArea)) selectedArea = rows[0].a.key;
    $("#areas-table tbody").querySelectorAll("tr").forEach((tr, i) => {
      const on = rows[i].a.key === selectedArea; tr.classList.toggle("selected", on); tr.setAttribute("aria-selected", String(on)); });
    areaChart(asset, asset.areas.find(a => a.key === selectedArea));
    const crit = side => rows.filter(r => bandOf(side(r.sc)).key === "immediate").length;
    $("#areas-subtitle").textContent = `${asset.areas.length} areas · ${crit(sc => sc.now)} ${SC.bands[0].label.toLowerCase()} now, ` +
      `${crit(sc => sc.next)} at the next inspection (${asset.latest_inspection_year + NEXT}). Ranked by score now; click an area to see its projection and filter the defects below.`;
    $("#weights-note").textContent = isDefault() ? "Placeholder weights · to agree with Capstone" : "Custom weights (normalised to sum to 1)";
  }
  function selectArea(asset, key) {
    selectedArea = key; renderAreas(asset);
    $("#filter-area").value = key; showAll = false; defectsTable(asset);
  }
  Object.values(weightInputs).forEach(inp => inp.addEventListener("input", () => renderAreas(current())));
  $("#w-reset").addEventListener("click", () => { setWeights(SC.weights); renderAreas(current()); });
  $("#areas-next-head").textContent = `Score at next inspection (+${NEXT} y)`;
  $("#next-grade-head").textContent = `Grade at next inspection (+${NEXT} y)`;
  $("#strip-severity").textContent = `The grade each defect is expected to have at the next inspection, taken as ${NEXT} years after the latest one.`;

  /* ----------------------------------------------------------- render */
  const current = () => D.assets.find(a => a.key === select.value);
  function renderAsset(key) {
    const asset = D.assets.find(a => a.key === key);
    if (!asset) return;
    showAll = false;
    const text = SOURCE_TEXT[asset.source] || SOURCE_TEXT.method_b;
    $("#timeline-sub").textContent = text.timeline;
    $("#links-sub").textContent = text.links;
    $("#rates-notice").hidden = false;
    $("#rates-notice").textContent = text.notice;
    $("#human-block").hidden = asset.source !== "method_a";
    $("#asset-title").textContent = asset.name;
    const years = (asset.coverage && asset.coverage.years) || [];
    $("#asset-subtitle").textContent = years.length ? `Inspections: ${years.join(", ")} · forecasts start from the ${asset.latest_inspection_year ?? "–"} inspection` : "No extracted defects.";
    const chips = $("#asset-chips"); chips.innerHTML = "";
    const c = asset.coverage || {};
    [[`${c.defects || 0} defect events`], [`${c.element_keyed || 0} on numbered elements`], [`${c.multi_year_tracks || 0} linked tracks`],
      [`${asset.pairs_summary.fit_eligible} pairs used in fit`]].forEach(([t]) => chips.append(el("span", { class: "chip", text: t })));
    const pc = $("#pairs-chips"); pc.innerHTML = "";
    const ps = asset.pairs_summary;
    [["worsened", ps.worsened], ["stable", ps.stable], ["repaired", ps.repaired], ["improved, unexplained", ps.improved_unexplained]]
      .forEach(([l, v]) => pc.append(el("span", { class: "chip", text: `${v} ${l}` })));

    stackedTimeline($("#timeline-chart"), asset);
    outlookChart($("#outlook-chart"), $("#outlook-heroes"), asset);

    const fa = $("#filter-area"); fa.innerHTML = ""; fa.append(el("option", { value: "", text: "all areas" }));
    asset.areas.forEach(a => fa.append(el("option", { value: a.key, text: a.label })));
    selectedArea = null;
    renderAreas(asset);
    const fc = $("#filter-class"); fc.innerHTML = ""; fc.append(el("option", { value: "", text: "all classes" }));
    const classes = [...new Set(asset.current_defects.map(r => r.defect_class))].sort();
    classes.forEach(k => fc.append(el("option", { value: k, text: D.class_labels[k] || k })));
    $("#filter-grade").value = "0"; $("#filter-search").value = "";
    $("#defects-subtitle").textContent = asset.current_defects.length
      ? `${text.defects(asset.latest_inspection_year)} Grade at next inspection = most likely grade ${NEXT} years on (severity forecast). P(repair) = probability of reaching grade 3 (advanced) by that horizon; time to intervention is the median year it gets there.`
      : "No inspection report with extracted defects for this structure.";
    defectsTable(asset);
    tracksTable(asset);
    humanTable(asset);
  }
  select.value = ordered[0].key;
  renderAsset(select.value);
})();
