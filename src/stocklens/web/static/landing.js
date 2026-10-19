// The landing page. Nothing on it is a mock-up: the card, the numbers and the
// calibration chart are fetched from the same API the stock pages use.
"use strict";

import { api, h, num, pct, signed, wireSearch } from "./common.js";
import { reliabilityChart } from "./charts.js";

const NS = "http://www.w3.org/2000/svg";
const SYMBOL = "MSFT";
const TICKER = /^[A-Z]{1,5}([.-][A-Z]{1,2})?$/;

wireSearch();
const form = document.getElementById("l-search");
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("l-q");
  const sym = input.value.trim().toUpperCase();
  if (!TICKER.test(sym)) { input.setCustomValidity("Enter a ticker like AAPL or BRK.B"); input.reportValidity(); return; }
  location.href = `/s/${encodeURIComponent(sym)}`;
});
document.getElementById("l-q").addEventListener("input", (e) => e.target.setCustomValidity(""));

const set = (id, ...nodes) => document.getElementById(id)?.replaceChildren(...nodes);

api("/api/v1/meta").then((m) => { if (m.demo) document.getElementById("demo-banner").hidden = false; }).catch(() => {});

api(`/api/v1/symbols/${SYMBOL}/report`).then((r) => {
  set("live-card", liveCard(r));
  const f = r.forecast;
  if (f) {
    set("vs-call", `${pct(f.p_up, 0)} chance ${r.symbol} is higher in ${f.horizon} sessions; 1σ range ${num(f.range_low, 0)}–${num(f.range_high, 0)}.`);
    set("vs-call-n", `Committed ${f.made_on}, resolves on or after ${f.resolves_on_or_after}. Graded either way.`);
  }
  const a = r.drivers?.attribution;
  if (a) {
    set("vs-risk", `${pct(a.variance_share.market, 0)} market, ${pct(a.variance_share.sector ?? 0, 0)} sector, ${pct(a.variance_share.idiosyncratic, 0)} its own story. Beta ${num(a.asymmetry.upside_beta, 2)} up / ${num(a.asymmetry.downside_beta, 2)} down.`);
    set("vs-risk-n", `Market beta t = ${num(a.market_beta.t_nw, 1)} (Newey–West). In the market's worst 5% of days it was in its own worst 5% ${pct(a.tail_dependence.probability, 0)} of the time.`);
  }
}).catch(() => set("live-card", h("p", { class: "muted small" }, "Live preview unavailable.")));

api(`/api/v1/symbols/${SYMBOL}/evidence`).then((e) => {
  const works = e.families.filter((f) => f.works).length;
  set("vs-evid", works ? `${works} of ${e.families.length} signal families show evidence on ${e.symbol}.` : `None of ${e.families.length} signal families shows evidence of predicting ${e.symbol} — so the page says so.`);
  set("vs-evid-n", `${e.observations} sessions re-scored point-in-time, ${e.tests_run} tests, each held to p < ${num(e.bonferroni_alpha, 4)} after ${e.cost_bps} bps costs.`);
}).catch(() => {});

api("/api/v1/track-record").then((t) => {
  const sc = t.simulated.scoring;
  set("s-forecasts", (sc.resolved + (t.live.scoring.resolved || 0)).toLocaleString());
  set("s-skill", signed(sc.brier_skill, 3));
  set("rel", reliabilityChart(sc.reliability || []));
  set("rel-verdict", h("span", { class: `badge ${sc.verdict.label === "skill" ? "up" : "flat"}` }, sc.verdict.label.replaceAll("_", " ")), " ", `${sc.resolved.toLocaleString()} forecasts over ${sc.decision_days} decision days. `, h("a", { href: "/track-record" }, "Full record →"));
}).catch(() => {});

function sparkline(values) {
  const W = 300, H = 64;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = (i) => (W * i) / (values.length - 1);
  const y = (v) => H - 4 - ((H - 8) * (v - lo)) / (hi - lo || 1);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const area = document.createElementNS(NS, "path");
  area.setAttribute("d", `${d}L${W},${H}L0,${H}Z`);
  area.setAttribute("class", "spark-area");
  const line = document.createElementNS(NS, "path");
  line.setAttribute("d", d);
  line.setAttribute("class", "spark-line");
  svg.append(area, line);
  return svg;
}

function liveCard(r) {
  const q = r.quote, c = r.composite, f = r.forecast, a = r.drivers?.attribution, x = r.research;
  const pin = h("span", { class: "pin" });
  pin.style.left = `${((c.score + 1) / 2) * 100}%`;
  const share = (v, cls) => { const el = h("span", { class: cls }); el.style.width = `${Math.max(0, (v || 0) * 100)}%`; return el; };
  const chip = (label, value, tone) => h("span", { class: `lc-chip ${tone || ""}` }, h("small", {}, label), value);
  const fams = c.contributions.filter((k) => k.kind === "directional");
  return h("a", { class: "lc", href: `/s/${r.symbol}`, "aria-label": `Open the ${r.symbol} page` },
    h("div", { class: "lc-top" },
      h("div", {}, h("div", { class: "lc-sym" }, r.symbol), h("div", { class: "lc-co" }, r.fundamentals?.company || "")),
      h("div", { class: "lc-px" }, num(q.price), h("span", { class: `badge ${q.change_pct >= 0 ? "up" : "down"}` }, pct(q.change_pct, 2, true)))),
    sparkline(r.chart.close.slice(-126)),
    h("div", { class: "lc-row" },
      h("div", { class: "lc-call" }, h("small", {}, `OUR CALL · ${f?.horizon ?? 21} SESSIONS`), h("b", {}, f ? pct(f.p_up, 0) : "—"), h("span", {}, "chance it is higher")),
      h("div", { class: "lc-score" }, h("small", {}, `COMPOSITE · ${c.stance}`), h("b", {}, signed(c.score)),
        h("div", { class: "scale" }, pin), h("span", {}, `${fams.filter((k) => k.score !== null).length} of ${fams.length} families · ${pct(c.confidence, 0)} confidence`))),
    a ? h("div", { class: "lc-var" }, h("small", {}, "WHAT MOVES IT"),
      h("div", { class: "vbar" }, share(a.variance_share.market, "m"), share(a.variance_share.sector, "s"), share(a.variance_share.idiosyncratic, "i")),
      h("span", {}, `market ${pct(a.variance_share.market, 0)} · sector ${pct(a.variance_share.sector ?? 0, 0)} · own story ${pct(a.variance_share.idiosyncratic, 0)}`)) : null,
    h("div", { class: "lc-chips" },
      x ? chip("Piotroski", `${x.piotroski.score ?? "—"}/${x.piotroski.max_known}`, x.piotroski.score >= 7 ? "ok" : x.piotroski.score <= 3 ? "bad" : "") : null,
      x ? chip("Altman Z''", num(x.altman.z, 2), x.altman.zone === "safe" ? "ok" : x.altman.zone === "distress" ? "bad" : "") : null,
      x ? chip("Beneish M", num(x.beneish.m, 2), x.beneish.flagged ? "bad" : "ok") : null,
      chip("Regime", r.quant?.regime?.trend?.replace("_", "-") ?? "—"),
      chip("GARCH half-life", r.quant?.volatility?.garch ? `${num(r.quant.volatility.garch.half_life_sessions, 0)}d` : "—")),
    h("div", { class: "lc-foot" }, h("span", { class: "pulse" }), "Computed live from the API just now · open the full page →"));
}
