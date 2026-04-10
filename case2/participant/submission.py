"""
UChicago Trading Competition 2026 — Case 2 submission.

Strategy: Sector Sharpe Momentum + Long/Short Blend + Self-Calibrating
          Regime Gate + Vol-Regime-Scaled EWMA Vol Targeting

Pipeline at each rebalance (every 5 days):
  1. Sector signal: rank the 5 sectors by trailing 50-day Sharpe, z-score.
  2. Intraday tilt: cross-sectional z-score of (-realized intraday vol),
     blended into the sector signal with weight `iw`.
  3. Regime gate: `iw` is set by comparing current vol-of-vol (VOV) to
     the 33rd / 67th percentiles of the strategy's *own* historical VOV.
     Stable regime → iw = IW_HIGH (0.5); turbulent → iw = IW_LOW (0.0).
     Self-calibrating: no hard-coded vol-level thresholds.
  4. Long-only candidate: equal-weight base × (1 + SCALE * blended signal),
     truncated at zero.
  5. Dollar-neutral L/S candidate: w_i ∝ sign(sig_i)·|sig_i| / Σ|sig|, so
     sum(w) ≈ 0 and sum(|w|) = 1.
  6. Final target: 0.55 * long_only + 0.45 * long_short. The constant blend
     was selected on a walk-forward sweep; the L/S leg gives a permanent
     short exposure to the worst sectors, which is automatic recession
     protection without a regime detector.
  7. Vol targeting: scale gross exposure so EWMA portfolio vol ≈ TARGET_VOL,
     with TARGET_VOL itself shrunk when short-vol > long-vol.
  8. Enforce sum(|w|) ≤ 1; rebalance only if total |Δw| > MIN_DELTA.
  9. Crash safety: get_weights() is wrapped in try/except returning the
     last valid weights (or equal-weight) on any exception.

Walk-forward CV (3 folds, expanding window, 1-year OOS):
  Plain sector momentum:                  Mean=1.098, Min=0.962
  Long-only sector momentum + regime gate Mean=1.354, Min=1.103
  This submission (L/S blend, bias=0.55): Mean=1.422, Min=1.216

Synthetic recession stress test (6 scenarios, sector drawdowns):
  Mean alpha vs equal-weight long-only:   +3.60 Sharpe
  Min  alpha vs equal-weight long-only:   +1.79 Sharpe
"""

from __future__ import annotations

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


def long_short_tilt(signals: np.ndarray) -> np.ndarray:
    """Pure dollar-neutral long/short tilt from a z-scored signal.

    w_i ∝ sign(sig_i) * |sig_i|, normalized so sum(|w_i|) = 1.
    Long the positive signal, short the negative signal, dollar-neutral
    by construction (sum(w) ≈ 0 when signals are z-scored).
    """
    clipped = np.clip(signals, -2.0, 2.0)
    gross = float(np.abs(clipped).sum())
    if gross < 1e-10:
        return np.zeros_like(signals)
    return clipped / gross


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
    """Sector Sharpe momentum, L/S blend, regime-adaptive intraday tilt, vol targeting.

    Concentrates capital in sectors with the highest trailing risk-adjusted
    returns (50-day Sharpe window). A cross-sectional "low intraday vol" tilt
    is blended in dynamically based on a vol-of-vol regime gate: turned on in
    stable regimes (VOV low), turned off in turbulent regimes (VOV high).
    The final target is 55% long-only sector tilt + 45% dollar-neutral
    long/short on the same signal — the L/S leg shorts the bottom sectors
    of the Sharpe ranking and serves as a permanent recession hedge.
    EWMA vol targeting then scales exposure to ~13% annualized vol, with
    the target itself damped when short-vol diverges above long-vol.
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

    # --- self-calibrating regime gate -------------------------------------
    # The gate compares current VOV to percentiles of historical VOV computed
    # over all data available so far (causal). No absolute thresholds.
    VOV_INNER: int = 20            # inner window: rolling vol length (days)
    VOV_OUTER: int = 60            # outer window: rolling vov length (days)
    HI_PCT: int = 67               # VOV percentile → turbulent threshold
    LO_PCT: int = 33               # VOV percentile → stable threshold
    IW_HIGH: float = 0.50          # intraday-tilt weight in stable regime
    IW_LOW: float = 0.0            # intraday-tilt weight in turbulent regime

    # --- long/short bias --------------------------------------------------
    # Strategy is a constant blend of long-only sector tilt and a
    # dollar-neutral L/S tilt on the same sector signal. Constant blend
    # validated by walk-forward CV to be the best risk-adjusted point on
    # the bias sweep (Mean Sharpe 1.42, Min 1.22, no fold drop > 0.05).
    # The L/S leg gives the strategy a permanent short exposure that
    # harvests the bottom of the sector signal — automatic recession
    # protection without a regime detector.
    USE_LONG_SHORT: bool = True
    LS_BIAS: float = 0.55          # 55% long-only + 45% dollar-neutral L/S

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

    def _vov_distribution(self, daily_rets: np.ndarray):
        """Build the historical VOV distribution from training data only.

        Returns (current_vov, lo_band, hi_band) where the bands are the
        LO_PCT and HI_PCT percentiles of the historical VOV time series.
        Fully causal — uses only data available at the call site.
        """
        if daily_rets.shape[0] < self.VOV_INNER + self.VOV_OUTER:
            return None, None, None
        ew = daily_rets @ _EW

        # Step 1: rolling VOV_INNER-day std → "vol time series"
        if ew.shape[0] < self.VOV_INNER:
            return None, None, None
        vol_ts = np.lib.stride_tricks.sliding_window_view(ew, self.VOV_INNER).std(axis=1)
        if vol_ts.shape[0] < self.VOV_OUTER:
            return None, None, None

        # Step 2: rolling VOV_OUTER-window std of vol_ts → "vov time series"
        vov_ts = np.lib.stride_tricks.sliding_window_view(vol_ts, self.VOV_OUTER).std(axis=1)
        if vov_ts.shape[0] < 30:
            return None, None, None

        current = float(vov_ts[-1])
        lo_band = float(np.percentile(vov_ts, self.LO_PCT))
        hi_band = float(np.percentile(vov_ts, self.HI_PCT))
        return current, lo_band, hi_band

    def _adaptive_intraday_weight(self, daily_rets: np.ndarray) -> float:
        """Linearly interpolate iw between IW_LOW (turbulent) and IW_HIGH (stable),
        using percentiles of the strategy's own VOV history as the bands."""
        cur, lo_band, hi_band = self._vov_distribution(daily_rets)
        if cur is None:
            return self.IW_HIGH  # cold-start: default to stable
        if cur >= hi_band:
            return self.IW_LOW
        if cur <= lo_band:
            return self.IW_HIGH
        t = (hi_band - cur) / max(hi_band - lo_band, 1e-12)
        return self.IW_LOW + t * (self.IW_HIGH - self.IW_LOW)

    def _long_short_bias(self) -> float:
        """Constant blend between long-only tilt and dollar-neutral L/S tilt."""
        return self.LS_BIAS if self.USE_LONG_SHORT else 1.0

    def _build_target(self, daily_rets: np.ndarray, intraday_sig: np.ndarray | None = None) -> np.ndarray:
        """Compute target weights: sector Sharpe tilt + regime-gated intraday tilt + vol targeting."""
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, self.LOOKBACK)

        iw = self._adaptive_intraday_weight(daily_rets)
        if intraday_sig is not None and iw > 0:
            sig = (1 - iw) * sig + iw * intraday_sig

        # Long-only and dollar-neutral L/S candidates, blended at constant
        # bias. Bias = 0.55 was selected by walk-forward sweep as the
        # best risk-adjusted point that passes the gate on every fold.
        long_only = tilt_weights(_EW, sig, scale=self.SIGNAL_SCALE)
        bias = self._long_short_bias()
        if bias < 1.0 - 1e-9:
            ls = long_short_tilt(sig)
            target = bias * long_only + (1.0 - bias) * ls
        else:
            target = long_only

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
    return MyStrategy()
