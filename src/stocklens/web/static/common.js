// Shared helpers. Every DOM node is built with textContent, never innerHTML,
// so text from an API or a news feed cannot become markup.
"use strict";

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export const DASH = "—";

export function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(value, digits = 1, signed = false) {
  if (value === null || value === undefined) return DASH;
  const s = (value * 100).toFixed(digits) + "%";
  return signed && value > 0 ? "+" + s : s;
}

export function signed(value, digits = 2) {
  if (value === null || value === undefined) return DASH;
  return (value > 0 ? "+" : "") + Number(value).toFixed(digits);
}

export function money(value) {
  if (value === null || value === undefined) return DASH;
  const a = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  return `${sign}$${a.toLocaleString()}`;
}

export function compact(value) {
  if (value === null || value === undefined) return DASH;
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function tone(value, eps = 0.0005) {
  if (value === null || value === undefined) return "flat";
  return value > eps ? "up" : value < -eps ? "down" : "flat";
}

export async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  const key = sessionStorageGet("stocklens.key");
  if (key) headers.Authorization = `Bearer ${key}`;
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(body.detail || `HTTP ${response.status}`);
    err.status = response.status;
    throw err;
  }
  return body;
}

function sessionStorageGet(name) {
  try { return window.sessionStorage.getItem(name); } catch { return null; }
}

const TICKER = /^[A-Z]{1,5}([.-][A-Z]{1,2})?$/;

export function wireSearch() {
  const form = document.querySelector("form.search");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = form.querySelector("input");
    const symbol = input.value.trim().toUpperCase();
    if (!TICKER.test(symbol)) {
      input.setCustomValidity("Enter a ticker like AAPL or BRK.B");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    window.location.href = `/s/${encodeURIComponent(symbol)}`;
  });
  form.querySelector("input").addEventListener("input", (e) => e.target.setCustomValidity(""));
}
