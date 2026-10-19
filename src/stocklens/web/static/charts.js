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
