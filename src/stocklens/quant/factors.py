"""Market, sector, and the company's own story: a two-factor attribution.

Why not just beta
-----------------
A beta answers one question — how much does this move with the market — and
silently files everything else under "idiosyncratic". For a refiner most of
that "else" is oil, for a regional bank it is the yield curve: things every
company in the sector shares. Only what is left after the sector has been
taken out is about *this* company, and that is the part any argument about the
company's fundamentals is actually an argument about.

The model
---------
On daily log returns, ``r_stock = a + b_m r_market + b_s u_sector + e``, where
``u_sector = r_sector - g r_market`` is the sector fund's return with its
market component removed (``g`` is the sector's own OLS beta on the market).

Why orthogonalise: a sector fund is mostly the market. Regressing on market
and raw sector together splits the shared component between the two betas
arbitrarily (the two regressors are 70-90% correlated), and both betas become
noisy and hard to read. After the projection the two factors are uncorrelated
in-sample, so ``b_m`` is the stock's full market beta — the number every
aggregator shows — and ``b_s`` is *only* the sensitivity to what the sector
does beyond the market. Only the slope is removed, not the sector's mean, so a
sector that outran the market is credited to the sector, not to the stock's
alpha. Because the factors are orthogonal and OLS residuals are orthogonal to
both, the variance decomposes exactly::

    var(r) = b_m² var(m) + b_s² var(u) + var(e)

Inference
---------
Daily residuals are not independent: volatility clusters, and a stock that
drifts away from its sector usually does so over weeks. The textbook OLS
standard error assumes independence and is then too small, so a lucky year
reads as a "significant" alpha. Standard errors here are Newey–West (1987)
heteroskedasticity-and-autocorrelation consistent, Bartlett kernel, lag
``floor(4 (T/100)^(2/9))`` (Newey & West 1994), with the T/(T-k) small-sample
factor. The OLS t-stat is reported beside it so the gap is visible.

Numeric policy
--------------
Float maths, stdlib only, 252 sessions a year, None below 120 aligned returns
(about six months: fewer and a two-factor regression's betas are dominated by
a handful of days).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from stocklens.quant.linalg import solve_psd

TRADING_DAYS = 252
MIN_RETURNS = 120
ROLLING_WINDOW = 63
ROLLING_MAX_POINTS = 260
DECOMPOSITION_SESSIONS = 252
TAIL_LEVEL = 0.05


# ----------------------------------------------------------------------- plumbing


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _var(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _r(x: float | None, places: int = 6) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, places)


def aligned_returns(*series: Sequence[float] | None) -> tuple[list[int], list[list[float]]]:
    """Tail-align close series and take log returns where every series is positive.

    Returns the index (into the tail-aligned closes) of each kept return's end
    day, and one return list per input. A non-positive close drops that day
    from *all* series, so the returns stay aligned — `risk.log_returns` drops
    it from one series only, which would shift every later day by one.
    """
    present = [list(s) for s in series if s is not None]
    n = min(len(s) for s in present) if present else 0
    tails = [s[len(s) - n:] for s in present]
    index: list[int] = []
    out: list[list[float]] = [[] for _ in tails]
    for i in range(1, n):
        if all(s[i - 1] > 0 and s[i] > 0 for s in tails):
            index.append(i)
            for k, s in enumerate(tails):
                out[k].append(math.log(s[i] / s[i - 1]))
    return index, out


def _slope(y: Sequence[float], x: Sequence[float]) -> tuple[float, float] | None:
    """Univariate OLS (intercept, slope), or None when x does not vary."""
    if len(x) < 3:
        return None
    mx, my = _mean(x), _mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return None
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / sxx
    return my - slope * mx, slope


def newey_west_lag(observations: int) -> int:
    """The Newey–West (1994) rule-of-thumb bandwidth: floor(4 (T/100)^(2/9))."""
    return int(math.floor(4 * (observations / 100) ** (2 / 9)))


def ols(y: Sequence[float], columns: Sequence[Sequence[float]]) -> dict | None:
    """OLS with an intercept, OLS and Newey–West standard errors.

    `columns` are the regressors (without the constant). Returns coefficients
    (constant first), both sets of standard errors, residuals and R². None when
    the design is singular.
    """
    t = len(y)
    x = [[1.0, *(c[i] for c in columns)] for i in range(t)]
    k = len(x[0])
    if t <= k + 1:
        return None
    xtx = [[sum(row[a] * row[b] for row in x) for b in range(k)] for a in range(k)]
    xty = [sum(row[a] * yi for row, yi in zip(x, y, strict=True)) for a in range(k)]
    coef = solve_psd(xtx, xty)
    if coef is None:
        return None
    inv_cols = [solve_psd(xtx, [1.0 if i == j else 0.0 for i in range(k)]) for j in range(k)]
    if any(c is None for c in inv_cols):
        return None
    inv = [[inv_cols[j][i] for j in range(k)] for i in range(k)]  # type: ignore[index]

    resid = [yi - sum(c * v for c, v in zip(coef, row, strict=True))
             for yi, row in zip(y, x, strict=True)]
    sse = sum(e * e for e in resid)
    my = _mean(y)
    sst = sum((yi - my) ** 2 for yi in y)
    s2 = sse / (t - k)
    se_ols = [math.sqrt(max(s2 * inv[i][i], 0.0)) for i in range(k)]

    # Newey–West meat: Σ e² x x' plus Bartlett-weighted lagged cross products.
    lag = newey_west_lag(t)
    scores = [[e * v for v in row] for e, row in zip(resid, x, strict=True)]
    meat = [[sum(s[a] * s[b] for s in scores) for b in range(k)] for a in range(k)]
    for lag_i in range(1, lag + 1):
        w = 1 - lag_i / (lag + 1)
        gamma = [[sum(scores[i][a] * scores[i - lag_i][b] for i in range(lag_i, t))
                  for b in range(k)] for a in range(k)]
        for a in range(k):
            for b in range(k):
                meat[a][b] += w * (gamma[a][b] + gamma[b][a])
    scale = t / (t - k)
    tmp = [[sum(inv[i][m] * meat[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    cov = [[sum(tmp[i][m] * inv[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    se_nw = [math.sqrt(max(scale * cov[i][i], 0.0)) for i in range(k)]
    return {
        "coef": coef,
        "se_ols": se_ols,
        "se_nw": se_nw,
        "resid": resid,
        "r_squared": 1 - sse / sst if sst > 0 else None,
        "lag": lag,
        "dof": t - k,
    }


def _t(value: float, se: float) -> float | None:
    """None when the standard error is numerically zero (a series regressed on itself)."""
    return value / se if se > 1e-12 else None


def _quantile(xs: Sequence[float], q: float) -> float:
    ordered = sorted(xs)
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _downsample(points: list[dict], limit: int) -> list[dict]:
    """Every n-th point, always keeping the last, so a chart ends on today."""
    if len(points) <= limit:
        return points
    step = math.ceil(len(points) / limit)
    kept = points[::step]
    if kept[-1] is not points[-1]:
        kept = kept[:-1] + [points[-1]] if len(kept) >= limit else kept + [points[-1]]
    return kept


# ----------------------------------------------------------------------- pieces


def rolling_beta(stock: Sequence[float], market: Sequence[float],
                 window: int = ROLLING_WINDOW) -> list[float | None]:
    """Market beta over each trailing `window` of returns, by running sums.

    One value per return from index window-1 on. A rolling beta is how a reader
    sees that "beta 1.2" was 0.8 last year and 1.6 this quarter.
    """
    out: list[float | None] = []
    sx = sy = sxx = sxy = 0.0
    for i, (y, x) in enumerate(zip(stock, market, strict=True)):
        sx, sy, sxx, sxy = sx + x, sy + y, sxx + x * x, sxy + x * y
        if i >= window:
            ox, oy = market[i - window], stock[i - window]
            sx, sy, sxx, sxy = sx - ox, sy - oy, sxx - ox * ox, sxy - ox * oy
        if i >= window - 1:
            var = sxx - sx * sx / window
            out.append((sxy - sx * sy / window) / var if var > 1e-18 else None)
    return out


def asymmetric_beta(stock: Sequence[float], market: Sequence[float]) -> dict:
    """Beta on market-up days and on market-down days, separately.

    A stock with the same overall beta can fall harder than it rises; the
    downside number is the one that matters in a sell-off. Each needs 30 days.
    """
    out: dict = {}
    for name, keep in (("upside", lambda m: m > 0), ("downside", lambda m: m < 0)):
        pairs = [(y, x) for y, x in zip(stock, market, strict=True) if keep(x)]
        fit = _slope([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 30 else None
        out[f"{name}_beta"] = _r(fit[1], 4) if fit else None
        out[f"{name}_days"] = len(pairs)
    return out


def tail_dependence(stock: Sequence[float], market: Sequence[float],
                    level: float = TAIL_LEVEL) -> dict:
    """P(stock in its worst `level` | market in its worst `level`).

    Correlation is an average over all days; diversification fails on the
    worst ones. If the two were independent this would equal `level`; a value
    near 1 means the stock's worst days *are* the market's worst days.
    """
    qs, qm = _quantile(stock, level), _quantile(market, level)
    tail_days = [y for y, x in zip(stock, market, strict=True) if x <= qm]
    both = sum(1 for y in tail_days if y <= qs)
    p = both / len(tail_days) if tail_days else None
    return {
        "probability": _r(p, 4),
        "if_independent": level,
        "ratio": _r(p / level, 3) if p is not None else None,
        "market_tail_days": len(tail_days),
    }


# ----------------------------------------------------------------------- headline


def attribution(stock: list[float], market: list[float], sector: list[float] | None,
                dates: list[str]) -> dict | None:
    """Decompose a stock's daily moves into market, sector and its own part.

    Inputs are daily closes, tail-aligned (the last N of each, N the shortest
    length, `dates` included). `sector` may be None, giving a one-factor model.
    None below 120 aligned returns.
    """
    n = min(len(stock), len(market), len(dates), *([len(sector)] if sector else []))
    dates = list(dates[len(dates) - n:])
    index, rets = aligned_returns(stock, market, sector if sector else None)
    if len(index) < MIN_RETURNS:
        return None
    y, m = rets[0], rets[1]
    s_raw = rets[2] if sector else None
    ret_dates = [dates[i] for i in index]
    t = len(y)

    g = None
    columns: list[list[float]] = [m]
    if s_raw is not None:
        fit_g = _slope(s_raw, m)
        if fit_g is None:
            return None
        g = fit_g[1]
        u = [s - g * x for s, x in zip(s_raw, m, strict=True)]
        columns.append(u)
    fit = ols(y, columns)
    if fit is None:
        return None
    coef, se_nw, se_ols, resid = fit["coef"], fit["se_nw"], fit["se_ols"], fit["resid"]

    var_y = _var(y)
    shares = {"market": coef[1] ** 2 * _var(m) / var_y}
    if s_raw is not None:
        shares["sector"] = coef[2] ** 2 * _var(columns[1]) / var_y
    shares["idiosyncratic"] = sum(e * e for e in resid) / (t - 1) / var_y
    # Exact in-sample up to float error; normalise so the shares a chart draws sum to 1.
    total_share = sum(shares.values())
    shares = {k: v / total_share for k, v in shares.items()}

    def coefficient(i: int) -> dict:
        return {"value": _r(coef[i], 4), "se_nw": _r(se_nw[i], 6),
                "t_nw": _r(_t(coef[i], se_nw[i]), 3), "t_ols": _r(_t(coef[i], se_ols[i]), 3)}

    idio_vol = math.sqrt(sum(e * e for e in resid) / fit["dof"] * TRADING_DAYS)

    # Rolling one-factor market beta, keyed to the window's last day.
    roll = rolling_beta(y, m)
    roll_points = [{"date": ret_dates[ROLLING_WINDOW - 1 + i], "beta": _r(b, 4)}
                   for i, b in enumerate(roll)]

    # Cumulative decomposition over the last year. The specific part is the
    # remainder, so the three parts sum to the stock's own log return exactly.
    span = min(DECOMPOSITION_SESSIONS, t)
    start = t - span
    cum_mkt = cum_sec = cum_tot = 0.0
    series = []
    for i in range(start, t):
        cum_tot += y[i]
        cum_mkt += coef[1] * m[i]
        if s_raw is not None:
            cum_sec += coef[2] * columns[1][i]
        series.append({"date": ret_dates[i], "total": _r(cum_tot), "market": _r(cum_mkt),
                       "sector": _r(cum_sec), "specific": _r(cum_tot - cum_mkt - cum_sec)})
    decomposition = {
        "sessions": span,
        "start": dates[index[start] - 1],
        "total": _r(cum_tot), "market": _r(cum_mkt),
        "sector": _r(cum_sec) if s_raw is not None else None,
        "specific": _r(cum_tot - cum_mkt - cum_sec),
        "series": _downsample(series, ROLLING_MAX_POINTS),
    }

    result = {
        "observations": t,
        "start": ret_dates[0],
        "end": ret_dates[-1],
        "has_sector": s_raw is not None,
        "nw_lag": fit["lag"],
        "alpha_annual": _r(coef[0] * TRADING_DAYS, 4),
        "alpha_t_nw": _r(_t(coef[0], se_nw[0]), 3),
        "alpha_t_ols": _r(_t(coef[0], se_ols[0]), 3),
        "alpha_se_nw_annual": _r(se_nw[0] * TRADING_DAYS, 4),
        "market_beta": coefficient(1),
        "sector_beta": coefficient(2) if s_raw is not None else None,
        "sector_market_beta": _r(g, 4),
        "r_squared": _r(fit["r_squared"], 4),
        "variance_share": {k: _r(v, 4) for k, v in shares.items()} | (
            {} if s_raw is not None else {"sector": None}),
        "idio_vol_annual": _r(idio_vol, 4),
        "total_vol_annual": _r(math.sqrt(var_y * TRADING_DAYS), 4),
        "rolling_beta": {"window": ROLLING_WINDOW,
                         "points": _downsample(roll_points, ROLLING_MAX_POINTS)},
        "decomposition": decomposition,
        "asymmetry": asymmetric_beta(y, m),
        "tail_dependence": tail_dependence(y, m),
    }
    result["reading"] = _reading(result)
    return result


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _reading(r: dict) -> str:
    shares = r["variance_share"]
    parts = [f"{_pct(shares['market'])} of this stock's daily variance is the market"]
    if r["has_sector"]:
        parts.append(f"{_pct(shares['sector'])} its sector beyond the market")
    parts.append(f"{_pct(shares['idiosyncratic'])} its own story")
    text = ", ".join(parts[:-1]) + " and " + parts[-1] + "."
    beta = r["market_beta"]["value"]
    text += f" Market beta {beta:.2f}"
    asym = r["asymmetry"]
    if asym["upside_beta"] is not None and asym["downside_beta"] is not None:
        text += (f" ({asym['upside_beta']:.2f} on up days, "
                 f"{asym['downside_beta']:.2f} on down days)")
    text += "."
    t_nw = r["alpha_t_nw"]
    if t_nw is not None:
        alpha = r["alpha_annual"]
        if abs(t_nw) >= 2:
            text += (f" Alpha of {alpha * 100:+.1f}% a year is statistically distinguishable "
                     f"from zero (Newey–West t = {t_nw:.1f}).")
        else:
            text += (f" Alpha of {alpha * 100:+.1f}% a year is within noise "
                     f"(Newey–West t = {t_nw:.1f}); do not read it as skill.")
    td = r["tail_dependence"]
    if td["probability"] is not None:
        text += (f" On the market's worst 5% of days the stock was in its own worst 5% "
                 f"{_pct(td['probability'])} of the time (5% if independent).")
    return text
