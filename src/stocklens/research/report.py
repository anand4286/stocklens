"""One company's research file, assembled from the measurements.

The bull and bear cases are not written, they are *derived*. Each side is the
set of measurements that argues for it — a Piotroski score of 8, a safe Altman
zone and a passed red-flag test are bull points; a raised flag, a distress zone
and a price that asks for more growth than the company has ever delivered are
bear points — so the debate cannot contain a claim the rest of the page does
not support, and every line traces back to a figure in a filing.

That is a real constraint and it shows: the bear case for a company with clean
accounts is short, and it should be. A generated debate that always finds five
points a side is not a debate, it is a template.

The beginner checklist applies the same discipline to the questions a
first-time reader actually has. Each answer is yes, no, or unknown — and
*unknown* is first-class here, because "we could not tell" is the true state for
some of these on most companies and a checklist that never says so is worthless.
"""

from __future__ import annotations

from ..data.base import Fundamentals
from . import forensics, valuation

#: Revenue growth a year above which "growing" is a bull point. Below zero it
#: is a bear point; in between it is neither, which is the honest third outcome.
GROWING = 0.05
#: Trailing P/E bands outside which the multiple argues for a side.
PE_RICH, PE_CHEAP = 40.0, 15.0
#: How far implied growth must sit from realised before the reverse DCF argues.
IMPLIED_GAP = 0.02


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _flag_line(flag: dict) -> str:
    measured = flag["measured"]
    return (f"{flag['name']}: measured {_num(measured)} against {_num(flag['threshold'])} "
            f"({flag['rule']})")


def _num(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.0f}" if abs(x) >= 1e4 else f"{x:.3g}"


def debate(sections: dict) -> dict:
    """Bull and bear, each line a measurement from this file."""
    bull: list[str] = []
    bear: list[str] = []

    pio = sections["piotroski"]
    if pio["score"] is not None:
        line = f"Piotroski F-score {pio['score']}/{pio['max_known']} — {pio['reading']}."
        if pio["score"] >= 7:
            bull.append(line)
        elif pio["score"] <= 3:
            bear.append(line)

    alt = sections["altman"]
    if alt["zone"] == "safe":
        bull.append(f"Altman {alt['reading']}.")
    elif alt["zone"] == "distress":
        bear.append(f"Altman {alt['reading']}.")

    ben = sections["beneish"]
    if ben["flagged"] is True:
        bear.append(f"Beneish {ben['reading']}.")
    elif ben["flagged"] is False:
        bull.append(f"Beneish {ben['reading']}.")

    acc = sections["accruals"]
    if acc["sloan_ratio"] is not None:
        ratio = acc["sloan_ratio"]
        if acc.get("raised"):
            bear.append(f"Sloan accruals ratio {_pct(ratio)} — earnings run ahead of cash.")
        elif ratio < 0:
            bull.append(f"Sloan accruals ratio {_pct(ratio)} — cash flow exceeds earnings.")

    # Raised flags are each a bear point. Passed ones are folded into a single
    # bull line: eight separate "this was fine" lines would pad one side with
    # the absence of findings, and still every figure is shown.
    passed = []
    for flag in sections["red_flags"]:
        if flag["raised"] is True:
            bear.append(_flag_line(flag) + f"; severity {flag['severity']:.2f}.")
        elif flag["raised"] is False:
            passed.append(f"{flag['name'].lower()} " + (
                "(nothing to measure)" if flag["measured"] is None
                else f"{_num(flag['measured'])} (line {_num(flag['threshold'])})"))
    if passed:
        tested = sum(1 for flag in sections["red_flags"] if flag["raised"] is not None)
        bull.append(f"Clears {len(passed)} of {tested} testable red flags: "
                    + "; ".join(passed) + ".")

    val = sections["valuation"]
    rev = val.get("reverse_dcf") or {}
    hist = rev.get("historical_growth")
    if hist is not None:
        line = f"Revenue has compounded {_pct(hist)} a year."
        if hist > GROWING:
            bull.append(line)
        elif hist < 0:
            bear.append(line)

    pe = val["multiples"].get("pe")
    if pe is not None:
        if pe > PE_RICH:
            bear.append(f"At {pe:.1f}x trailing earnings the price assumes growth continues.")
        elif pe < PE_CHEAP:
            bull.append(f"At {pe:.1f}x trailing earnings little growth is assumed.")

    implied = rev.get("implied_growth")
    if implied is not None and hist is not None:
        if implied > hist + IMPLIED_GAP:
            bear.append(f"The price implies {_pct(implied)} a year of free-cash-flow growth "
                        f"against {_pct(hist)} realised revenue growth.")
        elif implied < hist - IMPLIED_GAP:
            bull.append(f"The price implies only {_pct(implied)} a year of free-cash-flow "
                        f"growth against {_pct(hist)} realised revenue growth.")
    return {"bull": bull, "bear": bear}


def checklist(f: Fundamentals, sections: dict) -> list[dict]:
    """The questions a first-time reader has, answered yes, no or unknown."""
    rows: list[dict] = []

    def add(question: str, answer: str, because: str) -> None:
        rows.append({"question": question, "answer": answer, "because": because})

    flags = {flag["name"]: flag for flag in sections["red_flags"]}

    if f.net_income is None:
        add("Is it profitable?", "unknown", "Net income is not reported.")
    else:
        add("Is it profitable?", "yes" if f.net_income > 0 else "no",
            f"Net income for the year was {f.net_income:,.0f}.")

    conv = flags["Cash conversion"]
    if conv["raised"] is None:
        add("Do the profits arrive as cash?", "unknown",
            "Needs positive net income and operating cash flow.")
    else:
        add("Do the profits arrive as cash?", "no" if conv["raised"] else "yes",
            f"Operating cash flow is {conv['measured']:.2f}x net income, against a floor "
            f"of {conv['threshold']:.2f}x.")

    alt = sections["altman"]
    if alt["zone"] in ("safe", "distress"):
        add("Is the balance sheet strong?", "yes" if alt["zone"] == "safe" else "no",
            f"Altman {alt['reading']}.")
    else:
        add("Is the balance sheet strong?", "unknown",
            f"Altman {alt['reading']}." if alt["zone"] == "grey"
            else f"Altman score {alt['reading']}.")

    hist = (sections["valuation"].get("reverse_dcf") or {}).get("historical_growth")
    if hist is None:
        hist, basis = valuation.historical_growth(f)
    else:
        basis = "revenue compound growth from the filing history"
    if hist is None:
        add("Is it growing?", "unknown", "No comparable earlier revenue.")
    else:
        add("Is it growing?", "yes" if hist > 0 else "no",
            f"Revenue growth of {_pct(hist)} a year ({basis}).")

    ben, acc = sections["beneish"], sections["accruals"]
    if ben["flagged"] is True or acc.get("raised") is True:
        add("Do the accounts look clean?", "no",
            f"Beneish: {ben['reading']}. Sloan accruals ratio: {acc['sloan_ratio']}.")
    elif ben["flagged"] is False and acc.get("raised") is False:
        add("Do the accounts look clean?", "yes",
            f"Beneish: {ben['reading']}. Sloan accruals ratio {_pct(acc['sloan_ratio'])}. "
            "A screen, not an audit.")
    else:
        add("Do the accounts look clean?", "unknown",
            f"Beneish: {ben['reading']}. Sloan: "
            + ("not computable." if acc["sloan_ratio"] is None
               else f"{_pct(acc['sloan_ratio'])}."))

    dil = flags["Dilution"]
    if dil["raised"] is None:
        add("Is the share count under control?", "unknown",
            "Needs two years of diluted share counts.")
    else:
        add("Is the share count under control?", "no" if dil["raised"] else "yes",
            f"Diluted shares changed {_pct(dil['measured'])} against a ceiling of "
            f"{_pct(dil['threshold'])}.")

    rev = sections["valuation"].get("reverse_dcf") or {}
    implied, realised = rev.get("implied_growth"), rev.get("historical_growth")
    if implied is None or realised is None:
        add("Does the price ask for less growth than the company has delivered?", "unknown",
            rev.get("reading") or sections["valuation"]["withheld"].get(
                "reverse_dcf", "No positive free cash flow to run a reverse DCF on."))
    else:
        add("Does the price ask for less growth than the company has delivered?",
            "yes" if implied <= realised else "no", rev["reading"] + ".")

    add("Does this need more research?", "yes",
        "Always. This file is built from summary financial statements and a price. It has "
        "not read the annual report, the competition or the terms of a single contract.")
    return rows


def build(f: Fundamentals | None, price: float | None) -> dict | None:
    """Everything this system can honestly say about one company's filing."""
    if f is None:
        return None
    if price is not None and not price > 0:
        price = None
    sections = forensics.examine(f, price)
    sections["valuation"] = valuation.build(f, price)
    return {
        "company": f.company,
        "fiscal_year": f.fiscal_year,
        "source": f.source,
        **sections,
        "debate": debate(sections),
        "checklist": checklist(f, sections),
    }
