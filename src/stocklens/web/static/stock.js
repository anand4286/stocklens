// The stock page: /s/{SYMBOL}. One API call renders every tab; the analyst
// note is a second, explicit call because it may cost money.
"use strict";

import { DASH, api, compact, h, money, num, pct, signed, tone, wireSearch } from "./common.js";
import { macdChart, oscillator, priceChart, tail } from "./charts.js";
import { callCard, driversTab, evidenceTab, quant, research, structure } from "./panels.js";

const TABS = [
  ["overview", "Overview"], ["technicals", "Technicals"], ["fundamentals", "Fundamentals"],
  ["research", "Research"], ["evidence", "Evidence"], ["drivers", "Drivers"], ["risk", "Risk"],
  ["quant", "Quant"], ["structure", "Structure"], ["news", "News"], ["analyst", "AI Analyst"],
];
const FAMILY_LABEL = {
  technical: "Technical", momentum: "Momentum", relative_strength: "Relative strength",
  fundamental: "Fundamental", valuation: "Valuation", sentiment: "Sentiment",
  volume: "Volume", news_flow: "News flow",
};

const root = document.getElementById("app");
const symbol = decodeURIComponent(location.pathname.split("/")[2] || "").toUpperCase();
document.title = `${symbol} · StockLens`;
wireSearch();
document.querySelector("form.search input").value = symbol;

main().catch((err) => {
  root.replaceChildren(h("div", { class: "wrap" }, h("p", { class: "error", role: "alert" },
    err.status === 404 ? `No data for ${symbol}.` : err.status === 401 ? "This deployment needs an API key." : `Could not load ${symbol}: ${err.message}`)));
});

async function main() {
  const [report, meta] = await Promise.all([
    api(`/api/v1/symbols/${encodeURIComponent(symbol)}/report`),
    api("/api/v1/meta"),
  ]);
  if (meta.demo) document.getElementById("demo-banner").hidden = false;

  const panels = {
    overview: overview(report), technicals: technicals(report), fundamentals: fundamentals(report),
    research: research(report), news: news(report), risk: risk(report), quant: quant(report),
    drivers: driversTab(report), evidence: null,
    structure: structure(report), analyst: analyst(report, meta),
  };
  const tabBar = h("div", { class: "tabs", role: "tablist" });
  const body = h("div");
  const select = (key) => {
    for (const btn of tabBar.children) btn.setAttribute("aria-selected", String(btn.dataset.key === key));
    // The evidence lab re-scores the whole history, so it is fetched on first open.
    if (key === "evidence" && !panels.evidence) panels.evidence = evidenceTab(report);
    body.replaceChildren(panels[key]);
    history.replaceState(null, "", `#${key}`);
  };
  for (const [key, label] of TABS) {
    const btn = h("button", { role: "tab", "data-key": key, onclick: () => select(key) }, label);
    tabBar.append(btn);
  }
  root.replaceChildren(h("div", { class: "wrap" }, header(report), composite(report), callCard(report), tabBar, body));
  const initial = location.hash.slice(1);
  select(TABS.some(([k]) => k === initial) ? initial : "overview");
}

function header(r) {
  const q = r.quote;
  const company = r.fundamentals?.company;
  return h("section", { class: "head" },
    h("div", {}, h("h1", {}, r.symbol), company ? h("div", { class: "company" }, company) : null),
    h("div", {},
      h("div", { class: "price" }, num(q.price)),
      h("span", { class: `badge ${tone(q.change_pct)}` }, `${signed(q.change)} (${pct(q.change_pct, 2, true)})`)),
    h("div", { class: "asof" },
      `As of ${q.as_of} close · prices: ${r.sources.prices} · fundamentals: ${r.sources.fundamentals ?? "none"} · news: ${r.sources.news ?? "none"}`));
}

function composite(r) {
  const c = r.composite;
  const pin = h("span", { class: "pin", "aria-hidden": "true" });
  pin.style.left = `${((c.score + 1) / 2) * 100}%`;
  const stanceTone = c.stance === "Constructive" ? "up" : c.stance === "Cautious" ? "down" : "flat";
  return h("section", { class: "card composite", "aria-label": "Composite score" },
    h("div", {}, h("div", { class: "score-big" }, signed(c.score)), h("span", { class: `badge ${stanceTone}` }, c.stance)),
    h("div", {},
      h("div", { class: "scale", role: "meter", "aria-valuemin": -1, "aria-valuemax": 1, "aria-valuenow": c.score }, pin),
      h("div", { class: "scale-labels" }, h("span", {}, "−1 cautious"), h("span", {}, "0"), h("span", {}, "+1 constructive")),
      h("p", { class: "small muted" },
        `Confidence ${pct(c.confidence, 0)} (evidence coverage ${pct(c.coverage, 0)}` +
        (c.conviction === null ? "" : `, conviction ${signed(c.conviction)}`) + "). " +
        `Rule: constructive at ≥ ${c.rule.constructive_at}, cautious at ≤ ${c.rule.cautious_at}, ` +
        `and no stance below ${pct(c.rule.min_confidence, 0)} confidence. Weighted mean of the available directional families` +
        (c.regime ? `, with weights ${c.regime === "random" ? "as published (no regime detected)" : `tilted for a ${c.regime.replace("_", "-")} regime — see Quant`}.` : "."))));
}

function kv(pairs) {
  return h("div", { class: "kv" }, pairs.map(([label, value, hint]) =>
    h("div", { title: hint || null }, h("dl", {}, h("dt", {}, label), h("dd", {}, value)))));
}

function dbar(score) {
  const bar = h("div", { class: "dbar" }, h("span", { class: "mid" }));
  if (score !== null) {
    const fill = h("span", { class: `fill ${score >= 0 ? "up" : "down"}` });
    const w = Math.abs(score) * 50;
    fill.style.width = `${w}%`;
    fill.style.left = score >= 0 ? "50%" : `${50 - w}%`;
    bar.append(fill);
  }
  return bar;
}

function overview(r) {
  const ranges = [["3M", 63], ["6M", 126], ["1Y", 252]];
  const holder = h("div");
  const readout = h("div", { class: "readout", "aria-live": "polite" });
  const rangeBar = h("div", { class: "range", role: "group", "aria-label": "Chart range" });
  const draw = (count) => {
    const series = tail(r.chart, count);
    holder.replaceChildren(priceChart(series, {
      onHover: (i) => {
        readout.textContent = i === null ? "" :
          `${series.dates[i]}  O ${num(series.open[i])}  H ${num(series.high[i])}  L ${num(series.low[i])}  C ${num(series.close[i])}  Vol ${compact(series.volume[i])}`;
      },
    }));
    for (const b of rangeBar.children) b.setAttribute("aria-pressed", String(Number(b.dataset.n) === count));
  };
  for (const [label, n] of ranges) rangeBar.append(h("button", { "data-n": n, onclick: () => draw(n) }, label));
  draw(126);

  const q = r.quote;
  const rows = r.composite.contributions.map((c) => h("tr", {},
    h("td", { class: "name" }, FAMILY_LABEL[c.name] || c.name, " ", c.kind === "conviction" ? h("span", { class: "tag" }, "conviction") : null),
    h("td", { class: "bar" }, dbar(c.score)),
    h("td", { class: "num" }, c.score === null ? h("span", { class: "na" }, "n/a") : signed(c.score)),
    h("td", { class: "small" }, c.reason, c.kind === "directional" && c.score !== null ? h("div", { class: "tiny muted" }, `weight ${pct(c.effective_weight, 0)} → contributes ${signed(c.contribution, 3)}`) : null)));

  return h("div", { class: "grid" },
    h("section", { class: "card span-8" },
      h("div", { class: "card-head" }, h("h2", {}, "Price"), rangeBar),
      holder, readout,
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "c1" }), "Close"), h("span", {}, h("i", { class: "c2" }), "SMA 20"),
        h("span", {}, h("i", { class: "c3" }), "SMA 50"), h("span", {}, h("i", { class: "cb" }), "Bollinger 20, 2σ"))),
    h("section", { class: "card span-4" }, h("h2", {}, "Key figures"), kv([
      ["Open", num(q.open)], ["Day range", `${num(q.low)} – ${num(q.high)}`],
      ["52-week range", `${num(q.low_52w)} – ${num(q.high_52w)}`], ["Volume", compact(q.volume)],
      ["1 month", pct(q.return_1m, 1, true)], ["3 months", pct(q.return_3m, 1, true)],
      ["1 year", pct(q.return_1y, 1, true)], ["Volatility (EWMA)", pct(r.risk.volatility_ewma, 0), "RiskMetrics λ=0.94, annualised"],
    ])),
    h("section", { class: "card span-12" },
      h("h2", {}, "Why the score is what it is"),
      h("p", { class: "small muted" }, "Each family scores −1 to +1 or abstains. Contributions of the directional families sum to the composite; conviction families scale confidence instead."),
      h("div", { class: "table-scroll" }, h("table", { class: "contrib" }, h("tbody", {}, rows)))));
}

function technicals(r) {
  const t = r.technicals;
  const series = tail(r.chart, 126);
  const bb = t.bollinger || [null, null, null];
  const price = r.quote.price;
  const rel = (v) => (v ? pct(price / v - 1, 1, true) : DASH);
  return h("div", { class: "grid" },
    h("section", { class: "card span-6" }, h("h2", {}, "RSI (14)"), oscillator(series, series.rsi14),
      h("p", { class: "small muted" }, `Latest ${num(t.rsi14, 1)}. Wilder's RSI; 30 and 70 are the conventional oversold and overbought lines.`)),
    h("section", { class: "card span-6" }, h("h2", {}, "MACD (12, 26, 9)"), macdChart(series),
      h("p", { class: "small muted" }, `Histogram ${num(t.macd_histogram, 3)}. Bars are MACD minus its signal line.`)),
    h("section", { class: "card span-12" }, h("h2", {}, "Indicators"),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Indicator"), h("th", { class: "num" }, "Value"), h("th", { class: "num" }, "Price vs"), h("th", {}, "Reading"))),
        h("tbody", {},
          row("SMA 20", num(t.sma20), rel(t.sma20), price > t.sma20 ? "Above short-term trend" : "Below short-term trend"),
          row("SMA 50", num(t.sma50), rel(t.sma50), price > t.sma50 ? "Above medium-term trend" : "Below medium-term trend"),
          row("SMA 200", num(t.sma200), rel(t.sma200), t.sma200 === null ? "Needs 200 sessions" : price > t.sma200 ? "Above long-term trend" : "Below long-term trend"),
          row("Bollinger (20, 2σ)", `${num(bb[0])} – ${num(bb[2])}`, DASH, bb[0] === null ? DASH : price > bb[2] ? "Above upper band" : price < bb[0] ? "Below lower band" : "Inside the bands"),
          row("Stochastic %K (14)", num(t.stochastic_k, 1), DASH, t.stochastic_k > 80 ? "Near the 14-day high" : t.stochastic_k < 20 ? "Near the 14-day low" : "Mid-range"),
          row("ATR (14)", num(t.atr14), DASH, `${pct(t.atr14 / price, 1)} of price per session`))))));
}

function row(...cells) {
  return h("tr", {}, cells.map((c, i) => h("td", { class: i === 1 || i === 2 ? "num" : null }, c)));
}

function fundamentals(r) {
  const f = r.fundamentals;
  if (!f) {
    return h("section", { class: "card" }, h("h2", {}, "Fundamentals"),
      h("p", { class: "muted" }, "No filing data for this symbol. Funds and some foreign issuers do not file XBRL financials with the SEC; the fundamental and valuation families abstain rather than guess."));
  }
  const fcf = f.operating_cash_flow !== null && f.capex !== null ? f.operating_cash_flow - f.capex : null;
  const figures = [
    ["Revenue", money(f.revenue)], ["Prior-year revenue", money(f.revenue_prior)], ["Gross profit", money(f.gross_profit)],
    ["Operating income", money(f.operating_income)], ["Net income", money(f.net_income)], ["Operating cash flow", money(f.operating_cash_flow)],
    ["Capex", money(f.capex)], ["Free cash flow", money(fcf)], ["Total assets", money(f.total_assets)],
    ["Total liabilities", money(f.total_liabilities)], ["Long-term debt", money(f.long_term_debt)], ["Cash", money(f.cash)],
    ["Equity", money(f.equity)], ["Shares outstanding", compact(f.shares_outstanding)],
  ];
  const fmtCheck = (c) => {
    if (c.value === null) return DASH;
    return ["profitable", "free_cash_flow"].includes(c.name) ? money(c.value) : c.name.endsWith("ratio") || c.name === "debt_to_equity" || c.name === "cash_conversion" ? `${num(c.value)}×` : pct(c.value, 1);
  };
  return h("div", { class: "grid" },
    h("section", { class: "card span-7" }, h("h2", {}, `Annual figures · FY${f.fiscal_year ?? DASH}`),
      h("p", { class: "small muted" }, `${f.company}. Source: ${f.source}. Absent means not reported, not zero.`), kv(figures)),
    h("section", { class: "card span-5" }, h("h2", {}, "Balance-sheet checks"),
      h("ul", { class: "checks" }, f.checks.map((c) => h("li", {},
        h("span", { class: `mark badge ${c.passed === null ? "flat" : c.passed ? "up" : "down"}` }, c.passed === null ? "unknown" : c.passed ? "pass" : "fail"),
        h("span", {}, c.rule, h("div", { class: "tiny muted" }, `measured: ${fmtCheck(c)}`)))))));
}

function news(r) {
  if (!r.news.length) return h("section", { class: "card" }, h("h2", {}, "News"), h("p", { class: "muted" }, "No coverage found in the last two weeks."));
  return h("section", { class: "card" }, h("h2", {}, "Recent headlines"),
    h("p", { class: "small muted" }, "Tone is a transparent finance lexicon score (after Loughran & McDonald), −1 to +1; blank means no tonal words."),
    h("ul", { class: "news" }, r.news.map((n) => h("li", {},
      h("span", { class: `badge ${tone(n.tone, 0)}` }, n.tone === null ? "·" : signed(n.tone, 1)),
      h("div", {}, n.url ? h("a", { href: n.url, rel: "noopener noreferrer nofollow", target: "_blank" }, n.headline) : n.headline,
        h("div", { class: "meta" }, `${n.source} · ${n.published.replace("T", " ").slice(0, 16)} UTC`))))));
}

function risk(r) {
  const k = r.risk;
  return h("div", { class: "grid" },
    h("section", { class: "card span-7" }, h("h2", {}, "Risk and return (trailing year)"), kv([
      ["Volatility", pct(k.volatility_annual, 1), "Annualised standard deviation of daily log returns"],
      ["Volatility (EWMA)", pct(k.volatility_ewma, 1), "RiskMetrics λ = 0.94"],
      ["Max drawdown", pct(k.max_drawdown, 1), "Worst peak-to-trough over the last year"],
      ["VaR 95%, 1 day", pct(k.var_95_1d, 2), "Historical, not parametric"],
      ["Expected shortfall 95%", pct(k.es_95_1d, 2), "Average loss on the worst 5% of days"],
      ["Sharpe", num(k.sharpe), "Zero risk-free rate"], ["Sortino", num(k.sortino)],
      [`Beta vs ${r.benchmark}`, num(k.beta)],
      ["Hurst exponent", num(k.hurst, 3), "≈0.5 random walk, >0.5 trending, <0.5 mean-reverting"],
      ["Variance ratio (5d)", num(k.variance_ratio_5d, 3), "Lo–MacKinlay: 1 is a random walk"],
    ]), h("p", { class: "tiny muted" }, `${k.observations} daily observations. Figures below their sample floor show ${DASH}.`)),
    h("section", { class: "card span-5" }, h("h2", {}, "One-sigma range"),
      h("p", { class: "small muted" }, "How far the price could plausibly move, from EWMA volatility — a statement about spread, not direction."),
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Horizon"), h("th", { class: "num" }, "Low"), h("th", { class: "num" }, "High"), h("th", { class: "num" }, "±σ"))),
        h("tbody", {}, k.range_cone.map((c) => h("tr", {}, h("td", {}, `${c.sessions} sessions`), h("td", { class: "num" }, num(c.low)), h("td", { class: "num" }, num(c.high)), h("td", { class: "num" }, pct(c.sigma_pct, 1))))))));
}

function analyst(r, meta) {
  const out = h("div");
  const button = h("button", { class: "btn" }, "Generate analyst note");
  const status = h("p", { class: "small muted", "aria-live": "polite" });
  const mode = meta.agent.mode === "model"
    ? `Claude (${meta.agent.model}) drives read-only tools over this page's numbers, then submits a schema-validated note. Every figure it cites is checked against what the tools returned.`
    : "No model is configured on this deployment, so a deterministic rules engine writes the brief from the same numbers. Configure an Anthropic key to enable the agent.";
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Working…";
    try {
      const run = await api(`/api/v1/symbols/${encodeURIComponent(r.symbol)}/analyst`, { method: "POST" });
      status.textContent = run.note || (run.cached ? "Showing a recent run (cached to cap cost)." : "");
      out.replaceChildren(note(run));
    } catch (err) {
      status.textContent = "";
      out.replaceChildren(h("p", { class: "error", role: "alert" }, err.status === 429 ? "Rate limited — try again in a minute." : err.message));
    } finally {
      button.disabled = false;
    }
  });
  return h("div", { class: "grid" },
    h("section", { class: "card span-12" }, h("h2", {}, "AI analyst"), h("p", { class: "small muted" }, mode), button, status),
    h("div", { class: "span-12" }, out));
}

function list(items) {
  return items.length ? h("ul", {}, items.map((t) => h("li", {}, t))) : h("p", { class: "muted small" }, "None identified.");
}

function note(run) {
  const rep = run.report;
  const g = run.grounding;
  const stanceTone = rep.stance === "constructive" ? "up" : rep.stance === "cautious" ? "down" : "flat";
  return h("div", { class: "grid" },
    h("section", { class: "card span-8 note" },
      h("span", { class: `badge ${stanceTone}` }, rep.stance.replace("_", " ")), " ",
      h("span", { class: "tag" }, run.mode === "model" ? `written by ${run.model}` : "written by rules"),
      h("h2", {}, rep.headline), h("p", {}, rep.summary),
      h("h3", {}, "Supporting"), list(rep.bull_points),
      h("h3", {}, "Against"), list(rep.bear_points),
      h("h3", {}, "Key risks"), list(rep.key_risks),
      h("p", { class: "tiny muted" }, "Educational analytics, not investment advice.")),
    h("section", { class: "card span-4" },
      h("h2", {}, "Verification"),
      h("p", { class: "small" }, h("strong", { class: g.score === 1 ? "ok" : "bad" }, `${g.verified} of ${g.total}`), " cited figures match the tool outputs."),
      h("ul", { class: "checks" }, g.checks.map((c) => h("li", {},
        h("span", { class: c.verified ? "ok" : "bad" }, c.verified ? "✓" : "✗"),
        h("span", {}, h("code", {}, c.metric), ` ${c.cited}`, c.verified ? null : h("div", { class: "tiny bad" }, c.why))))),
      h("h3", {}, "Trace"),
      h("ol", { class: "trace" }, run.tool_calls.map((c) => h("li", {},
        h("span", { class: c.ok ? "ok" : "bad" }, c.ok ? "●" : "○"),
        h("code", {}, c.tool), c.step ? h("span", { class: "muted tiny" }, `step ${c.step}`) : null))),
      h("p", { class: "tiny muted" },
        `${run.steps} model step(s) · ${run.input_tokens.toLocaleString()} in / ${run.output_tokens.toLocaleString()} out tokens · ${run.latency_ms} ms`)));
}
