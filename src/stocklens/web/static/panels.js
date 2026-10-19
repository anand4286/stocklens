// The deep-analysis tabs: Quant (volatility models, regime, inference),
// Structure (volume at price, shape analogs) and Research (forensic scores,
// valuation grid, derived debate). Same rules as the rest of the page: every
// number arrives with the rule that produced it, and a withheld figure shows
// a dash with the reason rather than a guess.
"use strict";

import { DASH, api, h, money, num, pct, signed } from "./common.js";
import { analogChart, coneChart, fanChart, multiLine, profileChart, termChart } from "./charts.js";

const TREND_LABEL = { trending: "Trending", mean_reverting: "Mean-reverting", random: "No exploitable structure" };
const FAMILY_LABEL = {
  technical: "Technical", momentum: "Momentum", relative_strength: "Relative strength",
  fundamental: "Fundamental", valuation: "Valuation", sentiment: "Sentiment",
};

function kv(pairs) {
  return h("div", { class: "kv" }, pairs.map(([label, value, hint]) =>
    h("div", { title: hint || null }, h("dl", {}, h("dt", {}, label), h("dd", {}, value)))));
}

function unavailable(title, why) {
  return h("section", { class: "card span-12" }, h("h2", {}, title), h("p", { class: "muted" }, why));
}

function badge(text, cls) {
  return h("span", { class: `badge ${cls}` }, text);
}

function yesNo(passed) {
  return passed === null || passed === undefined ? badge("unknown", "flat") : passed ? badge("pass", "up") : badge("fail", "down");
}

// -- Quant ------------------------------------------------------------------

export function quant(r) {
  const q = r.quant || {};
  const out = h("div", { class: "grid" });
  out.append(regimeCard(q.regime, r.composite));
  out.append(garchCard(q.volatility));
  out.append(coneCard(q.volatility));
  out.append(distributionCard(q.volatility));
  out.append(sharpeCard(q.sharpe));
  return out;
}

function regimeCard(g, comp) {
  if (!g) return unavailable("Regime", "Not enough history to classify the regime.");
  const tone = g.trend === "trending" ? "up" : g.trend === "mean_reverting" ? "down" : "flat";
  const tilt = comp.regime_weights ? Object.entries(comp.regime_weights).map(([k, w]) => {
    const base = comp.base_weights?.[k] ?? w;
    return h("tr", {}, h("td", {}, FAMILY_LABEL[k] || k), h("td", { class: "num" }, pct(base, 1)),
      h("td", { class: "num" }, pct(w, 1)), h("td", { class: "num" }, `×${num(g.multipliers?.[k] ?? 1, 2)}`));
  }) : [];
  return h("section", { class: "card span-12" },
    h("div", { class: "card-head" }, h("h2", {}, "Market regime"),
      h("div", {}, badge(TREND_LABEL[g.trend] || g.trend, tone), " ", badge(`${g.volatility} volatility`, "flat"))),
    h("p", { class: "reading" }, g.reason),
    kv([
      ["Hurst (corrected)", num(g.hurst, 3), "Anis–Lloyd small-sample corrected R/S. 0.5 is a random walk"],
      ["Variance ratio (5d)", num(g.variance_ratio, 3), "Lo–MacKinlay. 1 is a random walk"],
      ["Measures agree", g.agree ? "yes" : "no", "A regime is asserted only when both measures point the same way"],
      ["Vol percentile", pct(g.vol_percentile, 0), "Today's 21-day realised vol against its own history"],
    ]),
    tilt.length ? h("div", {},
      h("h3", {}, "Regime-conditioned weights"),
      h("p", { class: "small muted" }, "The same RSI reading means opposite things in a trending and a mean-reverting market, so the composite tilts its family weights by regime. The multipliers are picked, bounded to 0.5–1.5×, and leave the total weight unchanged."),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Family"), h("th", { class: "num" }, "Base"), h("th", { class: "num" }, "In this regime"), h("th", { class: "num" }, "Multiplier"))),
        h("tbody", {}, tilt)))) : null);
}

function garchCard(v) {
  const g = v?.garch;
  if (!g) return unavailable("GARCH(1,1)", "The maximum-likelihood fit needs more history, or did not converge to a stationary solution.");
  return h("section", { class: "card span-6" },
    h("h2", {}, "GARCH(1,1) volatility model"),
    h("p", { class: "small muted" }, "σ²ₜ = ω + α·ε²ₜ₋₁ + β·σ²ₜ₋₁, fitted by maximum likelihood. Persistence α+β says how long a shock lingers; the forecast decays from today's conditional vol toward the long-run level."),
    kv([
      ["α (shock)", num(g.alpha, 3)], ["β (memory)", num(g.beta, 3)],
      ["Persistence", num(g.persistence, 3)], ["Half-life", `${num(g.half_life_sessions, 1)} sessions`],
      ["Conditional vol now", pct(g.current_vol_annual, 1)], ["Long-run vol", pct(g.long_run_vol_annual, 1)],
    ]),
    v.forecast?.length ? termChart(v.forecast, g.long_run_vol_annual) : null,
    h("p", { class: "tiny muted" }, `Fitted on ${g.observations} daily returns · log-likelihood ${num(g.log_likelihood, 1)}. Dashed line: long-run vol.`));
}

function coneCard(v) {
  const re = v?.range_estimators || {};
  return h("section", { class: "card span-6" },
    h("h2", {}, "Volatility cone and range estimators"),
    v?.cone?.length ? coneChart(v.cone) : h("p", { class: "muted" }, "Not enough history for a cone."),
    h("p", { class: "small muted" }, "Band: 10th–90th percentile of rolling realised vol at each window over the history; amber line the median, blue today. A reading near the top of the cone has historically tended to fall back."),
    kv([
      ["Close-to-close", pct(re.close_to_close, 1), "Annualised, last 63 sessions"],
      ["Parkinson", pct(re.parkinson, 1), "High–low range estimator, ~5× more efficient than close-to-close"],
      ["Garman–Klass", pct(re.garman_klass, 1), "Uses open, high, low and close"],
    ]));
}

function distributionCard(v) {
  const d = v?.distribution;
  if (!d) return unavailable("Return distribution", "Not enough history.");
  return h("section", { class: "card span-6" },
    h("h2", {}, "Shape of the return distribution"),
    h("p", { class: "small muted" }, "Why the risk tab uses historical rather than normal VaR: equity returns are skewed and fat-tailed, and Jarque–Bera says how far from normal this one is."),
    kv([
      ["Skewness", num(d.skewness, 2), "Negative: the large moves are mostly down"],
      ["Excess kurtosis", num(d.excess_kurtosis, 2), "0 for a normal distribution"],
      ["Jarque–Bera", num(d.jarque_bera, 1)],
      ["Normality", d.normal_rejected === null ? DASH : d.normal_rejected ? "rejected at 5%" : "not rejected"],
      ["Autocorrelation (lag 1)", num(d.autocorr_lag1, 3)],
      ["Ulcer index", num(d.ulcer_index, 1), "Root-mean-square drawdown in percentage points over the full history: penalises long, deep drawdowns"],
    ]));
}

function sharpeCard(sr) {
  if (!sr) return unavailable("Is the Sharpe ratio real?", "Needs at least 60 daily returns.");
  return h("section", { class: "card span-6" },
    h("h2", {}, "Is the Sharpe ratio real?"),
    h("p", { class: "small muted" }, "A Sharpe ratio is an estimate with a standard error that grows with skew and fat tails. The probabilistic Sharpe ratio (Bailey & López de Prado) is the probability the true Sharpe is above zero given this sample's length and shape."),
    kv([
      ["Sharpe (annualised)", num(sr.sharpe_annual, 2)],
      ["P(true Sharpe > 0)", pct(sr.psr_vs_zero, 0), "Probabilistic Sharpe ratio"],
      ["Deflated", sr.dsr === null ? DASH : pct(sr.dsr, 0), "After correcting for the number of trials"],
      ["Track record needed", sr.min_track_record_sessions === null ? DASH : `${num(sr.min_track_record_sessions, 0)} sessions`, "Minimum track record length for 95% confidence"],
    ]),
    h("p", { class: "reading" }, sr.reading));
}

// -- Structure --------------------------------------------------------------

export function structure(r) {
  const s = r.structure || {};
  return h("div", { class: "grid" }, profileCard(s.volume_profile), analogCard(s.analogs));
}

const POSITION = { above_value: "Above the value area", inside_value: "Inside the value area", below_value: "Below the value area" };

function profileCard(p) {
  if (!p) return unavailable("Volume at price", "Needs at least 60 sessions.");
  const lt = p.level_test;
  return h("div", { class: "grid span-12" },
    h("section", { class: "card span-8" },
      h("h2", {}, `Volume at price · last ${p.lookback} sessions`),
      profileChart(p),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "c1" }), "Value area (70% of volume)"),
        h("span", {}, h("i", { class: "c2" }), "Point of control"), h("span", {}, h("i", { class: "cl" }), "Last price"))),
    h("section", { class: "card span-4" },
      h("h2", {}, "Where it traded"),
      h("p", { class: "reading" }, p.reading),
      kv([
        ["Point of control", num(p.poc), "The price with the most volume: fair value by revealed preference"],
        ["Value area", `${num(p.value_area_low)} – ${num(p.value_area_high)}`],
        ["Position", POSITION[p.position] || p.position],
        ["Distance to POC", pct(p.distance_to_poc_pct, 1, true)],
        ["High-volume nodes", p.hvn.length ? p.hvn.map((x) => num(x)).join(", ") : DASH, "Prices the market kept returning to"],
        ["Low-volume nodes", p.lvn.length ? p.lvn.map((x) => num(x)).join(", ") : DASH, "Prices the market moved through quickly"],
      ]),
      lt ? h("div", {},
        h("h3", {}, "Do the levels hold out of sample?"),
        h("p", { class: "small muted" }, "Levels are built on the first 70% of the window and tested on the last 30%, against levels placed at random."),
        kv([["Touches", String(lt.touches)], ["Reaction rate", pct(lt.reaction_rate, 0)], ["Random levels", pct(lt.base_rate, 0)], ["Lift", lt.lift === null ? DASH : `${num(lt.lift, 2)}×`]]),
        h("p", { class: "small" }, lt.verdict)) : null));
}

function analogCard(a) {
  if (!a) return unavailable("Shape analogs", "History too short to search for analogs.");
  const rows = a.matches.map((m) => h("tr", {},
    h("td", {}, `${m.start} → ${m.end}`), h("td", { class: "num" }, num(m.distance, 2)),
    h("td", { class: `num ${m.forward_return >= 0 ? "ok" : "bad"}` }, pct(m.forward_return, 1, true))));
  const sm = a.summary, base = a.base_rate;
  return h("div", { class: "grid span-12" },
    h("section", { class: "card span-7" },
      h("h2", {}, `Shape analogs · last ${a.window} sessions`),
      analogChart(a),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "c1" }), "Today, z-normalised"), h("span", {}, h("i", { class: "cl" }), `${a.matches.length} nearest past windows`)),
      h("p", { class: "reading" }, a.warning)),
    h("section", { class: "card span-5" },
      h("h2", {}, `What happened ${a.horizon} sessions later`),
      kv([
        ["Analog mean", pct(sm.mean_forward, 1, true)], ["Analogs up", pct(sm.share_up, 0)],
        ["All windows mean", pct(base.mean_forward, 1, true)], ["All windows up", pct(base.share_up, 0)],
        ["Edge vs base", pct(a.edge, 1, true)], ["Standard error", pct(sm.stderr, 1)],
      ]),
      h("p", { class: "small" }, h("strong", { class: a.significant ? "ok" : "muted" }, a.significant ? "Distinguishable from the base rate. " : "Not distinguishable from the base rate. "), a.verdict),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Window"), h("th", { class: "num" }, "Distance"), h("th", { class: "num" }, "Forward"))),
        h("tbody", {}, rows))),
      h("p", { class: "tiny muted" }, `${a.candidates.toLocaleString()} candidate windows searched, non-overlapping.`)));
}

// -- Research ---------------------------------------------------------------

export function research(r) {
  const x = r.research;
  if (!x) return h("div", { class: "grid" }, unavailable("Research", "No filing data for this symbol, so the forensic scores and the valuation grid are withheld."));
  return h("div", { class: "grid" },
    h("div", { class: "span-12 score-row" }, piotroskiTile(x.piotroski), altmanTile(x.altman), beneishTile(x.beneish), accrualsTile(x.accruals)),
    debateCard(x.debate), checklistCard(x.checklist),
    piotroskiCard(x.piotroski), flagsCard(x.red_flags),
    multiplesCard(x.valuation), dcfCard(x.valuation));
}

function tile(title, big, tone, reading) {
  return h("section", { class: "card" }, h("h2", {}, title), h("div", { class: `stat-big ${tone}` }, big), h("p", { class: "small muted" }, reading));
}

function piotroskiTile(p) {
  const tone = p.score === null ? "" : p.score >= 7 ? "ok" : p.score <= 3 ? "bad" : "";
  return tile("Piotroski F-score", p.score === null ? DASH : `${p.score} / ${p.max_known}`, tone, p.reading);
}

function altmanTile(a) {
  const tone = a.zone === "safe" ? "ok" : a.zone === "distress" ? "bad" : "";
  return tile(`Altman ${a.variant}`, a.z === null ? DASH : num(a.z, 2), tone, a.reading);
}

function beneishTile(b) {
  const tone = b.flagged === null ? "" : b.flagged ? "bad" : "ok";
  return tile("Beneish M-score", b.m === null ? DASH : num(b.m, 2), tone, b.reading);
}

function accrualsTile(a) {
  return tile("Sloan accruals", a.sloan_ratio === null ? DASH : pct(a.sloan_ratio, 1), "", a.rule);
}

function debateCard(d) {
  const list = (items) => items.length ? h("ul", {}, items.map((t) => h("li", {}, t))) : h("p", { class: "muted small" }, "Nothing in the measurements argues this side.");
  return h("section", { class: "card span-7 note" },
    h("h2", {}, "The case, derived from the measurements"),
    h("p", { class: "small muted" }, "Not written: each point is a measurement on this page that argues for its side. A short bear case for clean accounts is the correct output, not a gap."),
    h("h3", {}, "Bull"), list(d.bull), h("h3", {}, "Bear"), list(d.bear));
}

function checklistCard(items) {
  return h("section", { class: "card span-5" }, h("h2", {}, "First-read checklist"),
    h("ul", { class: "checks" }, items.map((c) => h("li", {},
      h("span", { class: `mark badge ${c.answer === "yes" ? "up" : c.answer === "no" ? "down" : "flat"}` }, c.answer),
      h("span", {}, c.question, h("div", { class: "tiny muted" }, c.because))))));
}

function piotroskiCard(p) {
  return h("section", { class: "card span-6" }, h("h2", {}, "Piotroski's nine tests"),
    h("p", { class: "small muted" }, "Profitability, leverage and liquidity, and operating efficiency, each against the prior fiscal year. Untestable is not a fail."),
    h("ul", { class: "checks" }, p.tests.map((t) => h("li", {}, h("span", { class: "mark" }, yesNo(t.passed)),
      h("span", {}, t.rule, h("div", { class: "tiny muted" }, `measured: ${t.measured === null ? DASH : num(t.measured, 3)}`))))));
}

function flagsCard(flags) {
  const rows = flags.map((f) => {
    const sev = h("span", { class: "sev" });
    const fill = h("i");
    fill.style.width = `${Math.round((f.severity || 0) * 100)}%`;
    sev.append(fill);
    return h("li", {}, h("span", { class: "mark" }, f.raised === null ? badge("unknown", "flat") : f.raised ? badge("raised", "down") : badge("clear", "up")),
      h("span", {}, f.rule, h("div", { class: "tiny muted" }, `measured ${f.measured === null ? DASH : num(f.measured, 3)} · line ${f.threshold === null ? DASH : num(f.threshold, 3)} `, f.raised ? sev : null)));
  });
  return h("section", { class: "card span-6" }, h("h2", {}, "Red flags, graded"),
    h("p", { class: "small muted" }, "Severity is the distance past the line, not a yes/no: failing a leverage test by 15% and a dilution test by 6× are not the same news."),
    h("ul", { class: "checks" }, rows));
}

const MULTIPLE_LABEL = {
  pe: "P/E", ps: "P/S", pb: "P/B", ev_ebit: "EV/EBIT", ev_sales: "EV/Sales", fcf_yield: "FCF yield",
  earnings_yield: "Earnings yield", market_cap: "Market cap", enterprise_value: "Enterprise value", forward_pe: "Forward P/E",
};

function multiplesCard(v) {
  const m = v?.multiples || {};
  const fmt = (k, val) => val === null || val === undefined ? DASH : k.endsWith("yield") ? pct(val, 1) : ["market_cap", "enterprise_value"].includes(k) ? money(val) : `${num(val, 1)}×`;
  const withheld = Object.entries(v?.withheld || {});
  return h("section", { class: "card span-5" }, h("h2", {}, "Multiples"),
    kv(Object.entries(m).filter(([k, val]) => val !== null || !(k in (v?.withheld || {}))).map(([k, val]) => [MULTIPLE_LABEL[k] || k, fmt(k, val)])),
    withheld.length ? h("div", {}, h("h3", {}, "Withheld"), h("ul", { class: "small muted" }, withheld.map(([k, why]) => h("li", {}, `${MULTIPLE_LABEL[k] || k}: ${why}`)))) : null,
    v?.reverse_dcf ? h("div", {}, h("h3", {}, "Reverse DCF"),
      kv([["Growth the price implies", pct(v.reverse_dcf.implied_growth, 1)], ["Historical growth", pct(v.reverse_dcf.historical_growth, 1)]]),
      h("p", { class: "reading" }, v.reverse_dcf.reading)) : null);
}

function dcfCard(v) {
  const d = v?.dcf;
  if (!d) return h("section", { class: "card span-7" }, h("h2", {}, "Discounted cash flow"), h("p", { class: "muted" }, "Withheld: needs positive free cash flow and a share count."));
  const midR = Math.floor(d.discount_rates.length / 2), midG = Math.floor(d.terminal_growth.length / 2);
  const head = h("tr", {}, h("th", {}, "r \\ g"), d.terminal_growth.map((g) => h("th", { class: "num" }, pct(g, 1))));
  const body = d.discount_rates.map((rate, i) => h("tr", {}, h("th", {}, pct(rate, 1)), d.grid[i].map((val, j) => {
    const cls = ["num", val !== null && d.price ? (val > d.price * 1.1 ? "cheap" : val < d.price * 0.9 ? "rich" : "") : "", i === midR && j === midG ? "base" : ""].filter(Boolean).join(" ");
    return h("td", { class: cls }, val === null ? DASH : num(val, 0));
  })));
  return h("section", { class: "card span-7" }, h("h2", {}, "Discounted cash flow — the grid, not a point"),
    h("p", { class: "small muted" }, `Per-share value across discount rate (rows) and terminal growth (columns), from ${num(d.base_fcf_per_share, 2)} of free cash flow per share. Green cells are >10% above today's ${num(d.price, 2)}, red >10% below. Terminal value is ${pct(d.terminal_share, 0)} of the base cell — which is why no single fair value is printed.`),
    h("div", { class: "table-scroll" }, h("table", { class: "data grid-dcf" }, h("thead", {}, head), h("tbody", {}, body))),
    v.cases?.length ? h("p", { class: "tiny muted" }, "Cases: " + v.cases.map((c) => `${c.name} ${pct(c.growth, 1)} growth at ${pct(c.discount, 1)}, ${pct(c.terminal, 1)} terminal`).join(" · ")) : null);
}


// -- Our call ---------------------------------------------------------------

export function callCard(r) {
  const f = r.forecast;
  if (!f) return null;
  const record = h("div", { class: "small muted" }, "Loading this stock's record…");
  api(`/api/v1/symbols/${encodeURIComponent(r.symbol)}/forecasts`).then((d) => {
    const v = d.simulated.scoring.verdict, live = d.live.scoring;
    record.replaceChildren(
      h("div", {}, h("span", { class: "badge label-sim" }, "simulated"), " ", h("strong", {}, v.label.replaceAll("_", " ")), ` — ${d.simulated.scoring.resolved} past calls on ${r.symbol}, Brier skill ${signed(d.simulated.scoring.brier_skill, 3)}.`),
      h("div", {}, h("span", { class: "badge label-live" }, "live"), ` ${d.live.forecasts.length} committed, ${live.resolved} resolved. `, h("a", { href: "/track-record" }, "Full track record →")));
  }).catch(() => record.replaceChildren("Record unavailable."));
  const tone = f.direction === "up" ? "up" : "down";
  return h("section", { class: "card call", "aria-label": "Committed forecast" },
    h("div", {}, h("div", { class: "tiny muted" }, `OUR CALL · ${f.horizon} SESSIONS`), h("div", { class: `score-big ${tone === "up" ? "ok" : "bad"}` }, pct(f.p_up, 0)), h("div", { class: "small" }, "chance it is higher")),
    h("div", { class: "small" },
      `Committed on ${f.made_on} to the append-only ledger, resolves on or after ${f.resolves_on_or_after}. One-sigma range ${num(f.range_low)} – ${num(f.range_high)}. `,
      h("span", { class: "muted" }, `Model ${f.model_version}: price families only, regime ${f.regime.replace("_", "-")}. Deliberately shrunk toward 50% — the record decides whether it deserves more.`)),
    record);
}

// -- Drivers ----------------------------------------------------------------

export function driversTab(r) {
  const d = r.drivers || {};
  return h("div", { class: "grid" }, attributionCard(d), decompositionCard(d.attribution), betaCard(d.attribution), shocksCard(d), simulationCard(d.simulation));
}

function attributionCard(d) {
  const a = d.attribution;
  if (!a) return unavailable("What moves it", "Needs 120 sessions aligned with the benchmark (and not the benchmark itself).");
  const vs = a.variance_share;
  const bar = (share, cls) => { const el = h("span", { class: `vshare ${cls}` }); el.style.width = `${Math.max(0, share * 100)}%`; return el; };
  const tstat = (b) => b ? `${num(b.value, 2)} (t ${num(b.t_nw, 1)})` : DASH;
  return h("section", { class: "card span-12" },
    h("div", { class: "card-head" }, h("h2", {}, "What moves it"), h("span", { class: "tag" }, d.sector_label ? `${d.sector_label} · ${d.sector_etf ?? "no sector fund"}` : "sector unknown")),
    h("p", { class: "reading" }, a.reading),
    h("div", { class: "vbar", role: "img", "aria-label": `market ${pct(vs.market, 0)}, sector ${pct(vs.sector, 0)}, own ${pct(vs.idiosyncratic, 0)}` },
      bar(vs.market, "m"), bar(vs.sector || 0, "s"), bar(vs.idiosyncratic, "i")),
    h("div", { class: "legend" }, h("span", {}, h("i", { class: "c1" }), `Market ${pct(vs.market, 0)}`), h("span", {}, h("i", { class: "c2" }), `Sector beyond market ${pct(vs.sector, 0)}`), h("span", {}, h("i", { class: "c4" }), `Its own story ${pct(vs.idiosyncratic, 0)}`)),
    kv([
      ["Market beta", tstat(a.market_beta), "Newey–West t-statistic: robust to autocorrelated, heteroskedastic residuals"],
      ["Sector beta (orthogonalised)", tstat(a.sector_beta), "The sector's move beyond what the market explains"],
      ["Alpha, annualised", `${pct(a.alpha_annual, 1, true)} (t ${num(a.alpha_t_nw, 1)})`, "|t| < 2: indistinguishable from zero"],
      ["R²", num(a.r_squared, 2)], ["Own-story vol", pct(a.idio_vol_annual, 1)],
      ["Beta on up days", num(a.asymmetry.upside_beta, 2)], ["Beta on down days", num(a.asymmetry.downside_beta, 2), "Higher than up-day beta: it falls harder than it rises with the market"],
      ["Crashes together", a.tail_dependence.probability === null ? DASH : `${pct(a.tail_dependence.probability, 0)} vs 5% if independent`, "P(stock in its worst 5% of days | market in its worst 5%)"],
    ]),
    h("p", { class: "tiny muted" }, `${a.observations} daily returns, ${a.start} → ${a.end}; Newey–West lag ${a.nw_lag}.`));
}

function decompositionCard(a) {
  if (!a?.decomposition?.series?.length) return null;
  const dc = a.decomposition, ser = dc.series;
  return h("section", { class: "card span-7" },
    h("h2", {}, `Where the last ${dc.sessions} sessions' return came from`),
    multiLine(ser.map((p) => p.date), [
      { label: "total", values: ser.map((p) => p.total), cls: "l1" },
      { label: "market", values: ser.map((p) => p.market), cls: "l2" },
      ...(dc.sector === null ? [] : [{ label: "sector", values: ser.map((p) => p.sector), cls: "l3" }]),
      { label: "specific", values: ser.map((p) => p.specific), cls: "l4" },
    ], { fmt: (v) => `${(v * 100).toFixed(0)}%`, zero: true }),
    h("div", { class: "legend" }, h("span", {}, h("i", { class: "c1" }), `Total ${pct(dc.total, 1, true)}`), h("span", {}, h("i", { class: "c2" }), `Market ${pct(dc.market, 1, true)}`),
      dc.sector === null ? null : h("span", {}, h("i", { class: "c3" }), `Sector ${pct(dc.sector, 1, true)}`), h("span", {}, h("i", { class: "c4" }), `Specific ${pct(dc.specific, 1, true)}`)),
    h("p", { class: "tiny muted" }, "Cumulative log returns; the three parts sum to the total exactly. A stock that rose with the market has done nothing of its own."));
}

function betaCard(a) {
  const pts = a?.rolling_beta?.points;
  if (!pts?.length) return null;
  return h("section", { class: "card span-5" }, h("h2", {}, `Rolling ${a.rolling_beta.window}-session market beta`),
    multiLine(pts.map((p) => p.date), [{ label: "beta", values: pts.map((p) => p.beta), cls: "l1" }], { fmt: (v) => v.toFixed(1) }),
    h("p", { class: "small muted" }, "A beta that wanders is a reason not to trust any single beta number, including the one at the top of this tab."));
}

function shocksCard(d) {
  if (!d.shocks?.length) return null;
  return h("section", { class: "card span-12" }, h("h2", {}, "If the market moves over the next month"),
    h("p", { class: "small muted" }, "Expected move from the factor betas (downside beta for falls, upside for rises), a ±1σ band from everything the market does not explain, and what actually happened in past months when the market moved that much."),
    h("div", { class: "table-scroll" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Market"), h("th", { class: "num" }, "Expected"), h("th", { class: "num" }, "Band (1 sd)"), h("th", { class: "num" }, "Beta used"), h("th", { class: "num" }, "Past analogs"))),
      h("tbody", {}, d.shocks.map((x) => h("tr", {},
        h("td", {}, pct(x.market_move, 0, true)), h("td", { class: `num ${x.expected >= 0 ? "ok" : "bad"}` }, pct(x.expected, 1, true)),
        h("td", { class: "num" }, `${pct(x.low, 1, true)} to ${pct(x.high, 1, true)}`), h("td", { class: "num" }, `${num(x.beta_used, 2)} ${x.beta_kind}`),
        h("td", { class: "num" }, x.analog.median === null ? `${DASH} (n=${x.analog.n})` : `${pct(x.analog.median, 1, true)} (n=${x.analog.n})`)))))),
    h("p", { class: "tiny muted" }, "Analog counts are overlapping windows, not independent episodes."));
}

function simulationCard(sim) {
  if (!sim) return unavailable("Simulated price distribution", "Needs 250 returns for the GARCH filter.");
  const rows = sim.horizons.map((x) => h("tr", {}, h("td", {}, `${x.sessions} sessions`),
    h("td", { class: "num" }, `${num(x.fhs.p5)} – ${num(x.fhs.p95)}`), h("td", { class: "num" }, pct(x.fhs.p_up, 0)),
    h("td", { class: "num" }, pct(x.fhs.p_drop_10, 0)), h("td", { class: "num" }, `${pct(x.fhs.expected_shortfall_95, 1)} vs ${pct(x.normal.expected_shortfall_95, 1)}`)));
  return h("section", { class: "card span-12" }, h("h2", {}, "Simulated price distribution"),
    h("div", { class: "grid" },
      h("div", { class: "span-7" }, fanChart(sim.fan, sim.last_price),
        h("div", { class: "legend" }, h("span", {}, h("i", { class: "cb" }), "5–95% and 25–75%"), h("span", {}, h("i", { class: "c1" }), "Median path"))),
      h("div", { class: "span-5" }, h("p", { class: "reading" }, sim.reading),
        h("p", { class: "small muted" }, `Filtered historical simulation (Barone-Adesi 1999): ${sim.paths.toLocaleString()} paths that replay this stock's own standardised shocks through its GARCH volatility, starting from today's conditional vol. Zero drift by design — this is a statement about spread, not direction.`))),
    h("div", { class: "table-scroll" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Horizon"), h("th", { class: "num" }, "90% range"), h("th", { class: "num" }, "P(up)"), h("th", { class: "num" }, "P(fall > 10%)"), h("th", { class: "num" }, "ES 95%: simulated vs normal"))),
      h("tbody", {}, rows))));
}

// -- Evidence ---------------------------------------------------------------

export function evidenceTab(r) {
  const holder = h("div", { class: "grid" }, h("section", { class: "card span-12" }, h("p", { class: "loading" }, "Re-scoring every session of history, point-in-time…")));
  api(`/api/v1/symbols/${encodeURIComponent(r.symbol)}/evidence`)
    .then((e) => holder.replaceChildren(...evidence(e)))
    .catch((err) => holder.replaceChildren(unavailable("Evidence", err.message)));
  return holder;
}

const VERDICT_CLASS = (f) => f.works ? "works" : f.verdict.startsWith("Evidence against") ? "against" : "noevid";

function evidence(e) {
  const curves = e.equity_curves;
  const lines = [{ label: "buy and hold", values: curves.buy_and_hold, cls: "l4" }];
  const cls = ["l1", "l2", "l3"];
  let i = 0;
  for (const [k, v] of Object.entries(curves)) {
    if (k === "dates" || k === "buy_and_hold" || k === "price_composite_long_flat") continue;
    lines.push({ label: k, values: v, cls: cls[i++ % 3] });
  }
  if (curves.price_composite_long_flat) lines.push({ label: "composite", values: curves.price_composite_long_flat, cls: "l5" });
  const famRows = e.families.map((f) => {
    const hz = f.horizons;
    const cell = (k) => hz[k] ? h("td", { class: "num" }, `${signed(hz[k].ic_independent ?? hz[k].ic, 3)}`, h("div", { class: "tiny muted" }, `t ${num(hz[k].t_stat, 1)} · p ${num(Math.max(hz[k].permutation_p ?? 0, hz[k].shift_p ?? 0), 3)}`)) : h("td", {}, DASH);
    const lf = f.rules.long_flat;
    return h("tr", {}, h("td", { class: "name" }, f.label), ...e.horizons.map((k) => cell(String(k))),
      h("td", { class: "num" }, num(lf.sharpe, 2), h("div", { class: "tiny muted" }, `DSR ${pct(lf.dsr, 0)}`)),
      h("td", {}, h("span", { class: VERDICT_CLASS(f) }, f.works ? "works here" : VERDICT_CLASS(f) === "against" ? "evidence against" : "no evidence"), h("div", { class: "tiny muted" }, f.verdict)));
  });
  const bh = e.buy_and_hold;
  return [
    h("section", { class: "card span-12" },
      h("h2", {}, "Does any of this actually work on this stock?"),
      h("p", { class: "reading" }, e.reading),
      h("p", { class: "small muted" }, `Every family re-scored on each of ${e.observations} past sessions using only what was knowable that day, then compared with what happened next. ${e.tests_run} tests were run, so each must clear p < ${num(e.bonferroni_alpha, 4)} (Bonferroni), t ≥ 2 on non-overlapping days, and a deflated Sharpe above 95% after ${e.cost_bps} bps per trade.`),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Family"), ...e.horizons.map((k) => h("th", { class: "num" }, `Rank IC, ${k}d`)), h("th", { class: "num" }, "Long/flat Sharpe"), h("th", {}, "Verdict"))),
        h("tbody", {}, famRows)))),
    h("section", { class: "card span-7" }, h("h2", {}, "Rules traded on the signals, after costs"),
      multiLine(curves.dates, lines, { fmt: (v) => v.toFixed(2) }),
      h("div", { class: "legend" }, lines.map((l) => h("span", {}, h("i", { class: { l1: "c1", l2: "c2", l3: "c3", l4: "c4", l5: "c5" }[l.cls] }), l.label.replace("_long_flat", "").replaceAll("_", " ")))),
      h("p", { class: "tiny muted" }, `Long when the score is above +0.15, flat otherwise; ${e.cost_bps} bps per change of position. Buy and hold: ${pct(bh.annual_return, 1, true)} a year, Sharpe ${num(bh.sharpe, 2)}.`)),
    h("section", { class: "card span-5" }, h("h2", {}, "Does conviction pay?"),
      h("p", { class: "small muted" }, "Mean 21-session return by composite score band. A real signal orders the bands; one where mild beats strong is pointing the wrong way where it matters most."),
      bandTable(e.families.find((f) => f.name === "price_composite") || e.families[0])),
  ];
}

function bandTable(f) {
  const b = f.bands["21"] || f.bands[Object.keys(f.bands)[0]];
  return h("div", {}, h("div", { class: "table-scroll" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", {}, "Band"), h("th", { class: "num" }, "n"), h("th", { class: "num" }, "Mean return"), h("th", { class: "num" }, "Share up"))),
    h("tbody", {}, b.bands.map((x) => h("tr", {}, h("td", {}, x.band.replace("_", " ")), h("td", { class: "num" }, String(x.n)), h("td", { class: `num ${x.mean_return >= 0 ? "ok" : "bad"}` }, x.mean_return === null ? DASH : pct(x.mean_return, 2, true)), h("td", { class: "num" }, pct(x.share_up, 0))))))),
    h("p", { class: "small" }, b.monotonic ? "The bands are ordered." : "The bands are not ordered: conviction did not pay."));
}
