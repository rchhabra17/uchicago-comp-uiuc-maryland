"""Combine winning improvements and run validation tests (#13-15).

Winners from experiments:
  - Adaptive Vol Target (vlb=60): Mean 1.173, Min 1.017
  - Intraday Vol signal (w=0.1): Mean 1.128, Min 0.938

Test combinations + permutation test + cost analysis + monthly decomposition.
"""

from __future__ import annotations
import sys
sys.path.insert(0, ".")
import warnings
import numpy as np
import math

warnings.filterwarnings("ignore")

from validate import run_backtest, annualized_sharpe, TICKS_PER_DAY, TRADING_DAYS_PER_YEAR
from submission import (
    load_prices, load_meta, PublicMeta, StrategyBase, _EW, N_ASSETS,
    ewma_vol, enforce_gross_limit, tilt_weights, sector_sharpe_signal,
)

prices = load_prices()
meta = load_meta()
ticks_per_year = TRADING_DAYS_PER_YEAR * TICKS_PER_DAY
sector_ids = meta.sector_id


def run_cv(strat_class, label, **kwargs):
    sharpes = []
    costs = []
    max_dds = []
    for k in range(2, 5):
        train_end = k * ticks_per_year
        test_end = (k + 1) * ticks_per_year
        if test_end > prices.shape[0]:
            break
        strat = strat_class(**kwargs)
        result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
        dr = result["daily_returns"]
        sr = annualized_sharpe(dr)
        cum = np.cumprod(1 + dr)
        max_dd = float(np.min(cum / np.maximum.accumulate(cum) - 1))
        sharpes.append(sr)
        costs.append(sum(result["daily_costs"]) * 100)
        max_dds.append(max_dd * 100)
    mean_s = np.mean(sharpes)
    min_s = np.min(sharpes)
    print(f"  {label:55s}  F1={sharpes[0]:+.4f}  F2={sharpes[1]:+.4f}  F3={sharpes[2]:+.4f}  "
          f"Mean={mean_s:+.4f}  Min={min_s:+.4f}  Cost={np.mean(costs):.3f}%  MaxDD={np.min(max_dds):.1f}%")
    return sharpes, mean_s, min_s


# ===========================================================================
# Combined Strategy: Adaptive VT + Intraday Vol
# ===========================================================================

class CombinedStrategy(StrategyBase):
    """Best of both: adaptive vol target + optional intraday vol signal."""
    def __init__(self, vol_regime_lb=60, intraday_weight=0.0, base_tv=0.13,
                 signal_scale=30.0, lookback=50):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._vol_regime_lb = vol_regime_lb
        self._intraday_w = intraday_weight
        self._base_tv = base_tv
        self._scale = signal_scale
        self._lookback = lookback

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            intraday_sig = self._intraday_vol_signal(train_prices) if self._intraday_w > 0 else None
            self._last_weights = self._build(daily_rets, intraday_sig)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            intraday_sig = self._intraday_vol_signal(price_history) if self._intraday_w > 0 else None
            target = self._build(daily_rets, intraday_sig)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _intraday_vol_signal(self, tick_prices):
        n_ticks = tick_prices.shape[0]
        n_days = n_ticks // self._tpd
        n_assets = tick_prices.shape[1]
        if n_days < 20:
            return np.zeros(n_assets)
        reshaped = tick_prices[:n_days * self._tpd].reshape(n_days, self._tpd, -1)
        lookback = min(20, n_days)
        recent = reshaped[-lookback:]
        intraday_rets = np.diff(np.log(np.maximum(recent, 1e-12)), axis=1)
        daily_intraday_vol = intraday_rets.std(axis=1)
        avg_vol = daily_intraday_vol.mean(axis=0)
        signal = -avg_vol
        signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
        return signal

    def _build(self, daily_rets, intraday_sig=None):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, self._lookback)

        if intraday_sig is not None and self._intraday_w > 0:
            sig = (1 - self._intraday_w) * sig + self._intraday_w * intraday_sig

        target = tilt_weights(_EW, sig, scale=self._scale)

        # Adaptive vol targeting
        if daily_rets.shape[0] >= self._vol_regime_lb:
            ew_rets = daily_rets @ _EW
            short_vol = np.std(ew_rets[-20:]) * np.sqrt(252)
            long_vol = np.std(ew_rets[-self._vol_regime_lb:]) * np.sqrt(252)
            vol_ratio = short_vol / max(long_vol, 0.5)
            adaptive_tv = self._base_tv * (1.0 / max(vol_ratio, 0.5))
            adaptive_tv = np.clip(adaptive_tv, 0.08, 0.20)

            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(adaptive_tv / pvol, 1.0)
                target = target * scale
        elif daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(self._base_tv / pvol, 1.0)
                target = target * scale

        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# MAIN
# ===========================================================================

print("=" * 120)
print("COMBINING WINNING IMPROVEMENTS + VALIDATION TESTS")
print("=" * 120)

# --- COMBINATION TESTS ---
print("\n--- COMBINATION: Adaptive VT + Intraday Vol ---")
from submission import MyStrategy

print("\n  References:")
run_cv(MyStrategy, "BASELINE (current)")
run_cv(CombinedStrategy, "Adaptive VT vlb=60 only", vol_regime_lb=60)

print("\n  Combinations:")
for vlb in [60, 80, 120]:
    for iw in [0.0, 0.05, 0.1, 0.15, 0.2]:
        for tv in [0.12, 0.13, 0.14]:
            run_cv(CombinedStrategy, f"AVT vlb={vlb} iw={iw} tv={tv}",
                   vol_regime_lb=vlb, intraday_weight=iw, base_tv=tv)

# --- BEST COMBO with scale/lookback variations ---
print("\n  Scale/Lookback variations on best combo:")
for sc in [25, 30, 35]:
    for lb in [40, 50, 60]:
        run_cv(CombinedStrategy, f"AVT60 sc={sc} lb={lb}",
               vol_regime_lb=60, signal_scale=sc, lookback=lb)


# ===========================================================================
# TEST 13: Permutation Test
# ===========================================================================
print("\n" + "=" * 120)
print("TEST 13: PERMUTATION TEST — Is sector momentum signal real?")
print("=" * 120)

# Run 200 permutations with shuffled sector labels
from submission import MyStrategy as MS
np.random.seed(42)
n_perms = 200
perm_sharpes = []

for i in range(n_perms):
    # Shuffle sector labels
    shuffled_meta_sector = sector_ids.copy()
    np.random.shuffle(shuffled_meta_sector)

    # Create a modified meta with shuffled sectors
    from submission import PublicMeta
    shuffled_meta = PublicMeta(
        sector_id=shuffled_meta_sector,
        spread_bps=meta.spread_bps,
        borrow_bps_annual=meta.borrow_bps_annual,
    )

    # Run on fold 3 only (fastest, most representative)
    k = 4
    train_end = k * ticks_per_year
    test_end = (k + 1) * ticks_per_year
    strat = MS()
    result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, shuffled_meta)
    sr = annualized_sharpe(result["daily_returns"])
    perm_sharpes.append(sr)
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{n_perms} permutations done...")

perm_sharpes = np.array(perm_sharpes)
real_sharpe = 1.3587  # fold 3 with real labels

p_value = np.mean(perm_sharpes >= real_sharpe)
print(f"\n  Real Sharpe (fold 3): {real_sharpe:.4f}")
print(f"  Permutation distribution: mean={perm_sharpes.mean():.4f}, std={perm_sharpes.std():.4f}")
print(f"  Permutation max: {perm_sharpes.max():.4f}")
print(f"  Permutation min: {perm_sharpes.min():.4f}")
print(f"  p-value (fraction >= real): {p_value:.4f}")
print(f"  Signal is {'REAL (p < 0.05)' if p_value < 0.05 else 'NOT SIGNIFICANT'}")


# ===========================================================================
# TEST 14: Cost Drag Analysis
# ===========================================================================
print("\n" + "=" * 120)
print("TEST 14: COST DRAG ANALYSIS PER REBALANCE")
print("=" * 120)

# Run fold 3 and analyze daily costs
k = 4
train_end = k * ticks_per_year
test_end = (k + 1) * ticks_per_year
strat = MS()
result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
daily_costs = result["daily_costs"]
dr = result["daily_returns"]

# Costs are recorded at each rebalance event (entry + each day's rebalance)
nonzero_costs = daily_costs[daily_costs > 0]
print(f"\n  Total cost events: {len(nonzero_costs)} (out of {len(daily_costs)} days)")
print(f"  Total cost: {sum(daily_costs)*100:.4f}%")
print(f"  Avg cost per event: {np.mean(nonzero_costs)*100:.4f}%")
print(f"  Max cost event: {np.max(nonzero_costs)*100:.4f}%")
print(f"  Cost distribution (bps):")
for pct in [25, 50, 75, 90, 95, 99]:
    print(f"    P{pct}: {np.percentile(nonzero_costs, pct)*10000:.2f} bps")

# Identify wasteful rebalances (high cost, near-zero return)
print(f"\n  Rebalance efficiency (cost vs. next-day |return|):")
rebal_days = np.where(daily_costs[1:] > 0.00001)[0]  # skip entry
if len(rebal_days) > 0 and len(dr) > 0:
    n_wasteful = 0
    for d in rebal_days:
        if d < len(dr):
            cost = daily_costs[d + 1]
            next_ret = abs(dr[d]) if d < len(dr) else 0
            if cost > next_ret and cost > 0.0001:
                n_wasteful += 1
    print(f"    'Wasteful' rebalances (cost > |next_return|): {n_wasteful}/{len(rebal_days)}")


# ===========================================================================
# TEST 15: Monthly Sharpe Decomposition
# ===========================================================================
print("\n" + "=" * 120)
print("TEST 15: MONTHLY SHARPE DECOMPOSITION")
print("=" * 120)

for k in range(2, 5):
    train_end = k * ticks_per_year
    test_end = (k + 1) * ticks_per_year
    strat = MS()
    result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
    dr = result["daily_returns"]

    # Split into ~21-day months
    days_per_month = 21
    n_months = len(dr) // days_per_month
    monthly_rets = []
    monthly_sharpes = []
    for m in range(n_months):
        month_dr = dr[m * days_per_month:(m + 1) * days_per_month]
        mret = np.prod(1 + month_dr) - 1
        monthly_rets.append(mret)
        mmu = np.mean(month_dr)
        msd = np.std(month_dr, ddof=1)
        msharpe = np.sqrt(252) * mmu / msd if msd > 1e-12 else 0
        monthly_sharpes.append(msharpe)

    monthly_rets = np.array(monthly_rets)
    monthly_sharpes = np.array(monthly_sharpes)

    fold_sharpe = annualized_sharpe(dr)
    pos_months = np.sum(monthly_rets > 0)
    neg_months = np.sum(monthly_rets <= 0)

    print(f"\n  Fold {k-1} (test year {k}): Sharpe={fold_sharpe:+.4f}")
    print(f"    Months: {n_months} total, {pos_months} positive, {neg_months} negative")
    print(f"    Win rate: {pos_months/n_months*100:.0f}%")
    print(f"    Monthly returns: mean={np.mean(monthly_rets)*100:+.2f}%, "
          f"std={np.std(monthly_rets)*100:.2f}%, "
          f"min={np.min(monthly_rets)*100:+.2f}%, max={np.max(monthly_rets)*100:+.2f}%")
    print(f"    Monthly Sharpe: mean={np.mean(monthly_sharpes):+.2f}, "
          f"std={np.std(monthly_sharpes):.2f}")
    print(f"    Best month:  {np.max(monthly_rets)*100:+.2f}%")
    print(f"    Worst month: {np.min(monthly_rets)*100:+.2f}%")

    # Is it driven by one big month?
    sorted_rets = np.sort(monthly_rets)
    cum_without_best = np.prod(1 + sorted_rets[:-1]) - 1
    cum_total = np.prod(1 + monthly_rets) - 1
    print(f"    Total return: {cum_total*100:+.2f}%, without best month: {cum_without_best*100:+.2f}%")
