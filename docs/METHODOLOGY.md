# Methodology

Every number StockLens prints, how it is computed, and why the constant is what
it is. Where a constant was picked rather than measured, this says so.

## Contract

- Each family returns a score in **[-1, +1]** or **abstains** (`None`).
- Abstaining is not scoring zero. Zero is a claim ("neutral"); abstaining is
  the absence of one. Averaging the two would let missing evidence dilute
  strong evidence.
- Every non-finite intermediate (NaN, ±∞) is clamped to "no opinion" rather
  than propagating.
- Every score carries a human-readable `reason` and the inputs in `detail`.

## Indicators (`analytics/indicators.py`)

| Indicator | Definition | Window |
|---|---|---|
| SMA | arithmetic mean of last *n* closes | 20 / 50 / 200 |
| EMA | seeded with the SMA of the first *n* values, α = 2/(n+1) | — |
| RSI | Wilder (1978): average gain / average loss with α = 1/n smoothing | 14 |
| MACD | EMA12 − EMA26; signal = EMA9 of MACD; histogram = MACD − signal | 12/26/9 |
| Bollinger | SMA20 ± 2 × population σ | 20 |
| ATR | Wilder-smoothed true range | 14 |
| Stochastic %K | (close − low₁₄) / (high₁₄ − low₁₄) | 14 |

Each returns `None` until its full window exists.

## Families (`analytics/signals.py`)

### Technical — directional

```
rsi_leg   = +1 if RSI ≤ 30;  −1 if RSI ≥ 70;  else (50 − RSI) / 20
trend_leg = clamp(10 × (price − SMA20) / SMA20)        # 10% above saturates
macd_leg  = clamp(histogram / (0.02 × price))           # 2% of price saturates
score     = clamp(0.6·rsi_leg + 0.2·trend_leg + 0.2·macd_leg)
```

RSI dominates because the short-horizon premise is mean reversion; trend and
MACD temper it so an oversold stock in free fall is not scored as a bargain.
Weights are picked. Needs 35 sessions.

### Momentum — directional

12-1 month momentum (Jegadeesh & Titman, 1993): the return from 12 months ago
to 1 month ago, skipping the latest month because one-month returns tend to
reverse (Jegadeesh, 1990).

```
mom_leg   = clamp(momentum / 0.40)       # ±40% ≈ 10th/90th percentile of large-cap 12-1 momentum
align_leg = +1 if price > SMA50 > SMA200; −1 if price < SMA50 < SMA200; else 0
score     = clamp(0.75·mom_leg + 0.25·align_leg)
```

Falls back to a shorter window (≥ 6 months) when a full year is not available,
and says so in the reason.

### Relative strength — directional

`clamp((r_stock − r_benchmark) / 0.20)` over 63 sessions. Twenty percentage
points saturates so only genuine outliers pin at ±1; a scale that saturates
too early turns a measurement into a coin flip.

### Fundamental — directional

Eight falsifiable checks on the latest annual filing:

| Check | Pass rule |
|---|---|
| profitable | net income > 0 |
| gross_margin | gross profit / revenue ≥ 30% |
| operating_margin | operating income / revenue ≥ 10% |
| revenue_growth | revenue > prior-year revenue |
| current_ratio | current assets / current liabilities ≥ 1.0 |
| debt_to_equity | long-term debt / equity ≤ 1.5 (unknown if equity ≤ 0) |
| free_cash_flow | operating cash flow − capex > 0 |
| cash_conversion | operating cash flow / net income ≥ 0.8 (unknown if loss-making) |

`score = (passed − failed) / answered`. An item the company did not report is
**unknown**, not failed. Fewer than four answered checks and the family
abstains. Thresholds are conventional screening levels, picked, and listed on
the page next to each result. They are sector-blind: a bank's current ratio
and a utility's leverage read badly by construction — the obvious refinement
is sector-relative bands.

### Valuation — directional

```
earnings_yield = net income / market cap
fcf_yield      = (operating cash flow − capex) / market cap
score          = mean over available yields of clamp((yield − 5%) / 3%)
```

2% reads as expensive and 8% as cheap, bracketing the long-run S&P 500
earnings yield of roughly 4–6%. Picked, not fitted. Market cap uses the latest
reported share count and the latest close.

### Sentiment — directional

A small, inspectable finance lexicon in the spirit of Loughran & McDonald
(2011) — general-purpose sentiment dictionaries misread finance, where
"liability" or "tax" are not negative.

```
tone(headline) = (positive − negative) / (positive + negative), or None if neither
score          = Σ tone·w / Σ w,   w = 0.5^(age_days / 3)     # 3-day half-life
```

Needs three toned headlines; two is a coin flip, not a reading.

### Volume — conviction

Ratio of the last session to the prior 20-session average. ≥ 2× → +1,
≤ 0.5× → −1, linear `1.5 × (ratio − 1)` between.

### News flow — conviction

`clamp(log2((recent₃d + 0.5) / baseline) / 3)` where the baseline is the
14-day daily rate scaled to 3 days (floored at 0.5). Measured in doublings so
busy and quiet names are comparable; three doublings saturates.

## Blend (`analytics/composite.py`)

```
score      = Σ wᵢ·sᵢ / Σ wᵢ                over AVAILABLE directional families
coverage   = Σ w(available) / Σ w(model)   over ALL directional families in the model
conviction = mean(volume, news_flow)       over available conviction families
confidence = clamp(coverage × (1 + 0.25 × conviction), 0, 1)
```

| Family | Weight |
|---|---|
| technical | 0.20 |
| momentum | 0.20 |
| fundamental | 0.20 |
| relative_strength | 0.15 |
| sentiment | 0.15 |
| valuation | 0.10 |

Weights are transparent defaults, not a fitted claim. Price-derived families
carry the most weight because they exist for every symbol every day.

**Attribution.** Each directional family's `contribution = sᵢ × wᵢ / Σ w(available)`.
Contributions sum exactly to the score (tested), so every point of the
composite is attributable.

**Stance.** Constructive at ≥ +0.20, Cautious at ≤ −0.20, Neutral between, and
"Insufficient evidence" whenever confidence < 0.45. These are descriptive
labels on a threshold, printed next to the threshold — not recommendations.

## Risk (`analytics/risk.py`)

All on daily log returns, annualised with 252 sessions.

| Statistic | Definition | Sample floor |
|---|---|---|
| Volatility | sample σ × √252 over the last year | 20 |
| EWMA volatility | RiskMetrics (1996), λ = 0.94 | 20 |
| Max drawdown | worst peak-to-trough over the last year | 2 |
| VaR 95% (1-day) | historical 5th percentile loss, last 500 days | 100 |
| Expected shortfall 95% | mean of losses beyond VaR | 100 |
| Sharpe | mean / σ × √252, zero risk-free rate | 60 |
| Sortino | mean / downside deviation × √252 | 60 |
| Beta | cov(stock, benchmark) / var(benchmark) | 60 |
| Hurst | rescaled-range (R/S) slope over doubling window sizes | 128 |
| Variance ratio | Lo & MacKinlay (1988), q = 5 | 100 |

VaR is **historical**, not parametric: daily equity returns are fat-tailed,
and a normal VaR understates exactly the days it exists to describe.

R/S Hurst is biased upward in short samples; read 0.45–0.60 as
indistinguishable from a random walk at these lengths.

**One-sigma range.** `price × exp(±σ_EWMA × √(h/252))` for h = 5, 21, 63
sessions. Centred on today's price on purpose: it is a statement about spread,
not direction.

## Evaluation (`evaluate.py`)

Walk-forward: at each session *t*, score each symbol using only bars ≤ *t*,
then compare with the return from *t* to *t + h*.

| Metric | Why |
|---|---|
| Mean daily rank IC (Spearman) | The standard cross-sectional skill measure; published models live at 0.02–0.08 |
| IC t-statistic on every *h*-th day | Overlapping horizons are dependent; using all days would overstate significance by ~√h |
| Top-minus-bottom third spread | Whether the ranking separates returns in money terms |
| Hit rate **and** base rate | A 55% hit rate in a market that rose 56% of the time is no skill |

Only price families are evaluated. The free fundamentals and news sources are
not point-in-time — a 10-K filed in February would be visible on January
dates — so including them would leak the future and flatter the result.

## Known limitations

- Weights and thresholds are picked, documented defaults; nothing in the open
  edition claims to have fitted them.
- Fundamental thresholds are sector-blind.
- The sentiment lexicon is deliberately small and English-only.
- The IEX free feed covers a fraction of consolidated volume, which affects the
  volume family on real data.
- Demo data is a random walk; any edge measured on it is noise by construction.

## References

- Jegadeesh, N. & Titman, S. (1993). Returns to buying winners and selling losers. *Journal of Finance* 48(1).
- Jegadeesh, N. (1990). Evidence of predictable behavior of security returns. *Journal of Finance* 45(3).
- Loughran, T. & McDonald, B. (2011). When is a liability not a liability? *Journal of Finance* 66(1).
- Lo, A. & MacKinlay, A. C. (1988). Stock market prices do not follow random walks. *Review of Financial Studies* 1(1).
- J.P. Morgan/Reuters (1996). *RiskMetrics — Technical Document*, 4th ed.
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*.
- Grinold, R. & Kahn, R. (1999). *Active Portfolio Management*, 2nd ed. (information coefficient)
