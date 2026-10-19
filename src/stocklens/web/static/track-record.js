// The track record: /track-record. Every forecast StockLens commits is
// written before its outcome exists and graded afterwards. Two records are
// shown and never mixed: the live ledger (cannot be cherry-picked) and a
// simulated walk-forward record over a fixed basket (labelled as such).
"use strict";

import { DASH, api, h, num, pct, signed, wireSearch } from "./common.js";
import { reliabilityChart } from "./charts.js";

const root = document.getElementById("app");
wireSearch();
main().catch((err) => root.replaceChildren(h("p", { class: "error", role: "alert" }, err.message)));

async function main() {
  root.replaceChildren(h("p", { class: "loading" }, "Scoring every forecast…"));
  const [d, meta] = await Promise.all([api("/api/v1/track-record"), api("/api/v1/meta")]);
  if (meta.demo) document.getElementById("demo-banner").hidden = false;
  const sim = d.simulated;
  root.replaceChildren(
    h("section", { class: "hero" },
      h("h1", {}, "Track record"),
      h("p", {}, "Most stock sites print a price target and never mention it again. StockLens commits to a probability — will this be higher in 21 sessions? — writes it to an append-only ledger before the outcome exists, and grades it here. Including when the grade is bad.")),
    h("div", { class: "grid top" },
      recordCard("Live ledger", "live", d.live.scoring, `${d.live.count} forecast${d.live.count === 1 ? "" : "s"} committed by this deployment. Written once per symbol per session when a report is viewed; resolved automatically ${sim.horizon} sessions later; never revised.`),
      recordCard("Simulated record", "simulated", sim.scoring,
        `The same model (${sim.model_version}) walked point-in-time over a fixed basket — ${d.universe.join(", ")} — every 5 sessions from ${sim.period?.start ?? DASH} to ${sim.period?.end ?? DASH}. Each forecast sees only bars up to its date.`)),
    h("div", { class: "grid" }, method()));
}

function recordCard(title, kind, sc, blurb) {
  const v = sc.verdict;
  const tone = v.label === "skill" ? "up" : v.label === "worse_than_climatology" ? "down" : "flat";
  const hr = sc.hit_rate || {};
  const eff = hr.effective?.wilson_95;
  const rel = (sc.reliability || []).map((b) => h("tr", {}, h("td", {}, b.bin), h("td", { class: "num" }, String(b.n)),
    h("td", { class: "num" }, pct(b.mean_predicted, 1)), h("td", { class: "num" }, pct(b.observed_up, 1)),
    h("td", { class: "num" }, b.wilson_95 ? `${pct(b.wilson_95.low, 0)}–${pct(b.wilson_95.high, 0)}` : DASH),
    h("td", {}, b.reliable ? (b.consistent ? "calibrated" : "off") : h("span", { class: "muted" }, "small sample"))));
  const regimes = (sc.slices?.regime || []).map((x) => h("tr", {}, h("td", {}, x.label), h("td", { class: "num" }, String(x.n)), h("td", { class: "num" }, pct(x.hit_rate, 1)), h("td", { class: "num" }, signed(x.brier_skill, 3)), h("td", {}, x.reliable ? "" : h("span", { class: "muted" }, "small sample"))));
  return h("section", { class: "card span-6" },
    h("div", { class: "card-head" }, h("h2", {}, title), h("span", { class: `badge ${kind === "live" ? "label-live" : "label-sim"}` }, kind)),
    h("p", { class: "small muted" }, blurb),
    h("p", {}, h("span", { class: `badge ${tone}` }, v.label.replaceAll("_", " ")), " ", v.text),
    h("div", { class: "kv" }, ...[
      ["Resolved", String(sc.resolved ?? 0)], ["Decision days", String(sc.decision_days ?? 0)],
      ["Brier score", num(sc.brier, 4)], ["Brier skill vs base rate", signed(sc.brier_skill, 3)],
      ["Hit rate", pct(hr.point, 1)], ["Always-up would score", pct(hr.always_up, 1)],
      ["Hit rate 95% (effective)", eff ? `${pct(eff.low, 0)}–${pct(eff.high, 0)}` : DASH],
      ["Range coverage (nominal 68%)", pct(sc.range_coverage?.rate, 0)],
      ["Independent blocks", sc.block_test ? `${sc.block_test.blocks} · t ${num(sc.block_test.t_stat, 2)}` : DASH],
    ].map(([k, val]) => h("div", {}, h("dl", {}, h("dt", {}, k), h("dd", {}, val))))),
    sc.resolved ? h("div", {},
      h("h3", {}, "Calibration"),
      h("p", { class: "small muted" }, "When the model says 60%, does it happen 60% of the time? Points on the dashed diagonal are calibrated; bars are Wilson 95% intervals; faint points are below the slice floor."),
      h("div", { class: "chart rel" }, reliabilityChart(sc.reliability || [])),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Bin"), h("th", { class: "num" }, "n"), h("th", { class: "num" }, "Predicted"), h("th", { class: "num" }, "Observed"), h("th", { class: "num" }, "95%"), h("th", {}, ""))),
        h("tbody", {}, rel))),
      regimes.length ? h("div", {}, h("h3", {}, "By regime"), h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Regime"), h("th", { class: "num" }, "n"), h("th", { class: "num" }, "Hit rate"), h("th", { class: "num" }, "Brier skill"), h("th", {}, ""))),
        h("tbody", {}, regimes)))) : null)
      : h("p", { class: "muted small" }, "Nothing resolved yet. View a few stock pages today and come back in 21 sessions."));
}

function method() {
  return h("section", { class: "card span-12 prose" },
    h("h2", {}, "How it is graded, and why a hit rate is not enough"),
    h("ul", {},
      h("li", {}, h("strong", {}, "Brier score"), " is the mean squared error of the probability. ", h("strong", {}, "Brier skill"), " compares it with quoting the base rate of up moves for every forecast — the forecast a person with no model would make. Above zero is skill; zero is no information; below zero is worse than knowing nothing."),
      h("li", {}, "A ", h("strong", {}, "hit rate"), " is shown next to what always guessing up would have scored, because in a rising market a model that always says up looks brilliant."),
      h("li", {}, h("strong", {}, "Effective sample."), " Every symbol on one day shares the market's next month, so 12 forecasts on one morning are close to one observation. Intervals are sized on decision days, and the skill verdict also needs the Brier improvement to hold across independent 21-session blocks (t ≥ 2)."),
      h("li", {}, h("strong", {}, "Write-once."), " One forecast per symbol per session per model version; a refresh cannot replace it, and an outcome once recorded is never revised. Changing the model bumps the version, so old forecasts stay graded against the model that made them.")),
    h("p", { class: "small muted" }, "On demo data — a factor model with no expected-return signal — the correct verdict is “no skill”. It is."));
}
