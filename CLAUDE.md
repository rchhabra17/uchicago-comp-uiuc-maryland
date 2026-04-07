# CLAUDE.md — UChicago Trading Competition 2026, Case 2

## Mission
Maximize the **annualized Sharpe ratio** of a long/short portfolio over 25 assets across 5 sectors,
evaluated on the 12 months immediately following the provided training CSV. Final code is due
**11:59 PM CST, Thursday, April 9, 2026**. Code that fails to run on any tick = 0 points.

Sharpe is computed as `sqrt(252) * mean(r_t) / std(r_t)` on **net** returns (after costs/borrow).
Variance reduction is as valuable as return generation. A consistent 1.5 Sharpe beats a lumpy 2.5.

## Hard Constraints (read every time before editing code)
- **Universe:** 25 assets, 5 sectors (5 each). Intraday data: **30 ticks/day**.
- **Weights:** can be +, −, or 0. **Sum of |w_i| ≤ 1.** Violations are rescaled proportionally by the
  evaluator — but rescaling distorts intent, so enforce the constraint ourselves.
- **Rebalance cadence:** algorithm receives price history each day and returns new weights. Costs are
  charged on weight *changes* at end-of-day rebalance.
- **Costs (must model exactly — they appear in scoring):**
  - Linear: `0.5 * spread_bps * |Δw_i|`
  - Quadratic: `2.5 * spread_bps * (Δw_i)^2`
  - Borrow (per tick, shorts only): `short_exposure_i * borrow_bps_i * (1/252/30)`
  - Total cost per rebalance = sum of linear + quadratic across assets.
- **Returns within a day:** portfolio return per tick = Σ w_i * simple_return_i, where simple return
  is derived from log return (`exp(log_ret) - 1`). Borrow charge applies every tick.
- **Environment:** Python 3.12, only `numpy`, `pandas`, `scikit-learn`, `scipy`. No other deps unless
  explicitly approved on Ed. Test in a clean venv that mirrors this before submitting.
- **Failure mode:** any exception on any tick disqualifies us. **Wrap the strategy entry point in a
  try/except that falls back to the last valid weights (or equal-weight) on error.**

## Inputs Provided
- CSV of intraday tick prices for 25 assets (5 years × ~252 days × 30 ticks).
- Per-asset metadata: sector label, bid-ask spread (bps), borrow cost (bps annualized).
- Stub code with an example evaluator. **Treat the stub evaluator as a sanity check, not as ground
  truth for final scoring** — packet explicitly warns against this.

## Project Layout
/data/            # raw CSV, never modified
/src/
strategy.py     # the submitted entry point (keep this minimal & robust)
features.py     # signal construction (momentum, reversion, vol, sector)
covariance.py   # covariance estimators (Ledoit-Wolf, OAS, shrinkage, factor)
optimizer.py    # weight construction (RPA, mean-variance, robust)
costs.py        # exact replica of evaluator cost model
evaluator.py    # local backtester matching the spec
/tests/
test_constraints.py  # |w| sum, NaN, dim checks
test_evaluator.py    # parity vs. stub on a known portfolio
test_robustness.py   # missing data, flat asset, single-day input
/notebooks/       # exploration only — never imported by strategy.py

## How I Want You (Claude) to Work

### Default behaviors
1. **Read before writing.** Open `strategy.py`, `features.py`, and the stub evaluator before
   proposing changes. Don't guess at interfaces.
2. **Match the evaluator exactly.** Before any strategy work, verify our local `costs.py` and
   `evaluator.py` reproduce the stub's output to numerical precision on a fixed test portfolio.
   If they don't match, fix that first. Everything downstream depends on it.
3. **Always run the local backtest after a change** and report: in-sample Sharpe, out-of-sample
   Sharpe (held-out tail), turnover, average gross exposure, max drawdown, and cost drag (bps/yr).
4. **Show me deltas.** When you change a strategy component, run an A/B against the prior version on
   the same held-out window and report the Sharpe difference + a one-line attribution.
5. **Don't overfit.** If you're tempted to tune a hyperparameter on the full series, stop and use a
   walk-forward split instead. The packet specifically warns: *"Every year, people overfit. Don't
   let that be you."*
6. **Prefer simple + robust over clever + fragile.** A shrinkage-covariance risk-parity portfolio
   that always runs beats a brilliant ML model that crashes on tick 4,732.

### When proposing a new signal or estimator
State up front:
- What inefficiency it targets (momentum, reversion, sector dispersion, vol clustering, etc.)
- Why it should generalize out-of-sample (economic rationale, not just backtest fit)
- How it interacts with cost (does it require high turnover? if so, what's the breakeven edge?)
- The single-line null hypothesis it would falsify if it failed

### When in doubt, ask
If a design choice has real tradeoffs (e.g., "shrinkage intensity 0.3 vs adaptive", "rebalance daily
vs every 3 days"), surface the tradeoff and ask me. Don't silently pick.

## Strategy Roadmap (build in this order — don't skip ahead)

### Phase 1 — Infrastructure (do this before any modeling)
- [ ] Load CSV, validate shape (25 cols, ~37,800 rows), check for NaNs, gaps, zero/negative prices.
- [ ] Compute log returns, simple returns, daily aggregated returns.
- [ ] Implement `costs.py` — linear + quadratic transaction costs and per-tick borrow.
- [ ] Implement `evaluator.py` — full path simulation: weights → tick returns → cost deduction →
      Sharpe. Verify against stub evaluator on equal-weight, all-cash, and a random portfolio.
- [ ] Walk-forward split utility: train on rolling window, evaluate on next month, never peek ahead.
- [ ] Constraint enforcer: project any weight vector onto `{|w|_1 ≤ 1}` (use L1-ball projection).

### Phase 2 — Baselines (every later strategy must beat these)
- [ ] Equal-weight long-only (1/25). This is the floor.
- [ ] Inverse-volatility weighting.
- [ ] Risk-parity allocation (equal risk contribution) using sample covariance.
- [ ] Risk parity using **Ledoit-Wolf shrunk covariance** — this is usually the strongest naive baseline.

Record Sharpe + cost drag for each on the same held-out window. Put the table in `/notebooks/baselines.md`.

### Phase 3 — Covariance estimation (this is where most of the edge lives in 25-asset universes)
- [ ] Ledoit-Wolf shrinkage (sklearn has it).
- [ ] OAS shrinkage.
- [ ] **Sector-block shrinkage**: shrink toward a structured target where intra-sector correlation
      is the average intra-sector corr, inter-sector is the average inter-sector corr. This
      exploits the 5-sector structure the packet *explicitly* tells us is meaningful.
- [ ] Exponentially weighted covariance (favors recent regime).
- [ ] Compare condition numbers and out-of-sample portfolio variance, not just Sharpe.

### Phase 4 — Return signals (only after covariance is solid)
The packet hints at: momentum, mean reversion, cross-sector relationships, volatility clustering.
Build these as orthogonal signals, then combine.

- [ ] **Cross-sectional momentum** (rank assets by trailing return, long top / short bottom within sector).
- [ ] **Short-term reversion** (1–3 day reversal — common intraday phenomenon).
- [ ] **Sector-relative momentum** (asset return minus sector mean).
- [ ] **Volatility-scaled signals** — never feed raw returns into a combiner without normalizing by vol.
- [ ] **Signal combination**: simple average of z-scored signals first. Only move to regression /
      Lasso / Ridge if the simple combination already beats baselines and you have a held-out set
      to validate on.

For each signal: report IC (rank correlation of signal vs. next-period return), decay over horizons,
and turnover it implies.

### Phase 5 — Optimizer
- [ ] Mean-variance with shrunk covariance and signal-based expected returns.
- [ ] Add an L2 penalty on weights (tames the optimizer's appetite for extreme positions).
- [ ] Add a turnover penalty: `λ * ||w_new - w_old||_1` — this is how you trade off cost vs. signal.
- [ ] Solve via `scipy.optimize.minimize` with SLSQP, or closed-form if no inequality constraints
      beyond the L1 ball (then project).
- [ ] Sanity check: turn off signals → optimizer should converge to min-variance / risk-parity-like.

### Phase 6 — Robustness pass (do not skip)
- [ ] What happens on day 1 when we have ~0 history? (Fall back to equal-weight or inverse-vol.)
- [ ] What happens if a price series goes flat? (Vol = 0 → division by zero. Handle it.)
- [ ] What happens if covariance is singular? (Always shrink, never invert raw sample cov.)
- [ ] Time the strategy: must run comfortably within whatever per-tick budget the evaluator allows.
- [ ] Re-run full backtest 5x with different random seeds wherever randomness exists. Sharpe should
      be stable across seeds.

### Phase 7 — Final hardening (day before submission)
- [ ] Fresh venv, only the 4 allowed packages, run end-to-end.
- [ ] Try-except wrapper around `get_weights()` returning last valid weights on any exception.
- [ ] Log nothing to stdout in production code (may break the harness).
- [ ] Re-read Case 2 spec one more time looking for any rule we missed.

## Things That Will Lose Points (avoid these)
- **Lookahead bias.** Computing any statistic using data the algorithm wouldn't have at that tick.
  When in doubt, lag by one tick.
- **Overfitting to in-sample Sharpe.** If a strategy's IS Sharpe is 4 and OOS is 0.5, throw it away.
- **Ignoring transaction costs while tuning.** A signal with IC=0.05 and 200% daily turnover loses
  money after costs. Always optimize on net, not gross.
- **Trusting the sample covariance matrix.** With 25 assets and short windows, it's nearly singular.
  Always shrink.
- **Crashing on edge cases.** A Sharpe-3 strategy that throws on day 47 scores zero.
- **Submitting without testing in a clean Python 3.12 + 4-package environment.**

## Sanity Numbers (rough expectations — adjust as we learn the data)
- Equal-weight long-only Sharpe: probably ~0.5–1.0 depending on market regime in the test window.
- Risk parity with shrinkage: target 1.0–1.5.
- Well-built signal + optimizer combo: 1.5–2.5 is ambitious but realistic.
- Anything claiming >3 OOS on a 12-month window deserves extreme suspicion. Audit it for leakage.

## Communication Style I Want From You
- Lead with the result, then the reasoning.
- When you finish a task, give me a 3-line summary: what changed, OOS Sharpe before/after, what's next.
- If something looks too good, *say so* and audit before celebrating.
- Don't apologize. Don't pad. If there's a real tradeoff, surface it; if not, just ship.
- Use plots sparingly — a number in a table beats a chart for tracking iteration.

## Final Pre-Submission Checklist
- [ ] Runs end-to-end in clean venv (Python 3.12, numpy/pandas/sklearn/scipy only).
- [ ] No print statements, no file writes outside allowed paths, no network calls.
- [ ] Try/except fallback in place.
- [ ] Constraint `sum(|w|) ≤ 1` enforced internally (don't rely on rescaling).
- [ ] Local OOS Sharpe on held-out 12 months ≥ best baseline + meaningful margin.
- [ ] Walk-forward Sharpe is stable (not driven by one lucky month).
- [ ] Code is small, readable, commented at decision points.
- [ ] Submitted before 11:59 PM CST Thursday, April 9 — **with margin, not at 11:58.**