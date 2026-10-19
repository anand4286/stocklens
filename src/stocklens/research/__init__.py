"""The research layer: accounting forensics, valuation and a derived debate.

`analytics` scores a symbol on [-1, +1] for the composite. This package does
something different: it reproduces published, citable models (Piotroski 2000,
Altman 1968/1995, Beneish 1999, Sloan 1996) and a discounted cash flow, and
shows every input and threshold so a reader can recompute any figure.

Three rules hold throughout, inherited from the scorecard:

* every score carries the quantity it measured and the line it was held to;
* *unknown* is first-class and distinct from *fail* — a test that could not run
  is reported as `None`, never quietly counted as a pass or a miss;
* no invented scores — a model with too few inputs is withheld, with the reason.
"""

from __future__ import annotations

from . import forensics, report, valuation

__all__ = ["forensics", "report", "valuation"]
