// Small SVG chart primitives. No library: the page loads nothing from a CDN,
// which is what lets the Content-Security-Policy say `script-src 'self'`.
"use strict";

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function extent(values) {
  let lo = Infinity, hi = -Infinity;
  for (const v of values) if (v !== null && v !== undefined) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (lo === Infinity) return [0, 1];
  if (lo === hi) return [lo - 1, hi + 1];
  const pad = (hi - lo) * 0.06;
  return [lo - pad, hi + pad];
}

function path(values, x, y) {
  let d = "", pen = false;
  values.forEach((v, i) => {
    if (v === null || v === undefined) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

// A narrower canvas on phones keeps axis labels legible after scaling.
function canvasWidth() {
  return window.innerWidth < 640 ? 440 : 800;
}

function frame(width, height, pad) {
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
  return { svg, inner: { l: pad.l, r: width - pad.r, t: pad.t, b: height - pad.b } };
}

function yAxis(svg, inner, lo, hi, y, fmt, ticks = 4) {
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(v), y2: y(v), class: "grid-line" }));
    const t = s("text", { x: inner.r + 6, y: y(v) + 4 });
    t.textContent = fmt(v);
    svg.append(t);
  }
}

function xLabels(svg, inner, dates, x, count = 5) {
  for (let i = 0; i < count; i++) {
    const idx = Math.round((dates.length - 1) * (i / (count - 1)));
    const t = s("text", { x: x(idx), y: inner.b + 16, "text-anchor": i === 0 ? "start" : i === count - 1 ? "end" : "middle" });
    t.textContent = dates[idx]?.slice(2) ?? "";
    svg.append(t);
  }
}

/** Price with SMA20/SMA50, Bollinger band, volume underlay and a crosshair readout. */
export function priceChart(series, { onHover } = {}) {
  const W = canvasWidth(), H = 320, pad = { l: 8, r: 56, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const n = series.close.length;
  const [lo, hi] = extent([...series.close, ...series.bb_upper, ...series.bb_lower]);
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  svg.setAttribute("aria-label", `Price chart, ${series.dates[0]} to ${series.dates[n - 1]}`);

  yAxis(svg, inner, lo, hi, y, (v) => v.toFixed(hi - lo < 20 ? 2 : 0));
  const maxVol = Math.max(...series.volume);
  const barW = Math.max(1, (inner.r - inner.l) / n - 1);
  series.volume.forEach((v, i) => {
    const hgt = ((inner.b - inner.t) * 0.22 * v) / maxVol;
    svg.append(s("rect", { x: x(i) - barW / 2, y: inner.b - hgt, width: barW, height: hgt, class: "vol" }));
  });

  let band = "";
  const upper = [], lower = [];
  series.bb_upper.forEach((v, i) => { if (v !== null && series.bb_lower[i] !== null) { upper.push([x(i), y(v)]); lower.push([x(i), y(series.bb_lower[i])]); } });
  if (upper.length) {
    band = "M" + upper.map((p) => p.join(",")).join("L") + "L" + lower.reverse().map((p) => p.join(",")).join("L") + "Z";
    svg.append(s("path", { d: band, class: "band" }));
  }
  svg.append(s("path", { d: path(series.sma50, x, y), class: "sma50" }));
  svg.append(s("path", { d: path(series.sma20, x, y), class: "sma20" }));
  svg.append(s("path", { d: path(series.close, x, y), class: "price" }));
  xLabels(svg, inner, series.dates, x, W < 600 ? 3 : 5);

  const cross = s("line", { y1: inner.t, y2: inner.b, class: "cross", visibility: "hidden" });
  const dot = s("circle", { r: 3.5, class: "dot", visibility: "hidden" });
  svg.append(cross, dot);
  const overlay = s("rect", { x: inner.l, y: inner.t, width: inner.r - inner.l, height: inner.b - inner.t, fill: "transparent" });
  svg.append(overlay);
  const move = (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - inner.l) / (inner.r - inner.l)) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(series.close[i]));
    cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
    onHover?.(i);
  };
  overlay.addEventListener("pointermove", move);
  overlay.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); onHover?.(null); });
  return svg;
}

/** A bounded oscillator (RSI) with reference lines. */
export function oscillator(series, values, { lo = 0, hi = 100, refs = [30, 70] } = {}) {
  const W = canvasWidth(), H = 150, pad = { l: 8, r: 56, t: 8, b: 22 };
  const { svg, inner } = frame(W, H, pad);
  const n = values.length;
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => v.toFixed(0), 2);
  for (const r of refs) svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(r), y2: y(r), class: "ref" }));
  svg.append(s("path", { d: path(values, x, y), class: "price" }));
  xLabels(svg, inner, series.dates, x);
  svg.setAttribute("aria-label", "RSI 14 chart");
  return svg;
}

/** MACD line, signal line, and histogram bars around zero. */
export function macdChart(series) {
  const W = canvasWidth(), H = 170, pad = { l: 8, r: 56, t: 8, b: 22 };
  const { svg, inner } = frame(W, H, pad);
  const n = series.macd.length;
  const [lo0, hi0] = extent([...series.macd, ...series.macd_signal, ...series.macd_hist]);
  const m = Math.max(Math.abs(lo0), Math.abs(hi0));
  const lo = -m, hi = m;
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => v.toFixed(2), 2);
  const barW = Math.max(1, (inner.r - inner.l) / n - 1);
  series.macd_hist.forEach((v, i) => {
    if (v === null) return;
    const top = Math.min(y(v), y(0));
    svg.append(s("rect", { x: x(i) - barW / 2, y: top, width: barW, height: Math.abs(y(v) - y(0)), class: v >= 0 ? "bar-up" : "bar-down" }));
  });
  svg.append(s("path", { d: path(series.macd, x, y), class: "price" }));
  svg.append(s("path", { d: path(series.macd_signal, x, y), class: "sma20" }));
  xLabels(svg, inner, series.dates, x);
  svg.setAttribute("aria-label", "MACD chart");
  return svg;
}

/** Slice every array in a chart series to its last `count` points. */
export function tail(series, count) {
  const out = {};
  for (const [k, v] of Object.entries(series)) out[k] = Array.isArray(v) ? v.slice(-count) : v;
  return out;
}

/** Volume at price: horizontal bars by level, value area highlighted, POC and last price marked. */
export function profileChart(profile) {
  const W = canvasWidth(), H = 360, pad = { l: 8, r: 64, t: 8, b: 8 };
  const { svg, inner } = frame(W, H, pad);
  const bins = profile.bins;
  const lo = bins[0].low, hi = bins[bins.length - 1].high;
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  const maxVol = Math.max(...bins.map((b) => b.volume)) || 1;
  const x = (v) => inner.l + ((inner.r - inner.l) * v) / maxVol;
  for (const b of bins) {
    const top = y(b.high), hgt = Math.max(1, y(b.low) - y(b.high) - 1);
    svg.append(s("rect", { x: inner.l, y: top, width: Math.max(0, x(b.volume) - inner.l), height: hgt,
      class: b.in_value_area ? "pv-in" : "pv-out" }));
  }
  const mark = (price, cls, label) => {
    svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(price), y2: y(price), class: cls }));
    const t = s("text", { x: inner.r + 6, y: y(price) + 4 });
    t.textContent = `${label} ${price.toFixed(2)}`;
    svg.append(t);
  };
  mark(profile.value_area_high, "ref", "VAH");
  mark(profile.value_area_low, "ref", "VAL");
  mark(profile.poc, "poc", "POC");
  mark(profile.price, "last", "Last");
  svg.setAttribute("aria-label", `Volume profile, point of control ${profile.poc}`);
  return svg;
}

/** Realised-volatility cone: p10–p90 band and median per window, with today's reading. */
export function coneChart(cone) {
  const W = canvasWidth(), H = 220, pad = { l: 8, r: 56, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const vals = cone.flatMap((c) => [c.p10, c.p90, c.current]).filter((v) => v !== null);
  const [, hi] = extent(vals);
  const lo = 0;
  const n = cone.length;
  const x = (i) => inner.l + 30 + ((inner.r - inner.l - 60) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => `${(v * 100).toFixed(0)}%`);
  const band = "M" + cone.map((c, i) => `${x(i)},${y(c.p90)}`).join("L") + "L" +
    [...cone].reverse().map((c, j) => `${x(n - 1 - j)},${y(c.p10)}`).join("L") + "Z";
  svg.append(s("path", { d: band, class: "band" }));
  svg.append(s("path", { d: path(cone.map((c) => c.p50), x, y), class: "sma20" }));
  svg.append(s("path", { d: path(cone.map((c) => c.current), x, y), class: "price" }));
  cone.forEach((c, i) => {
    if (c.current !== null) svg.append(s("circle", { cx: x(i), cy: y(c.current), r: 4, class: "dot" }));
    const t = s("text", { x: x(i), y: inner.b + 16, "text-anchor": "middle" });
    t.textContent = `${c.window}d`;
    svg.append(t);
  });
  svg.setAttribute("aria-label", "Realised volatility cone");
  return svg;
}

/** A forecast term structure: one line over horizons, with a dashed long-run level. */
export function termChart(points, longRun) {
  const W = canvasWidth(), H = 200, pad = { l: 8, r: 56, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const vals = points.map((p) => p.vol_annual);
  const [lo, hi] = extent([...vals, longRun ?? vals[0]]);
  const n = points.length;
  const x = (i) => inner.l + 30 + ((inner.r - inner.l - 60) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => `${(v * 100).toFixed(1)}%`);
  if (longRun) svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(longRun), y2: y(longRun), class: "ref" }));
  svg.append(s("path", { d: path(vals, x, y), class: "price" }));
  points.forEach((p, i) => {
    svg.append(s("circle", { cx: x(i), cy: y(p.vol_annual), r: 3.5, class: "dot" }));
    const t = s("text", { x: x(i), y: inner.b + 16, "text-anchor": "middle" });
    t.textContent = `${p.sessions}d`;
    svg.append(t);
  });
  svg.setAttribute("aria-label", "GARCH volatility forecast by horizon");
  return svg;
}

/** Shape analogs: today's z-normalised path in bold over its nearest historical matches. */
export function analogChart(analogs) {
  const W = canvasWidth(), H = 220, pad = { l: 8, r: 56, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const all = [analogs.query.path, ...analogs.matches.map((m) => m.path)].flat();
  const [lo, hi] = extent(all);
  const n = analogs.query.path.length;
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => `${v.toFixed(1)}σ`, 2);
  for (const m of analogs.matches) svg.append(s("path", { d: path(m.path, x, y), class: "analog" }));
  svg.append(s("path", { d: path(analogs.query.path, x, y), class: "price" }));
  svg.setAttribute("aria-label", `Today's ${n}-session path against ${analogs.matches.length} historical analogs`);
  return svg;
}

/** A labelled square heatmap for a correlation matrix on [-1, 1]. */
export function heatmap(labels, matrix) {
  const n = labels.length;
  const cell = Math.max(18, Math.min(44, Math.floor(560 / n)));
  const left = 56, top = 8;
  const W = left + cell * n + 8, H = top + cell * n + 56;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart heat", role: "img" });
  matrix.forEach((row, i) => row.forEach((v, j) => {
    const r = s("rect", { x: left + j * cell, y: top + i * cell, width: cell - 1, height: cell - 1,
      class: v >= 0 ? "hm-up" : "hm-down", "fill-opacity": Math.min(1, Math.abs(v)).toFixed(2) });
    const tip = s("title");
    tip.textContent = `${labels[i]} · ${labels[j]}: ${v.toFixed(2)}`;
    r.append(tip);
    svg.append(r);
    if (cell >= 30) {
      const t = s("text", { x: left + j * cell + cell / 2, y: top + i * cell + cell / 2 + 4, "text-anchor": "middle", class: "hm-val" });
      t.textContent = v.toFixed(2).replace("0.", ".");
      svg.append(t);
    }
  }));
  labels.forEach((l, i) => {
    const a = s("text", { x: left - 6, y: top + i * cell + cell / 2 + 4, "text-anchor": "end" });
    a.textContent = l;
    const b = s("text", { x: left + i * cell + cell / 2, y: top + n * cell + 14, "text-anchor": "end",
      transform: `rotate(-45 ${left + i * cell + cell / 2} ${top + n * cell + 14})` });
    b.textContent = l;
    svg.append(a, b);
  });
  svg.setAttribute("aria-label", `Correlation matrix of ${n} symbols`);
  return svg;
}

/** Eigenvalues as bars against the Marchenko–Pastur noise band. */
export function spectrumChart(spectrum) {
  const W = canvasWidth(), H = 220, pad = { l: 8, r: 56, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const ev = spectrum.eigenvalues;
  const hi = Math.max(...ev, spectrum.mp_upper) * 1.08, lo = 0;
  const n = ev.length;
  const bw = (inner.r - inner.l) / n;
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => v.toFixed(1));
  svg.append(s("rect", { x: inner.l, y: y(spectrum.mp_upper), width: inner.r - inner.l,
    height: y(spectrum.mp_lower) - y(spectrum.mp_upper), class: "band" }));
  ev.forEach((v, i) => {
    svg.append(s("rect", { x: inner.l + i * bw + 2, y: y(v), width: Math.max(1, bw - 4), height: inner.b - y(v),
      class: v > spectrum.mp_upper ? "bar-up" : "bar-flat" }));
  });
  svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(spectrum.mp_upper), y2: y(spectrum.mp_upper), class: "ref" }));
  svg.setAttribute("aria-label", `${spectrum.signal_factors} eigenvalue(s) above the Marchenko–Pastur noise edge`);
  return svg;
}

/** Reliability diagram: predicted probability against observed frequency, with Wilson bars. */
export function reliabilityChart(bins) {
  const W = 420, H = 320, pad = { l: 44, r: 12, t: 12, b: 36 };
  const { svg, inner } = frame(W, H, pad);
  const lo = 0.25, hi = 0.75;
  const x = (v) => inner.l + ((inner.r - inner.l) * (v - lo)) / (hi - lo);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  for (const v of [0.3, 0.4, 0.5, 0.6, 0.7]) {
    svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(v), y2: y(v), class: "grid-line" }));
    const ty = s("text", { x: inner.l - 6, y: y(v) + 4, "text-anchor": "end" }); ty.textContent = `${v * 100}%`;
    const tx = s("text", { x: x(v), y: inner.b + 16, "text-anchor": "middle" }); tx.textContent = `${v * 100}%`;
    svg.append(ty, tx);
  }
  svg.append(s("line", { x1: x(lo), y1: y(lo), x2: x(hi), y2: y(hi), class: "ref" }));
  const label = s("text", { x: (inner.l + inner.r) / 2, y: H - 4, "text-anchor": "middle" }); label.textContent = "forecast P(up)";
  svg.append(label);
  const clamp = (v) => Math.max(lo, Math.min(hi, v));
  for (const b of bins) {
    if (!b.n || b.mean_predicted === null || b.observed_up === null) continue;
    const cx = x(clamp(b.mean_predicted));
    if (b.wilson_95) svg.append(s("line", { x1: cx, x2: cx, y1: y(clamp(b.wilson_95.low)), y2: y(clamp(b.wilson_95.high)), class: "errbar" }));
    const dot = s("circle", { cx, cy: y(clamp(b.observed_up)), r: Math.max(3, Math.min(10, Math.sqrt(b.n) / 2)), class: b.reliable ? "dot" : "dot-faint" });
    const tip = s("title"); tip.textContent = `${b.bin}: predicted ${(b.mean_predicted * 100).toFixed(1)}%, observed ${(b.observed_up * 100).toFixed(1)}% (n=${b.n})`;
    dot.append(tip);
    svg.append(dot);
  }
  svg.setAttribute("aria-label", "Reliability diagram: points on the diagonal are calibrated");
  return svg;
}

/** Several named series over shared dates. `series` is [{label, values, cls}]. */
export function multiLine(dates, series, { fmt = (v) => v.toFixed(2), zero = false, height = 240 } = {}) {
  const W = canvasWidth(), H = height, pad = { l: 8, r: 60, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const [lo, hi] = extent([...series.flatMap((x) => x.values), ...(zero ? [0] : [])]);
  const n = dates.length;
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, fmt);
  if (zero && lo < 0 && hi > 0) svg.append(s("line", { x1: inner.l, x2: inner.r, y1: y(0), y2: y(0), class: "ref" }));
  for (const sr of series) svg.append(s("path", { d: path(sr.values, x, y), class: sr.cls }));
  xLabels(svg, inner, dates, x, W < 600 ? 3 : 5);
  svg.setAttribute("aria-label", series.map((x) => x.label).join(", "));
  return svg;
}

/** Price fan: p5–p95 and p25–p75 bands with the median, by session ahead. */
export function fanChart(fan, last) {
  const W = canvasWidth(), H = 240, pad = { l: 8, r: 60, t: 10, b: 24 };
  const { svg, inner } = frame(W, H, pad);
  const pts = [{ session: 0, p5: last, p25: last, p50: last, p75: last, p95: last }, ...fan];
  const [lo, hi] = extent(pts.flatMap((p) => [p.p5, p.p95]));
  const n = pts.length;
  const x = (i) => inner.l + ((inner.r - inner.l) * i) / Math.max(1, n - 1);
  const y = (v) => inner.b - ((inner.b - inner.t) * (v - lo)) / (hi - lo);
  yAxis(svg, inner, lo, hi, y, (v) => v.toFixed(hi - lo < 20 ? 2 : 0));
  const band = (a, b, cls) => {
    const d = "M" + pts.map((p, i) => `${x(i)},${y(p[b])}`).join("L") + "L" + [...pts].reverse().map((p, j) => `${x(n - 1 - j)},${y(p[a])}`).join("L") + "Z";
    svg.append(s("path", { d, class: cls }));
  };
  band("p5", "p95", "band");
  band("p25", "p75", "band band-inner");
  svg.append(s("path", { d: path(pts.map((p) => p.p50), x, y), class: "price" }));
  for (const k of [5, 21, 63]) {
    if (k >= n) continue;
    const t = s("text", { x: x(k), y: inner.b + 16, "text-anchor": "middle" }); t.textContent = `+${k}d`;
    svg.append(t);
  }
  svg.setAttribute("aria-label", "Simulated price distribution by sessions ahead");
  return svg;
}
