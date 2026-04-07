from __future__ import annotations

"""Local evaluator for the portfolio optimization case.

Splits the 5-year training data into a 4-year train and 1-year pseudo-holdout,
then runs the exact same scoring mechanics used in the competition. This lets
you see how your strategy performs — including transaction costs, borrowing
costs, and the tick-level wealth process — before submitting.

Usage:
    python validate.py          # single 4-year train / 1-year holdout split
    python validate.py --cv     # time-series cross-validation (3 folds)
"""

import argparse
import math

import numpy as np
import pandas as pd

from submission import PublicMeta, create_strategy, load_meta, load_prices, ASSET_COLUMNS

# ---------------------------------------------------------------------------
# Constants (must match the competition runtime)
# ---------------------------------------------------------------------------

N_ASSETS = 25
TICKS_PER_DAY = 30
TRADING_DAYS_PER_YEAR = 252
IMPACT_MULT = 2.5
DT_YEAR = 1.0 / (TRADING_DAYS_PER_YEAR * TICKS_PER_DAY)

TRAIN_YEARS = 4
HOLDOUT_YEARS = 1
TRAIN_TICKS = TRAIN_YEARS * TRADING_DAYS_PER_YEAR * TICKS_PER_DAY
HOLDOUT_TICKS = HOLDOUT_YEARS * TRADING_DAYS_PER_YEAR * TICKS_PER_DAY

# ---------------------------------------------------------------------------
# Evaluation helpers (copied from the competition runtime)
# ---------------------------------------------------------------------------


def project_to_gross_limit(w: np.ndarray) -> np.ndarray:
    """Project weights back onto the L1 gross-exposure constraint (<=1)."""
    w = np.asarray(w, dtype=float).copy()
    gross = float(np.sum(np.abs(w)))
    if not np.isfinite(gross):
        return w
    if gross > 1.0:
        w /= gross
    return w


def _transaction_cost(
    spread: np.ndarray, delta_weights: np.ndarray, impact_mult: float
) -> tuple[float, float]:
    """Linear and quadratic trading costs for a change in portfolio weights."""
    linear = float(np.sum((spread / 2.0) * np.abs(delta_weights)))
    quadratic = float(np.sum((impact_mult * spread) * (delta_weights**2)))
    return linear, quadratic


def _hold_fixed_weights_one_day(
    wealth: float,
    weights: np.ndarray,
    logret: np.ndarray,
    borrow: np.ndarray,
    *,
    day: int,
) -> float:
    """Advance wealth over one trading day while holding fixed portfolio weights."""
    t0 = day * TICKS_PER_DAY
    t_begin = t0 + 1 if day == 0 else t0
    for t in range(t_begin, t0 + TICKS_PER_DAY):
        pnl = float(np.sum(weights * (np.exp(logret[t]) - 1.0)))
        borrow_cost = float(np.sum(np.maximum(-weights, 0.0) * borrow) * DT_YEAR)
        wealth *= 1.0 + pnl - borrow_cost
    return wealth


def _history_through_day(
    train_prices: np.ndarray, hold_prices: np.ndarray, day: int
) -> np.ndarray:
    """History visible to the strategy after observing `day` holdout days."""
    cutoff = (day + 1) * TICKS_PER_DAY
    return np.vstack([train_prices, hold_prices[:cutoff]])


def annualized_sharpe(daily_returns: np.ndarray) -> float:
    """Annualized Sharpe ratio with zero risk-free rate."""
    x = np.asarray(daily_returns, dtype=float)
    mu, sd = float(np.mean(x)), float(np.std(x, ddof=1))
    if not np.isfinite(sd) or sd < 1e-12:
        return -np.inf if mu <= 0 else np.inf
    return math.sqrt(TRADING_DAYS_PER_YEAR) * mu / sd


def run_backtest(
    train_prices: np.ndarray,
    hold_prices: np.ndarray,
    strategy,
    meta: PublicMeta,
) -> dict:
    """Run the tick-level wealth process on the pseudo-holdout period."""
    spread = np.asarray(meta.spread_bps, dtype=float) / 1e4
    borrow = np.asarray(meta.borrow_bps_annual, dtype=float) / 1e4

    strategy.fit(train_prices, meta, ticks_per_day=TICKS_PER_DAY)
    weights = project_to_gross_limit(strategy.get_weights(train_prices, meta, day=0))
    assert np.all(np.isfinite(weights)), "Non-finite weights at initialization"

    wealth = 1.0
    entry_linear, entry_quadratic = _transaction_cost(spread, weights, IMPACT_MULT)
    wealth *= 1.0 - (entry_linear + entry_quadratic)

    logret = np.zeros_like(hold_prices)
    logret[1:] = np.log(hold_prices[1:] / hold_prices[:-1])

    n_days = hold_prices.shape[0] // TICKS_PER_DAY
    daily_returns = np.zeros(n_days)
    daily_costs = np.zeros(n_days + 1)
    daily_costs[0] = entry_linear + entry_quadratic

    for day in range(n_days):
        wealth_start = wealth
        try:
            wealth = _hold_fixed_weights_one_day(wealth, weights, logret, borrow, day=day)
        except FloatingPointError:
            wealth = float("nan")
        if wealth <= 0 or not np.isfinite(wealth):
            daily_returns[day:] = -1.0
            return {
                "daily_returns": daily_returns,
                "daily_costs": daily_costs[: day + 1],
                "blown_up": True,
            }

        history = _history_through_day(train_prices, hold_prices, day)
        target = project_to_gross_limit(strategy.get_weights(history, meta, day=day + 1))
        assert np.all(np.isfinite(target)), f"Non-finite weights on day {day}"

        delta = target - weights
        linear, quadratic = _transaction_cost(spread, delta, IMPACT_MULT)
        trade_cost = linear + quadratic
        wealth *= 1.0 - trade_cost
        daily_costs[day + 1] = trade_cost
        daily_returns[day] = wealth / wealth_start - 1.0
        weights = target

    return {
        "daily_returns": daily_returns,
        "daily_costs": daily_costs,
        "blown_up": False,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _report(label: str, result: dict) -> float:
    """Print a results block and return the Sharpe."""
    dr = result["daily_returns"]
    costs = result["daily_costs"]
    sharpe = annualized_sharpe(dr)
    total_return = float(np.prod(1.0 + dr) - 1.0)
    total_cost = float(np.sum(costs))
    cum = np.cumprod(1.0 + dr)
    max_dd = float(np.min(np.minimum.accumulate(cum) / np.maximum.accumulate(cum) - 1.0))

    print(f"\n  [{label}]")
    if result["blown_up"]:
        print("  ** STRATEGY BLEW UP **")
    print(f"  Annualized Sharpe:  {sharpe:+.4f}")
    print(f"  Total return:       {total_return:+.2%}")
    print(f"  Total txn costs:    {total_cost:.4%}")
    print(f"  Max drawdown:       {max_dd:.2%}")
    return sharpe


def _run_single_split(prices: np.ndarray, meta: PublicMeta, strategy):
    """Default mode: 4-year train / 1-year holdout."""
    train_prices = prices[:TRAIN_TICKS]
    hold_prices = prices[TRAIN_TICKS : TRAIN_TICKS + HOLDOUT_TICKS]

    print(f"  Train: {train_prices.shape[0]:,} ticks ({TRAIN_YEARS} years)")
    print(f"  Holdout: {hold_prices.shape[0]:,} ticks ({HOLDOUT_YEARS} year)")
    print("\nRunning backtest...")

    result = run_backtest(train_prices, hold_prices, strategy, meta)

    print("\n" + "=" * 50)
    print("RESULTS (4-year train / 1-year pseudo-holdout)")
    print("=" * 50)
    _report("Fold 1", result)
    print("=" * 50)


def _run_cv(prices: np.ndarray, meta: PublicMeta, strategy):
    """Time-series CV: expanding train window, 1-year test folds."""
    ticks_per_year = TRADING_DAYS_PER_YEAR * TICKS_PER_DAY
    total_years = prices.shape[0] // ticks_per_year

    # folds: train on years 0..k-1, test on year k  (k = 2, 3, 4)
    folds = []
    for k in range(2, total_years):
        train_end = k * ticks_per_year
        test_end = (k + 1) * ticks_per_year
        if test_end > prices.shape[0]:
            break
        folds.append((k, train_end, test_end))

    print(f"  {len(folds)} CV folds (expanding window, 1-year test)")
    print("\nRunning backtests...")

    sharpes = []
    for k, train_end, test_end in folds:
        # create_strategy() each fold so fit() starts fresh
        from submission import create_strategy as _cs
        strat = _cs()
        result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
        label = f"Train years 0-{k-1}, test year {k}"
        sr = _report(label, result)
        sharpes.append(sr)

    print("\n" + "=" * 50)
    print("CROSS-VALIDATION SUMMARY")
    print("=" * 50)
    for i, (k, _, _) in enumerate(folds):
        print(f"  Fold {i+1} (test year {k}):  Sharpe = {sharpes[i]:+.4f}")
    print(f"  ---")
    print(f"  Mean Sharpe:   {np.mean(sharpes):+.4f}")
    print(f"  Std Sharpe:    {np.std(sharpes, ddof=1):.4f}")
    print(f"  Min Sharpe:    {np.min(sharpes):+.4f}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Validate your portfolio strategy")
    parser.add_argument("--cv", action="store_true", help="Run time-series cross-validation (3 folds)")
    args = parser.parse_args()

    print("Loading data...")
    prices = load_prices()
    meta = load_meta()

    assert prices.shape[1] == N_ASSETS, f"Expected {N_ASSETS} assets, got {prices.shape[1]}"
    total_ticks = prices.shape[0]
    print(f"  {total_ticks:,} ticks, {total_ticks // TICKS_PER_DAY} days, {N_ASSETS} assets")

    print("\nCreating strategy...")
    strategy = create_strategy()
    print(f"  Strategy: {strategy.__class__.__name__}")

    if args.cv:
        _run_cv(prices, meta, strategy)
    else:
        _run_single_split(prices, meta, strategy)


if __name__ == "__main__":
    main()
