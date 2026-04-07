from __future__ import annotations

"""Portfolio optimization submission for UChicago Trading Competition 2026.

Strategy: Vol-Targeted Sharpe-Ranked Sector Momentum

Inspired by NVIDIA's CVaR portfolio optimization framework, we use
risk-adjusted (Sharpe-ranked) sector selection instead of raw momentum.
This naturally downweights sectors whose gains came from high volatility
(less sustainable) and favors sectors with consistent risk-adjusted returns.

Pipeline:
  1. Equal-weight base (1/25)
  2. Compute each sector's trailing 50-day Sharpe ratio
  3. Z-score across sectors, tilt weights heavily toward top sectors
  4. Scale total exposure to target 15% annualized portfolio volatility
  5. Rebalance every 10 days to minimize transaction costs

Cross-validated across 3 time-series folds (years 2-4):
  Mean Sharpe ~1.02, Std ~0.25, Min ~0.84, all folds positive.
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
# Signal: Sharpe-ranked sector momentum
# ---------------------------------------------------------------------------

def sector_sharpe_signal(
    daily_rets: np.ndarray, sector_ids: np.ndarray, lookback: int = 50
) -> np.ndarray:
    """Rank sectors by trailing Sharpe ratio, z-score across sectors.

    Unlike raw momentum, this penalizes sectors whose gains came from
    high volatility — a more robust predictor of forward performance.
    All assets within a sector receive the same score.
    """
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return np.zeros(n_assets)

    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = np.empty(len(unique_sectors))

    for i, s in enumerate(unique_sectors):
        # Average daily return across assets in this sector
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
# Weight construction
# ---------------------------------------------------------------------------

def tilt_weights(
    base: np.ndarray, signals: np.ndarray, scale: float
) -> np.ndarray:
    """Multiplicatively tilt base weights using signal scores.

    w_i *= (1 + scale * clip(signal_i, -2, 2)), then renormalize.
    Long-only: any negative result is clipped to 0.
    """
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


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

_EW = np.ones(N_ASSETS) / N_ASSETS


class MyStrategy(StrategyBase):
    """Vol-targeted Sharpe-ranked sector momentum.

    Concentrates capital in sectors with the highest trailing risk-adjusted
    returns, then scales total exposure to maintain stable portfolio
    volatility. Rebalances every 10 days for cost efficiency.
    """

    SIGNAL_SCALE: float = 20.0      # strong sector tilt (concentrates in top sectors)
    LOOKBACK: int = 50              # days for sector Sharpe measurement
    REBAL_FREQ: int = 10            # rebalance every N days
    TARGET_VOL: float = 0.15        # annualized target portfolio volatility
    VOL_LOOKBACK: int = 20          # days for vol estimation
    MIN_DELTA: float = 0.002        # skip rebalance if total |Δw| below this

    def __init__(self) -> None:
        self._last_weights: np.ndarray | None = None
        self._sector_ids: np.ndarray | None = None
        self._tpd: int = TICKS_PER_DAY

    def fit(self, train_prices: np.ndarray, meta: PublicMeta, **kwargs) -> None:
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id

        # Compute initial allocation from training data
        daily_close = train_prices[self._tpd - 1 :: self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)

        target = self._build_target(daily_rets)
        self._last_weights = target

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
        # Only rebalance on schedule
        if self._last_weights is not None and day % self.REBAL_FREQ != 0:
            return self._last_weights.copy()

        # Daily closing prices → log returns
        daily_close = price_history[self._tpd - 1 :: self._tpd]
        n_days = daily_close.shape[0]

        if n_days < self.LOOKBACK + 10:
            self._last_weights = _EW.copy()
            return _EW.copy()

        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        target = self._build_target(daily_rets)

        # No-trade zone: skip tiny rebalances to save transaction costs
        if self._last_weights is not None:
            if np.sum(np.abs(target - self._last_weights)) < self.MIN_DELTA:
                return self._last_weights.copy()

        self._last_weights = target
        return target

    def _build_target(self, daily_rets: np.ndarray) -> np.ndarray:
        """Compute target weights: Sharpe-ranked sector tilt + vol targeting."""
        # Sector Sharpe signal → tilt from equal weight
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, self.LOOKBACK)
        target = tilt_weights(_EW, sig, scale=self.SIGNAL_SCALE)

        # Vol targeting: scale exposure so portfolio vol ≈ TARGET_VOL
        if daily_rets.shape[0] >= self.VOL_LOOKBACK:
            port_rets = daily_rets[-self.VOL_LOOKBACK :] @ target
            port_vol = float(np.std(port_rets)) * np.sqrt(252)
            if port_vol > 1e-6:
                scale = min(self.TARGET_VOL / port_vol, 1.0)
                target = target * scale

        return enforce_gross_limit(target, 1.0)


def create_strategy() -> StrategyBase:
    """Entry point called by validate.py."""
    return MyStrategy()
