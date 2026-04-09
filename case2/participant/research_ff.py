"""Fama-French / Long-Short factor strategy research.

Test whether L/S strategies can overcome the high borrow costs in this universe.
Factors tested (using only price data — no fundamentals available):
  1. Cross-sectional momentum (WML: winners minus losers)
  2. Sector momentum (long best sector, short worst sector)
  3. Low-volatility anomaly (long low-vol, short high-vol)
  4. Short-term reversal (long recent losers, short recent winners)
  5. Sector-relative momentum (long outperformers within sector, short underperformers)
  6. Combined multi-factor L/S
  7. 130/30 and partial-short variants
"""

from __future__ import annotations
import sys
sys.path.insert(0, ".")
import warnings
import math
import numpy as np
from validate import run_backtest, annualized_sharpe, TICKS_PER_DAY, TRADING_DAYS_PER_YEAR
from submission import load_prices, load_meta, PublicMeta, StrategyBase, _EW, N_ASSETS, ewma_vol, enforce_gross_limit

warnings.filterwarnings("ignore")

prices = load_prices()
meta = load_meta()
ticks_per_year = TRADING_DAYS_PER_YEAR * TICKS_PER_DAY
sector_ids = meta.sector_id
spread_bps = meta.spread_bps
borrow_bps = meta.borrow_bps_annual

print("=" * 80)
print("FAMA-FRENCH / LONG-SHORT STRATEGY RESEARCH")
print("=" * 80)
print(f"\nBorrow costs: min={borrow_bps.min():.0f} avg={borrow_bps.mean():.0f} max={borrow_bps.max():.0f} bps/yr")
print(f"Spread costs: min={spread_bps.min():.0f} avg={spread_bps.mean():.0f} max={spread_bps.max():.0f} bps")
print(f"Sectors: {np.unique(sector_ids)}, 5 assets each\n")


# ---------------------------------------------------------------------------
# Factor Construction Helpers
# ---------------------------------------------------------------------------

def rank_zscore(x):
    """Rank-based z-score (robust to outliers)."""
    ranks = np.argsort(np.argsort(x)).astype(float)
    ranks = (ranks - ranks.mean()) / max(ranks.std(), 1e-10)
    return ranks


def ls_weights_from_signal(signal, long_frac=0.4, short_frac=0.4, gross=1.0):
    """Long top `long_frac`, short bottom `short_frac` of signal, normalized."""
    n = len(signal)
    ranks = np.argsort(np.argsort(-signal))  # 0 = highest signal
    long_k = max(int(n * long_frac), 1)
    short_k = max(int(n * short_frac), 1)

    w = np.zeros(n)
    long_mask = ranks < long_k
    short_mask = ranks >= (n - short_k)

    w[long_mask] = 1.0 / long_k
    w[short_mask] = -1.0 / short_k

    # Scale to gross exposure limit
    total_gross = np.sum(np.abs(w))
    if total_gross > 1e-10:
        w = w * (gross / total_gross)
    return w


def ls_weights_proportional(signal, gross=1.0):
    """Proportional L/S: positive signal → long, negative → short, scaled."""
    pos = np.maximum(signal, 0)
    neg = np.minimum(signal, 0)
    total = np.sum(pos) + np.sum(np.abs(neg))
    if total < 1e-10:
        return np.zeros_like(signal)
    return signal / total * gross


def long_tilt_from_signal(signal, long_budget=1.0):
    """Long-only tilt: overweight high-signal assets, underweight low-signal."""
    base = np.ones(len(signal)) / len(signal)
    z = rank_zscore(signal)
    tilted = base * np.exp(0.5 * z)
    tilted = np.maximum(tilted, 0)
    return tilted / tilted.sum() * long_budget


# ---------------------------------------------------------------------------
# Factor Strategies
# ---------------------------------------------------------------------------

class FactorStrategy(StrategyBase):
    def __init__(self, factor_fn, name="Factor", rebal_freq=5, vol_target=None):
        self._factor_fn = factor_fn
        self._name = name
        self._rebal_freq = rebal_freq
        self._vol_target = vol_target
        self._last_weights = _EW.copy()
        self._tpd = TICKS_PER_DAY
        self._sector_ids = None

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1 :: self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._compute_weights(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % self._rebal_freq != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1 :: self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            w = self._compute_weights(daily_rets)
            self._last_weights = w
            return w
        except Exception:
            return self._last_weights.copy()

    def _compute_weights(self, daily_rets):
        w = self._factor_fn(daily_rets, self._sector_ids)

        # Optional vol targeting
        if self._vol_target and daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ w
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(self._vol_target / pvol, 1.0)
                w = w * scale

        return enforce_gross_limit(w, 1.0)


# ---------------------------------------------------------------------------
# Factor definitions
# ---------------------------------------------------------------------------

# 1. Cross-sectional Momentum (WML): long 12-month winners, short losers
def xsmom_factor(daily_rets, sector_ids, lookback=252, skip=21):
    """Classic Fama-French momentum: 12-1 month returns."""
    n_days = daily_rets.shape[0]
    if n_days < lookback:
        lookback = n_days
    # Cumulative return over lookback, skip most recent month
    end = max(n_days - skip, 1)
    start = max(end - lookback, 0)
    cum_ret = daily_rets[start:end].sum(axis=0)  # log return sum
    return ls_weights_from_signal(cum_ret)

# 2. Sector Momentum L/S: long best sector, short worst
def sector_mom_ls(daily_rets, sector_ids, lookback=50):
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return _EW.copy()
    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = {}
    for s in unique_sectors:
        sr = recent[:, sector_ids == s].mean(axis=1)
        sector_sharpes[s] = sr.mean() / max(sr.std(), 1e-10)
    signal = np.array([sector_sharpes[s] for s in sector_ids])
    return ls_weights_from_signal(signal)

# 3. Low-Volatility Anomaly: long low-vol, short high-vol
def low_vol_factor(daily_rets, sector_ids, lookback=60):
    if daily_rets.shape[0] < lookback:
        lookback = daily_rets.shape[0]
    recent = daily_rets[-lookback:]
    vols = recent.std(axis=0)
    signal = -vols  # negative vol = long low-vol
    return ls_weights_from_signal(signal)

# 4. Short-term Reversal: long recent losers, short recent winners
def reversal_factor(daily_rets, sector_ids, lookback=5):
    if daily_rets.shape[0] < lookback:
        return np.zeros(daily_rets.shape[1])
    recent_ret = daily_rets[-lookback:].sum(axis=0)
    signal = -recent_ret  # negative = reversal
    return ls_weights_from_signal(signal)

# 5. Sector-Relative Momentum: L/S within sectors
def sector_relative_mom(daily_rets, sector_ids, lookback=50):
    if daily_rets.shape[0] < lookback:
        lookback = max(daily_rets.shape[0], 10)
    recent = daily_rets[-lookback:]
    cum_ret = recent.sum(axis=0)
    n_assets = daily_rets.shape[1]

    # Demean within sector
    signal = np.zeros(n_assets)
    for s in np.unique(sector_ids):
        mask = sector_ids == s
        sector_ret = cum_ret[mask]
        signal[mask] = sector_ret - sector_ret.mean()
    return ls_weights_from_signal(signal)

# 6. Combined Multi-Factor L/S
def multi_factor_ls(daily_rets, sector_ids):
    """Equal-weight combination of momentum + low-vol + reversal signals."""
    n = daily_rets.shape[1]
    signals = np.zeros(n)

    # Momentum (252d, skip 21d)
    if daily_rets.shape[0] >= 60:
        lb = min(252, daily_rets.shape[0])
        end = max(daily_rets.shape[0] - 21, 1)
        start = max(end - lb, 0)
        mom = daily_rets[start:end].sum(axis=0)
        signals += rank_zscore(mom)

    # Low-vol
    lb = min(60, daily_rets.shape[0])
    vols = daily_rets[-lb:].std(axis=0)
    signals += rank_zscore(-vols)

    # Reversal (5d)
    if daily_rets.shape[0] >= 5:
        rev = -daily_rets[-5:].sum(axis=0)
        signals += rank_zscore(rev)

    return ls_weights_from_signal(signals)


# --- LONG-ONLY versions (no borrow costs) ---

def xsmom_long_only(daily_rets, sector_ids, lookback=252, skip=21):
    n_days = daily_rets.shape[0]
    if n_days < 60:
        return _EW.copy()
    lb = min(lookback, n_days)
    end = max(n_days - skip, 1)
    start = max(end - lb, 0)
    cum_ret = daily_rets[start:end].sum(axis=0)
    return long_tilt_from_signal(cum_ret)

def low_vol_long_only(daily_rets, sector_ids, lookback=60):
    lb = min(lookback, daily_rets.shape[0])
    vols = daily_rets[-lb:].std(axis=0)
    return long_tilt_from_signal(-vols)

def multi_factor_long_only(daily_rets, sector_ids):
    n = daily_rets.shape[1]
    signals = np.zeros(n)
    if daily_rets.shape[0] >= 60:
        lb = min(252, daily_rets.shape[0])
        end = max(daily_rets.shape[0] - 21, 1)
        start = max(end - lb, 0)
        signals += rank_zscore(daily_rets[start:end].sum(axis=0))
    lb = min(60, daily_rets.shape[0])
    signals += rank_zscore(-daily_rets[-lb:].std(axis=0))
    return long_tilt_from_signal(signals)

def sector_mom_long_only(daily_rets, sector_ids, lookback=50):
    """Current strategy reference: sector Sharpe tilt, long-only."""
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return _EW.copy()
    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = {}
    for s in unique_sectors:
        sr = recent[:, sector_ids == s].mean(axis=1)
        sector_sharpes[s] = sr.mean() / max(sr.std(), 1e-10)
    signal = np.array([sector_sharpes[s] for s in sector_ids])
    base = np.ones(n_assets) / n_assets
    z = rank_zscore(signal)
    tilted = base * (1 + 30.0 * np.clip(z, -2, 2))
    tilted = np.maximum(tilted, 0)
    return tilted / tilted.sum()


# --- 130/30 variants ---

def sector_mom_130_30(daily_rets, sector_ids, lookback=50):
    """130/30: 130% long best sectors, 30% short worst sectors."""
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return _EW.copy()
    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = {}
    for s in unique_sectors:
        sr = recent[:, sector_ids == s].mean(axis=1)
        sector_sharpes[s] = sr.mean() / max(sr.std(), 1e-10)
    signal = np.array([sector_sharpes[s] for s in sector_ids])
    z = rank_zscore(signal)

    w = np.zeros(n_assets)
    # Long side: top 60% of signal
    long_mask = z >= np.percentile(z, 40)
    short_mask = z < np.percentile(z, 40)

    if long_mask.sum() > 0:
        w[long_mask] = 0.65 / long_mask.sum()
    if short_mask.sum() > 0:
        w[short_mask] = -0.15 / short_mask.sum()

    # Ensure gross <= 1
    return enforce_gross_limit(w, 1.0)


def multi_factor_130_30(daily_rets, sector_ids):
    """130/30 multi-factor."""
    n = daily_rets.shape[1]
    signals = np.zeros(n)

    if daily_rets.shape[0] >= 60:
        # Sector momentum
        unique_sectors = np.unique(sector_ids)
        lookback = 50
        recent = daily_rets[-lookback:]
        sector_sharpes = {}
        for s in unique_sectors:
            sr = recent[:, sector_ids == s].mean(axis=1)
            sector_sharpes[s] = sr.mean() / max(sr.std(), 1e-10)
        sect_signal = np.array([sector_sharpes[s] for s in sector_ids])
        signals += 2.0 * rank_zscore(sect_signal)  # double weight on sector mom

        # Low-vol
        vols = daily_rets[-60:].std(axis=0)
        signals += rank_zscore(-vols)

    z = rank_zscore(signals)
    w = np.zeros(n)
    long_mask = z >= np.percentile(z, 30)
    short_mask = z < np.percentile(z, 30)
    if long_mask.sum() > 0:
        w[long_mask] = 0.65 / long_mask.sum()
    if short_mask.sum() > 0:
        w[short_mask] = -0.15 / short_mask.sum()
    return enforce_gross_limit(w, 1.0)


# ===========================================================================
# Run all strategies through competition evaluator (3-fold CV)
# ===========================================================================

strategies = [
    # L/S strategies
    ("XS Momentum L/S", lambda dr, si: xsmom_factor(dr, si), 5, None),
    ("Sector Mom L/S", lambda dr, si: sector_mom_ls(dr, si), 5, None),
    ("Low-Vol L/S", lambda dr, si: low_vol_factor(dr, si), 5, None),
    ("Reversal L/S (5d)", lambda dr, si: reversal_factor(dr, si), 5, None),
    ("Sector-Relative Mom L/S", lambda dr, si: sector_relative_mom(dr, si), 5, None),
    ("Multi-Factor L/S", lambda dr, si: multi_factor_ls(dr, si), 5, None),

    # L/S with vol targeting
    ("XS Momentum L/S VT13", lambda dr, si: xsmom_factor(dr, si), 5, 0.13),
    ("Sector Mom L/S VT13", lambda dr, si: sector_mom_ls(dr, si), 5, 0.13),
    ("Low-Vol L/S VT13", lambda dr, si: low_vol_factor(dr, si), 5, 0.13),
    ("Multi-Factor L/S VT13", lambda dr, si: multi_factor_ls(dr, si), 5, 0.13),

    # Long-only factor tilts
    ("XS Mom Long-Only", lambda dr, si: xsmom_long_only(dr, si), 5, None),
    ("Low-Vol Long-Only", lambda dr, si: low_vol_long_only(dr, si), 5, None),
    ("Multi-Factor Long-Only", lambda dr, si: multi_factor_long_only(dr, si), 5, None),
    ("Sector Mom Long-Only (ref)", lambda dr, si: sector_mom_long_only(dr, si), 5, None),

    # Long-only with vol targeting
    ("XS Mom LO VT13", lambda dr, si: xsmom_long_only(dr, si), 5, 0.13),
    ("Low-Vol LO VT13", lambda dr, si: low_vol_long_only(dr, si), 5, 0.13),
    ("Multi-Factor LO VT13", lambda dr, si: multi_factor_long_only(dr, si), 5, 0.13),

    # 130/30 variants
    ("Sector Mom 130/30", lambda dr, si: sector_mom_130_30(dr, si), 5, None),
    ("Multi-Factor 130/30", lambda dr, si: multi_factor_130_30(dr, si), 5, None),
    ("Sector Mom 130/30 VT13", lambda dr, si: sector_mom_130_30(dr, si), 5, 0.13),
    ("Multi-Factor 130/30 VT13", lambda dr, si: multi_factor_130_30(dr, si), 5, 0.13),

    # Different rebalance frequencies
    ("XS Mom L/S rb10", lambda dr, si: xsmom_factor(dr, si), 10, None),
    ("XS Mom L/S rb20", lambda dr, si: xsmom_factor(dr, si), 20, None),
    ("Sector Mom L/S rb10", lambda dr, si: sector_mom_ls(dr, si), 10, None),
]

print(f"\n{'Strategy':35s}  {'F1':>8s}  {'F2':>8s}  {'F3':>8s}  {'Mean':>8s}  {'Min':>8s}  {'Costs':>8s}")
print("-" * 100)

for name, factor_fn, rb, vt in strategies:
    sharpes = []
    costs = []
    for k in range(2, 5):
        train_end = k * ticks_per_year
        test_end = (k + 1) * ticks_per_year
        if test_end > prices.shape[0]:
            break
        strat = FactorStrategy(factor_fn, name, rebal_freq=rb, vol_target=vt)
        result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
        sr = annualized_sharpe(result["daily_returns"])
        sharpes.append(sr)
        costs.append(sum(result["daily_costs"]) * 100)

    if len(sharpes) == 3:
        avg_cost = np.mean(costs)
        print(f"{name:35s}  {sharpes[0]:+8.4f}  {sharpes[1]:+8.4f}  {sharpes[2]:+8.4f}  "
              f"{np.mean(sharpes):+8.4f}  {np.min(sharpes):+8.4f}  {avg_cost:7.3f}%")

# Current strategy reference
print("-" * 100)
from submission import MyStrategy
sharpes = []
costs = []
for k in range(2, 5):
    train_end = k * ticks_per_year
    test_end = (k + 1) * ticks_per_year
    strat = MyStrategy()
    result = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
    sr = annualized_sharpe(result["daily_returns"])
    sharpes.append(sr)
    costs.append(sum(result["daily_costs"]) * 100)
print(f"{'>>> CURRENT SUBMISSION <<<':35s}  {sharpes[0]:+8.4f}  {sharpes[1]:+8.4f}  {sharpes[2]:+8.4f}  "
      f"{np.mean(sharpes):+8.4f}  {np.min(sharpes):+8.4f}  {np.mean(costs):7.3f}%")

print("\n" + "=" * 80)
print("BORROW COST ANALYSIS")
print("=" * 80)

# Show how much borrow cost eats per sector
for s in np.unique(sector_ids):
    mask = sector_ids == s
    avg_borrow = borrow_bps[mask].mean()
    avg_spread = spread_bps[mask].mean()
    print(f"  Sector {s}: avg borrow={avg_borrow:.0f} bps/yr, avg spread={avg_spread:.1f} bps")

avg_borrow_cost = borrow_bps.mean() / 10000 * 0.5  # assume 50% short on avg for L/S
print(f"\n  Estimated annual borrow drag for 50% short: {avg_borrow_cost*10000:.0f} bps = {avg_borrow_cost*100:.2f}%")
print(f"  That's {avg_borrow_cost*100/math.sqrt(252)*252:.1f}% annualized drag on returns")
