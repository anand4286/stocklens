// The portfolio lab: /portfolio?symbols=AAPL,MSFT,... One call returns the
// correlation structure, its spectrum against random-matrix noise, and five
// construction methods side by side, so the differences between them are the
// thing on the page rather than any one answer.
"use strict";

import { api, h, num, pct, wireSearch } from "./common.js";
import { heatmap, spectrumChart } from "./charts.js";

const DEFAULT = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,KO,PFE";
const ORDER = ["equal", "inverse_vol", "min_variance", "risk_parity", "hrp"];
const TICKER = /^[A-Z]{1,5}([.-][A-Z]{1,2})?$/;

const root = document.getElementById("app");
wireSearch();

const params = new URLSearchParams(location.search);
const initial = (params.get("symbols") || DEFAULT).toUpperCase();
const input = h("input", { value: initial, "aria-label": "Symbols, comma separated", spellcheck: "false", maxlength: "220" });
const form = h("form", { class: "port-form", onsubmit: (e) => { e.preventDefault(); run(input.value); } },
  input, h("button", { class: "btn", type: "submit" }, "Analyse"));
const out = h("div");
root.replaceChildren(
  h("section", { class: "hero" },
    h("h1", {}, "Portfolio lab"),
    h("p", {}, "How many independent bets a basket really is, and how five construction methods would weight it — from a shrunk covariance matrix, with the noise floor drawn in.")),
  h("section", { class: "card" }, h("h2", {}, "Symbols"), form,
    h("p", { class: "tiny muted" }, "2 to 25 US tickers. Daily closes, trailing year, log returns.")),
  out);
run(initial);

async function run(raw) {
  const symbols = [...new Set(raw.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean))];
  const bad = symbols.filter((s) => !TICKER.test(s));
  if (bad.length || symbols.length < 2) {
    out.replaceChildren(h("p", { class: "error", role: "alert" }, bad.length ? `Not a ticker: ${bad.join(", ")}` : "Enter at least two symbols."));
    return;
  }
  history.replaceState(null, "", `?symbols=${symbols.join(",")}`);
  out.replaceChildren(h("p", { class: "loading" }, "Estimating…"));
  try {
    const [data, meta] = await Promise.all([api(`/api/v1/portfolio?symbols=${encodeURIComponent(symbols.join(","))}`), api("/api/v1/meta")]);
    if (meta.demo) document.getElementById("demo-banner").hidden = false;
    out.replaceChildren(render(data));
  } catch (err) {
    out.replaceChildren(h("p", { class: "error", role: "alert" }, err.message));
  }
}

function render(d) {
  const sp = d.spectrum;
  const methods = ORDER.filter((k) => d.methods[k]).map((k) => [k, d.methods[k]]);
  const symbols = d.hrp?.order?.length ? d.hrp.order : d.symbols;
  const idx = symbols.map((s) => d.symbols.indexOf(s));
  const ordered = idx.map((i) => idx.map((j) => d.correlation[i][j]));

  const weightRows = symbols.map((sym) => h("tr", {}, h("td", { class: "name" }, h("a", { href: `/s/${sym}` }, sym)),
    methods.map(([, m]) => {
      const w = m.weights[sym] ?? 0;
      const bar = h("span", { class: "wbar" });
      bar.style.width = `${Math.round(w * 120)}px`;
      return h("td", { class: "num" }, bar, " ", pct(w, 1), h("div", { class: "tiny muted" }, `risk ${pct(m.risk_contributions[sym], 0)}`));
    })));
  const stat = (label, f) => h("tr", {}, h("th", {}, label), methods.map(([, m]) => h("td", { class: "num" }, f(m))));

  return h("div", { class: "grid" },
    h("section", { class: "card span-12" }, h("p", { class: "reading" }, d.reading)),
    h("section", { class: "card span-6" },
      h("h2", {}, "Correlation, clustered"),
      h("p", { class: "small muted" }, "Sample correlation of daily log returns, rows in hierarchical-risk-parity order so correlated names sit together."),
      heatmap(symbols, ordered)),
    h("section", { class: "card span-6" },
      h("h2", {}, "Spectrum against noise"),
      h("p", { class: "small muted" }, `Eigenvalues of the correlation matrix. The shaded band is the Marchenko–Pastur range a matrix of pure noise would produce at q = N/T = ${num(sp.q, 3)}; only bars above it are structure.`),
      spectrumChart(sp),
      h("div", { class: "kv" },
        ...[["Factors above noise", String(sp.signal_factors)], ["First factor share", pct(sp.first_factor_share, 0)],
          ["MP upper edge", num(sp.mp_upper, 2)], ["Ledoit–Wolf intensity", num(d.shrinkage.intensity, 3)],
          ["Condition no. (sample)", num(sp.condition_number_sample, 0)], ["Condition no. (shrunk)", num(sp.condition_number_shrunk, 0)]]
          .map(([k, v]) => h("div", {}, h("dl", {}, h("dt", {}, k), h("dd", {}, v)))))),
    h("section", { class: "card span-12" },
      h("h2", {}, "Five ways to weight the same basket"),
      h("p", { class: "small muted" }, "Each column is a construction rule. Minimum variance, risk parity and HRP use the Ledoit–Wolf shrunk covariance — a raw sample matrix would hand the optimiser its own estimation noise. Effective bets is Meucci's entropy measure over uncorrelated principal directions."),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Symbol"), methods.map(([, m]) => h("th", { class: "num", title: m.rule }, m.label)))),
        h("tbody", {}, weightRows),
        h("tfoot", {},
          stat("Volatility", (m) => pct(m.volatility_annual, 1)),
          stat("Effective bets", (m) => num(m.effective_bets, 2)),
          stat("Diversification ratio", (m) => num(m.diversification_ratio, 2)),
          stat("Largest weight", (m) => pct(m.max_weight, 0))))),
      h("ul", { class: "small muted" }, methods.map(([, m]) => h("li", {}, h("strong", {}, m.label), `: ${m.rule}`))),
      h("p", { class: "tiny muted" }, `${d.observations} aligned daily returns · ${d.symbols.length} symbols. Educational analytics: weights describe the estimators, they are not a recommendation.`)));
}
