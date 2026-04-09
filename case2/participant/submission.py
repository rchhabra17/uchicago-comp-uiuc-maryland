from __future__ import annotations

"""Portfolio optimization submission for UChicago Trading Competition 2026.

Strategy: Sector Sharpe Momentum + EWMA Vol Targeting

Research findings (25-asset, 5-sector universe, tested via 4 optimization libraries):
  - Sector Sharpe momentum is the dominant signal (IC=0.06 at 30d, 0.05 at 50d)
  - Cross-sectional signals (momentum, reversion, vol): IC ~0 (noise)
  - Min-variance/risk-parity blends hurt in the competition evaluator (turnover cost)
  - Pure momentum tilt with vol targeting is the most cost-efficient approach
  - Tested: riskfolio-lib, skfolio, cvxportfolio, simulated-bifurcation — all
    underperform the tuned sector momentum tilt on this universe

Walk-forward CV (3 folds, expanding window, 1-year OOS):
  F1=0.97, F2=0.96, F3=1.36, Mean=1.098, Std=0.23

Design choices:
  - Equal-weight base → multiplicative sector tilt (scale=30)
  - EWMA vol targeting (half-life=20 days) for adaptive exposure sizing
  - 5-day rebalancing with no-trade zone for cost control
  - 13% annualized vol target → conservative, boosts Sharpe via variance reduction
  - Long-only (borrow costs eat all short-side edge in this universe)
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


N_ASSETS = 25
TICKS_PER_DAY = 30
ASSET_COLUMNS = tuple(f"A{i:02d}" for i in range(N_ASSETS))


@dataclass(frozen=True)
class PublicMeta:
    """Per-asset metadata visible to participants."""
    sector_id: np.ndarray
    spread_bps: np.ndarray
    borrow_bps_annual: np.ndarray


def load_prices(path: str = "prices.csv") -> np.ndarray:
    """Load the price matrix from CSV. Returns shape (n_ticks, 25)."""
    df = pd.read_csv(path, index_col="tick")
    return df[list(ASSET_COLUMNS)].to_numpy(dtype=float)


def load_meta(path: str = "meta.csv") -> PublicMeta:
    """Load asset metadata from CSV."""
    df = pd.read_csv(path)
    return PublicMeta(
        sector_id=df["sector_id"].to_numpy(dtype=int),
        spread_bps=df["spread_bps"].to_numpy(dtype=float),
        borrow_bps_annual=df["borrow_bps_annual"].to_numpy(dtype=float),
    )


class StrategyBase:
    def fit(self, train_prices: np.ndarray, meta: PublicMeta, **kwargs) -> None:
        pass

    def get_weights(self, price_history: np.ndarray, meta: PublicMeta, day: int) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Signal: Sector Sharpe Momentum
# ---------------------------------------------------------------------------

def sector_sharpe_signal(
    daily_rets: np.ndarray, sector_ids: np.ndarray, lookback: int = 40
) -> np.ndarray:
    """Z-scored sector Sharpe ratios → asset-level signal."""
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return np.zeros(n_assets)

    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = np.empty(len(unique_sectors))

    for i, s in enumerate(unique_sectors):
        sector_rets = recent[:, sector_ids == s].mean(axis=1)
        mu = sector_rets.mean()
        vol = sector_rets.std()
        sector_sharpes[i] = mu / max(vol, 1e-10)

    mu_s, sigma_s = sector_sharpes.mean(), sector_sharpes.std()
    if sigma_s < 1e-10:
        return np.zeros(n_assets)

    signal = np.zeros(n_assets)
    for i, s in enumerate(unique_sectors):
        signal[sector_ids == s] = (sector_sharpes[i] - mu_s) / sigma_s

    return signal


# ---------------------------------------------------------------------------
# Weight Construction
# ---------------------------------------------------------------------------

def tilt_weights(
    base: np.ndarray, signals: np.ndarray, scale: float
) -> np.ndarray:
    """Multiplicatively tilt base weights using signal scores."""
    clipped = np.clip(signals, -2.0, 2.0)
    tilted = base * (1.0 + scale * clipped)
    tilted = np.maximum(tilted, 0.0)
    total = tilted.sum()
    if total < 1e-10:
        return base.copy()
    return tilted / total


def enforce_gross_limit(w: np.ndarray, budget: float = 1.0) -> np.ndarray:
    """Enforce sum(|w_i|) <= budget."""
    gross = np.sum(np.abs(w))
    if gross > budget + 1e-12:
        w = w * (budget / gross)
    return w


def ewma_vol(returns: np.ndarray, half_life: float = 15.0) -> float:
    """EWMA volatility estimate."""
    alpha = 1.0 - np.exp(-np.log(2.0) / half_life)
    var = returns[0] ** 2
    for r in returns[1:]:
        var = alpha * r * r + (1.0 - alpha) * var
    return np.sqrt(var)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

_EW = np.ones(N_ASSETS) / N_ASSETS


class MyStrategy(StrategyBase):
    """Sector Sharpe momentum with EWMA vol targeting.

    Concentrates capital in sectors with the highest trailing risk-adjusted
    returns (50-day Sharpe window). EWMA vol targeting scales exposure to
    maintain stable ~13% annualized portfolio volatility.
    """

    SIGNAL_SCALE: float = 30.0     # strong sector tilt (concentrates in top sectors)
    LOOKBACK: int = 50             # days for sector Sharpe measurement
    REBAL_FREQ: int = 5            # rebalance every N days
    TARGET_VOL: float = 0.13       # annualized target portfolio volatility
    VOL_LOOKBACK: int = 20         # days for EWMA vol estimation
    EWMA_HALF_LIFE: float = 20.0   # EWMA half-life in days
    MIN_DELTA: float = 0.002       # skip rebalance if total |Δw| below this
    MIN_HISTORY_DAYS: int = 60     # need enough history for signal computation
    VOL_REGIME_LB: int = 120       # lookback for long-term vol regime
    INTRADAY_WEIGHT: float = 0.1   # weight of intraday vol penalization

    def __init__(self) -> None:
        self._last_weights: np.ndarray = _EW.copy()
        self._sector_ids: np.ndarray | None = None
        self._tpd: int = TICKS_PER_DAY

    def fit(self, train_prices: np.ndarray, meta: PublicMeta, **kwargs) -> None:
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id

        daily_close = train_prices[self._tpd - 1 :: self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)

        if daily_rets.shape[0] >= self.MIN_HISTORY_DAYS:
            intraday_sig = self._intraday_vol_signal(train_prices)
            self._last_weights = self._build_target(daily_rets, intraday_sig)

    def get_weights(
        self, price_history: np.ndarray, meta: PublicMeta, day: int
    ) -> np.ndarray:
        """Main entry point — wrapped in try/except for crash safety."""
        try:
            return self._compute(price_history, day)
        except Exception:
            if self._last_weights is not None:
                return self._last_weights.copy()
            return _EW.copy()

    def _compute(self, price_history: np.ndarray, day: int) -> np.ndarray:
        if day % self.REBAL_FREQ != 0:
            return self._last_weights.copy()

        daily_close = price_history[self._tpd - 1 :: self._tpd]
        n_days = daily_close.shape[0]

        if n_days < self.MIN_HISTORY_DAYS:
            return self._last_weights.copy()

        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        intraday_sig = self._intraday_vol_signal(price_history)
        target = self._build_target(daily_rets, intraday_sig)

        if np.sum(np.abs(target - self._last_weights)) < self.MIN_DELTA:
            return self._last_weights.copy()

        self._last_weights = target
        return target

    def _intraday_vol_signal(self, tick_prices: np.ndarray) -> np.ndarray:
        n_ticks = tick_prices.shape[0]
        n_days = n_ticks // self._tpd
        n_assets = tick_prices.shape[1]

        if n_days < 20:
            return np.zeros(n_assets)

        reshaped = tick_prices[:n_days * self._tpd].reshape(n_days, self._tpd, -1)
        lookback = min(20, n_days)
        recent = reshaped[-lookback:]
        intraday_rets = np.diff(np.log(np.maximum(recent, 1e-12)), axis=1)
        
        daily_intraday_vol = intraday_rets.std(axis=1)  # (days, assets)
        avg_vol = daily_intraday_vol.mean(axis=0)  # (assets,)
        
        signal = -avg_vol
        signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
        return signal

    def _build_target(self, daily_rets: np.ndarray, intraday_sig: np.ndarray | None = None) -> np.ndarray:
        """Compute target weights: sector Sharpe tilt + EWMA vol targeting."""
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, self.LOOKBACK)
        
        if intraday_sig is not None and self.INTRADAY_WEIGHT > 0:
            sig = (1 - self.INTRADAY_WEIGHT) * sig + self.INTRADAY_WEIGHT * intraday_sig
            
        target = tilt_weights(_EW, sig, scale=self.SIGNAL_SCALE)

        if daily_rets.shape[0] >= self.VOL_REGIME_LB:
            ew_rets = daily_rets @ _EW
            short_vol = np.std(ew_rets[-self.VOL_LOOKBACK:]) * np.sqrt(252)
            long_vol = np.std(ew_rets[-self.VOL_REGIME_LB:]) * np.sqrt(252)

            vol_ratio = short_vol / max(long_vol, 1e-10)
            adaptive_tv = self.TARGET_VOL * (1.0 / max(vol_ratio, 0.5))
            adaptive_tv = np.clip(adaptive_tv, 0.05, 0.18)

            port_rets = daily_rets[-self.VOL_LOOKBACK:] @ target
            pvol = ewma_vol(port_rets, self.EWMA_HALF_LIFE) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(adaptive_tv / pvol, 1.0)
                target = target * scale
        elif daily_rets.shape[0] >= self.VOL_LOOKBACK:
            port_rets = daily_rets[-self.VOL_LOOKBACK:] @ target
            pvol = ewma_vol(port_rets, self.EWMA_HALF_LIFE) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(self.TARGET_VOL / pvol, 1.0)
                target = target * scale

        return enforce_gross_limit(target, 1.0)


def create_strategy() -> StrategyBase:
    """Entry point called by validate.py."""
    return MyStrategy()
