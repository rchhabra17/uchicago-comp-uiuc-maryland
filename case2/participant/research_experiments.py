"""Systematic strategy experiments — 15 tests against the competition evaluator.

Each experiment modifies MyStrategy and runs 3-fold walk-forward CV.
Baseline: Mean Sharpe 1.098, Min fold 0.962 (sc30, hl20, tv13, lb50, rb5)
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
    TICKS_PER_DAY as TPD,
)

prices = load_prices()
meta = load_meta()
ticks_per_year = TRADING_DAYS_PER_YEAR * TICKS_PER_DAY
sector_ids = meta.sector_id


def run_cv(strat_class, label, **kwargs):
    """Run 3-fold walk-forward CV and report results."""
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
    delta = mean_s - 1.0979  # vs baseline
    marker = ">>>" if mean_s > 1.0979 else "   "
    print(f"{marker} {label:55s}  F1={sharpes[0]:+.4f}  F2={sharpes[1]:+.4f}  F3={sharpes[2]:+.4f}  "
          f"Mean={mean_s:+.4f}  Min={min_s:+.4f}  Δ={delta:+.4f}  "
          f"Cost={np.mean(costs):.3f}%  MaxDD={np.min(max_dds):.1f}%")
    return {"label": label, "sharpes": sharpes, "mean": mean_s, "min": min_s, "delta": delta}


# ===========================================================================
# EXPERIMENT 4: VWAP vs Closing Tick
# ===========================================================================

class VWAPStrategy(StrategyBase):
    """Use VWAP (mean of 30 intraday ticks) instead of closing tick."""
    def __init__(self, price_method="vwap", **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._price_method = price_method

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily = self._get_daily(train_prices)
        daily_rets = np.diff(np.log(np.maximum(daily, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily = self._get_daily(price_history)
            if daily.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _get_daily(self, tick_prices):
        if self._price_method == "vwap":
            # Mean of all 30 ticks in each day
            n_ticks = tick_prices.shape[0]
            n_days = n_ticks // self._tpd
            reshaped = tick_prices[:n_days * self._tpd].reshape(n_days, self._tpd, -1)
            return reshaped.mean(axis=1)
        elif self._price_method == "median":
            n_ticks = tick_prices.shape[0]
            n_days = n_ticks // self._tpd
            reshaped = tick_prices[:n_days * self._tpd].reshape(n_days, self._tpd, -1)
            return np.median(reshaped, axis=1)
        elif self._price_method == "open":
            return tick_prices[0::self._tpd]
        elif self._price_method == "mid":
            # Average of open and close
            opens = tick_prices[0::self._tpd]
            closes = tick_prices[self._tpd - 1::self._tpd]
            n = min(len(opens), len(closes))
            return (opens[:n] + closes[:n]) / 2
        else:  # close (baseline)
            return tick_prices[self._tpd - 1::self._tpd]

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)
        target = tilt_weights(_EW, sig, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 1: Intraday Features
# ===========================================================================

class IntradayStrategy(StrategyBase):
    """Use intraday features: intraday vol, open-close momentum, tick patterns."""
    def __init__(self, intraday_feature="intraday_vol", feature_weight=0.3, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._intraday_feature = intraday_feature
        self._feature_weight = feature_weight

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            intraday_sig = self._intraday_signal(train_prices)
            self._last_weights = self._build(daily_rets, intraday_sig)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            intraday_sig = self._intraday_signal(price_history)
            target = self._build(daily_rets, intraday_sig)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _intraday_signal(self, tick_prices):
        """Compute intraday feature signal."""
        n_ticks = tick_prices.shape[0]
        n_days = n_ticks // self._tpd
        n_assets = tick_prices.shape[1]

        if n_days < 20:
            return np.zeros(n_assets)

        reshaped = tick_prices[:n_days * self._tpd].reshape(n_days, self._tpd, -1)

        if self._intraday_feature == "intraday_vol":
            # Average intraday volatility per asset over last 20 days
            # Low intraday vol → more predictable → overweight
            lookback = min(20, n_days)
            recent = reshaped[-lookback:]
            intraday_rets = np.diff(np.log(np.maximum(recent, 1e-12)), axis=1)
            # std across ticks, then mean across days
            daily_intraday_vol = intraday_rets.std(axis=1)  # (days, assets)
            avg_vol = daily_intraday_vol.mean(axis=0)  # (assets,)
            # Negative = low vol is good
            signal = -avg_vol
            signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
            return signal

        elif self._intraday_feature == "open_close_mom":
            # Open-to-close return over last 20 days → intraday momentum
            lookback = min(20, n_days)
            recent = reshaped[-lookback:]
            opens = recent[:, 0, :]  # (days, assets)
            closes = recent[:, -1, :]
            oc_rets = np.log(np.maximum(closes, 1e-12)) - np.log(np.maximum(opens, 1e-12))
            signal = oc_rets.mean(axis=0)
            signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
            return signal

        elif self._intraday_feature == "close_to_open":
            # Overnight gap: close[t-1] to open[t]
            lookback = min(20, n_days)
            if n_days < 2:
                return np.zeros(n_assets)
            opens = reshaped[-lookback:, 0, :]     # (days, assets)
            prev_closes = reshaped[-lookback-1:-1, -1, :]
            n = min(opens.shape[0], prev_closes.shape[0])
            gaps = np.log(np.maximum(opens[:n], 1e-12)) - np.log(np.maximum(prev_closes[:n], 1e-12))
            signal = gaps.mean(axis=0)
            signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
            return signal

        elif self._intraday_feature == "last_tick_momentum":
            # Return in last 5 ticks of day (closing auction effect)
            lookback = min(20, n_days)
            recent = reshaped[-lookback:]
            late_rets = np.log(np.maximum(recent[:, -1, :], 1e-12)) - np.log(np.maximum(recent[:, -6, :], 1e-12))
            signal = late_rets.mean(axis=0)
            signal = (signal - signal.mean()) / max(signal.std(), 1e-10)
            return signal

        return np.zeros(n_assets)

    def _build(self, daily_rets, intraday_sig):
        # Primary signal: sector Sharpe
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)

        # Blend with intraday signal
        combined = (1 - self._feature_weight) * sig + self._feature_weight * intraday_sig

        target = tilt_weights(_EW, combined, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 6: Multi-Lookback Ensemble
# ===========================================================================

class MultiLookbackStrategy(StrategyBase):
    """Combine sector Sharpe at multiple lookbacks via inverse-variance weighting."""
    def __init__(self, lookbacks=(30, 50, 80), weighting="inverse_var", **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._lookbacks = lookbacks
        self._weighting = weighting

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        signals = []
        for lb in self._lookbacks:
            if daily_rets.shape[0] >= lb:
                sig = sector_sharpe_signal(daily_rets, self._sector_ids, lb)
                signals.append(sig)

        if not signals:
            return _EW.copy()

        if self._weighting == "equal":
            combined = np.mean(signals, axis=0)
        elif self._weighting == "inverse_var":
            # Weight each lookback by inverse of its signal variance
            variances = [max(np.var(s), 1e-10) for s in signals]
            inv_var = [1.0 / v for v in variances]
            total_iv = sum(inv_var)
            weights = [iv / total_iv for iv in inv_var]
            combined = sum(w * s for w, s in zip(weights, signals))
        elif self._weighting == "long_lookback_heavy":
            # Weight longer lookbacks more (more stable)
            n = len(signals)
            weights = np.arange(1, n + 1, dtype=float)
            weights = weights / weights.sum()
            combined = sum(w * s for w, s in zip(weights, signals))
        else:
            combined = np.mean(signals, axis=0)

        target = tilt_weights(_EW, combined, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 7: Cost-Aware Adaptive Rebalance
# ===========================================================================

class CostAwareStrategy(StrategyBase):
    """Only rebalance when expected alpha gain > estimated transaction cost."""
    def __init__(self, cost_threshold=0.0005, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._cost_threshold = cost_threshold
        self._spread = None

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        self._spread = meta.spread_bps / 1e4
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)

            # Estimate trading cost
            delta = target - self._last_weights
            linear_cost = np.sum((self._spread / 2) * np.abs(delta))
            quad_cost = np.sum(2.5 * self._spread * delta**2)
            est_cost = linear_cost + quad_cost

            # Only trade if the weight change is "worth it"
            weight_change = np.sum(np.abs(delta))
            if est_cost > self._cost_threshold and weight_change < 0.05:
                return self._last_weights.copy()

            if np.sum(np.abs(delta)) < 0.002:
                return self._last_weights.copy()

            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)
        target = tilt_weights(_EW, sig, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 2: Conditional Signal (Dispersion x Momentum)
# ===========================================================================

class DispersionStrategy(StrategyBase):
    """Scale signal strength by cross-sector dispersion — tilt more when sectors diverge."""
    def __init__(self, dispersion_mode="scale", **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._dispersion_mode = dispersion_mode

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)

        # Compute cross-sector dispersion (std of sector returns)
        lookback = min(50, daily_rets.shape[0])
        recent = daily_rets[-lookback:]
        unique_sectors = np.unique(self._sector_ids)
        sector_rets = []
        for s in unique_sectors:
            sector_rets.append(recent[:, self._sector_ids == s].mean(axis=1).sum())
        dispersion = np.std(sector_rets)

        # Historical dispersion for z-scoring
        if daily_rets.shape[0] >= 120:
            dispersions = []
            for start in range(0, daily_rets.shape[0] - lookback, 10):
                window = daily_rets[start:start + lookback]
                sr = []
                for s in unique_sectors:
                    sr.append(window[:, self._sector_ids == s].mean(axis=1).sum())
                dispersions.append(np.std(sr))
            disp_mean = np.mean(dispersions)
            disp_std = max(np.std(dispersions), 1e-10)
            disp_z = (dispersion - disp_mean) / disp_std
        else:
            disp_z = 0.0

        if self._dispersion_mode == "scale":
            # High dispersion → more tilt, low → less tilt
            dynamic_scale = 30.0 * (1 + 0.3 * np.clip(disp_z, -2, 2))
        elif self._dispersion_mode == "binary":
            # Only tilt when dispersion is above average
            dynamic_scale = 30.0 if disp_z > 0 else 15.0
        elif self._dispersion_mode == "aggressive":
            dynamic_scale = 30.0 * (1 + 0.5 * np.clip(disp_z, -2, 2))
        else:
            dynamic_scale = 30.0

        target = tilt_weights(_EW, sig, scale=dynamic_scale)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 3: Adaptive Vol Target
# ===========================================================================

class AdaptiveVolStrategy(StrategyBase):
    """Adaptive vol target: higher in calm markets, lower in volatile markets."""
    def __init__(self, base_tv=0.13, vol_regime_lookback=60, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._base_tv = base_tv
        self._vol_regime_lb = vol_regime_lookback

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)
        target = tilt_weights(_EW, sig, scale=30.0)

        if daily_rets.shape[0] >= self._vol_regime_lb:
            # Short-term vol (20d) vs long-term vol (60d)
            ew_rets = daily_rets @ _EW
            short_vol = np.std(ew_rets[-20:]) * np.sqrt(252)
            long_vol = np.std(ew_rets[-self._vol_regime_lb:]) * np.sqrt(252)

            vol_ratio = short_vol / max(long_vol, 1e-10)

            # When short vol < long vol (calm): increase target
            # When short vol > long vol (stress): decrease target
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
# EXPERIMENT 8: Vol-of-Vol as Secondary Signal
# ===========================================================================

class VolOfVolStrategy(StrategyBase):
    """Overweight sectors with stable volatility (low vol-of-vol)."""
    def __init__(self, vov_weight=0.2, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._vov_weight = vov_weight

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)

        # Compute vol-of-vol per sector
        n_assets = daily_rets.shape[1]
        vov_signal = np.zeros(n_assets)
        if daily_rets.shape[0] >= 80:
            unique_sectors = np.unique(self._sector_ids)
            for s in unique_sectors:
                mask = self._sector_ids == s
                sector_rets = daily_rets[:, mask].mean(axis=1)
                # Rolling 20d vol
                rolling_vols = []
                for i in range(20, len(sector_rets)):
                    rolling_vols.append(np.std(sector_rets[i-20:i]))
                if len(rolling_vols) > 10:
                    vov = np.std(rolling_vols[-40:]) / max(np.mean(rolling_vols[-40:]), 1e-10)
                    vov_signal[mask] = -vov  # negative = prefer stable vol

            # Z-score
            vov_mean = vov_signal.mean()
            vov_std = max(vov_signal.std(), 1e-10)
            vov_signal = (vov_signal - vov_mean) / vov_std

        combined = (1 - self._vov_weight) * sig + self._vov_weight * vov_signal
        target = tilt_weights(_EW, combined, scale=30.0)

        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 5: Exp-Weighted Covariance Risk Overlay
# ===========================================================================

class EWCovRiskStrategy(StrategyBase):
    """Adjust sector weights by exponentially-weighted sector risk estimate."""
    def __init__(self, risk_penalty=0.3, ew_halflife=30, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._risk_penalty = risk_penalty
        self._ew_hl = ew_halflife

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)

        # Compute EW sector vol and penalize high-vol sectors
        n_assets = daily_rets.shape[1]
        risk_adj = np.zeros(n_assets)
        alpha = 1 - np.exp(-np.log(2) / self._ew_hl)

        unique_sectors = np.unique(self._sector_ids)
        for s in unique_sectors:
            mask = self._sector_ids == s
            sector_rets = daily_rets[:, mask].mean(axis=1)
            # EWMA variance
            var = sector_rets[0] ** 2
            for r in sector_rets[1:]:
                var = alpha * r * r + (1 - alpha) * var
            ew_vol = np.sqrt(var) * np.sqrt(252)
            risk_adj[mask] = -ew_vol  # negative = penalize high vol

        # Z-score
        ra_mean = risk_adj.mean()
        ra_std = max(risk_adj.std(), 1e-10)
        risk_adj = (risk_adj - ra_mean) / ra_std

        combined = sig + self._risk_penalty * risk_adj
        target = tilt_weights(_EW, combined, scale=30.0)

        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 9: Mutual Information Sector Ranking
# ===========================================================================

class MutualInfoStrategy(StrategyBase):
    """Rank sectors by MI between past returns and future returns."""
    def __init__(self, mi_lookback=60, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._mi_lb = mi_lookback

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _mi_estimate(self, x, y, bins=10):
        """Simple binned mutual information estimate."""
        # Discretize
        x_bins = np.digitize(x, np.linspace(x.min() - 1e-10, x.max() + 1e-10, bins + 1))
        y_bins = np.digitize(y, np.linspace(y.min() - 1e-10, y.max() + 1e-10, bins + 1))

        # Joint and marginal histograms
        n = len(x)
        mi = 0.0
        for i in range(1, bins + 2):
            for j in range(1, bins + 2):
                pxy = np.sum((x_bins == i) & (y_bins == j)) / n
                px = np.sum(x_bins == i) / n
                py = np.sum(y_bins == j) / n
                if pxy > 0 and px > 0 and py > 0:
                    mi += pxy * np.log(pxy / (px * py))
        return mi

    def _build(self, daily_rets):
        n_assets = daily_rets.shape[1]
        unique_sectors = np.unique(self._sector_ids)

        if daily_rets.shape[0] < self._mi_lb + 10:
            # Fall back to Sharpe signal
            sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)
        else:
            # Compute MI for each sector: past sector return → future sector return
            mi_scores = {}
            for s in unique_sectors:
                mask = self._sector_ids == s
                sector_rets = daily_rets[:, mask].mean(axis=1)
                # Past lookback return vs next-5-day return
                past_rets = []
                future_rets = []
                for t in range(self._mi_lb, len(sector_rets) - 5):
                    past_rets.append(sector_rets[t - self._mi_lb:t].sum())
                    future_rets.append(sector_rets[t:t + 5].sum())
                if len(past_rets) > 20:
                    mi = self._mi_estimate(np.array(past_rets), np.array(future_rets))
                    # Also get direction: is the relationship positive?
                    corr = np.corrcoef(past_rets, future_rets)[0, 1]
                    mi_scores[s] = mi * np.sign(corr) if abs(corr) > 0.05 else 0
                else:
                    mi_scores[s] = 0

            sig = np.zeros(n_assets)
            scores = np.array([mi_scores[s] for s in unique_sectors])
            mu_s = scores.mean()
            std_s = max(scores.std(), 1e-10)
            for i, s in enumerate(unique_sectors):
                sig[self._sector_ids == s] = (mi_scores[s] - mu_s) / std_s

        target = tilt_weights(_EW, sig, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 10: Regime Switching (Correlation-Based)
# ===========================================================================

class RegimeSwitchStrategy(StrategyBase):
    """Switch to EW when cross-sector correlation is high, full tilt when dispersed."""
    def __init__(self, corr_threshold=0.6, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._corr_threshold = corr_threshold

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        # Compute average cross-sector correlation over last 60 days
        lookback = min(60, daily_rets.shape[0])
        recent = daily_rets[-lookback:]
        unique_sectors = np.unique(self._sector_ids)
        sector_rets = []
        for s in unique_sectors:
            sector_rets.append(recent[:, self._sector_ids == s].mean(axis=1))
        sector_rets = np.array(sector_rets)  # (5, lookback)
        corr_matrix = np.corrcoef(sector_rets)
        # Average off-diagonal correlation
        n = corr_matrix.shape[0]
        avg_corr = (corr_matrix.sum() - n) / (n * (n - 1))

        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)

        if avg_corr > self._corr_threshold:
            # High correlation → defensive, reduce tilt
            scale = 15.0
        else:
            # Low correlation → sectors are diverging, full tilt
            scale = 30.0

        target = tilt_weights(_EW, sig, scale=scale)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                s = min(0.13 / pvol, 1.0)
                target = target * s
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 11: Kelly Criterion Sizing
# ===========================================================================

class KellyStrategy(StrategyBase):
    """Size sector bets proportional to sector_sharpe / sector_variance."""
    def __init__(self, kelly_fraction=0.5, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._kelly_frac = kelly_fraction

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
            target = self._build(daily_rets)
            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets):
        lookback = 50
        n_assets = daily_rets.shape[1]
        if daily_rets.shape[0] < lookback:
            return _EW.copy()

        recent = daily_rets[-lookback:]
        unique_sectors = np.unique(self._sector_ids)

        # Kelly: f* = mu / sigma^2 for each sector
        kelly_weights = np.zeros(n_assets)
        for s in unique_sectors:
            mask = self._sector_ids == s
            sector_rets = recent[:, mask].mean(axis=1)
            mu = sector_rets.mean()
            var = sector_rets.var()
            if var > 1e-12:
                kelly = mu / var * self._kelly_frac
            else:
                kelly = 0
            kelly_weights[mask] = kelly / mask.sum()  # spread across assets in sector

        # Normalize to long-only, sum=1
        kelly_weights = np.maximum(kelly_weights, 0)
        total = kelly_weights.sum()
        if total < 1e-10:
            target = _EW.copy()
        else:
            target = kelly_weights / total

        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# EXPERIMENT 12: Drawdown Budget Overlay
# ===========================================================================

class DrawdownBudgetStrategy(StrategyBase):
    """Cut exposure when trailing drawdown exceeds threshold."""
    def __init__(self, dd_threshold=-0.05, cut_factor=0.5, **kw):
        self._last_weights = _EW.copy()
        self._sector_ids = None
        self._tpd = TICKS_PER_DAY
        self._dd_threshold = dd_threshold
        self._cut_factor = cut_factor
        self._peak_wealth = 1.0
        self._current_wealth = 1.0

    def fit(self, train_prices, meta, **kwargs):
        self._tpd = kwargs.get("ticks_per_day", TICKS_PER_DAY)
        self._sector_ids = meta.sector_id
        self._peak_wealth = 1.0
        self._current_wealth = 1.0
        daily_close = train_prices[self._tpd - 1::self._tpd]
        daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)
        if daily_rets.shape[0] >= 60:
            self._last_weights = self._build(daily_rets, 1.0)

    def get_weights(self, price_history, meta, day):
        try:
            if day % 5 != 0:
                return self._last_weights.copy()
            daily_close = price_history[self._tpd - 1::self._tpd]
            if daily_close.shape[0] < 61:
                return self._last_weights.copy()
            daily_rets = np.diff(np.log(np.maximum(daily_close, 1e-12)), axis=0)

            # Track approximate wealth from portfolio returns
            if daily_rets.shape[0] >= 20:
                recent_port = daily_rets[-20:] @ self._last_weights
                trailing_ret = np.sum(recent_port)
                # Approximate drawdown
                dd = trailing_ret if trailing_ret < 0 else 0
            else:
                dd = 0

            exposure = self._cut_factor if dd < self._dd_threshold else 1.0
            target = self._build(daily_rets, exposure)

            if np.sum(np.abs(target - self._last_weights)) < 0.002:
                return self._last_weights.copy()
            self._last_weights = target
            return target
        except Exception:
            return self._last_weights.copy()

    def _build(self, daily_rets, exposure_mult):
        sig = sector_sharpe_signal(daily_rets, self._sector_ids, 50)
        target = tilt_weights(_EW, sig, scale=30.0)
        if daily_rets.shape[0] >= 20:
            port_rets = daily_rets[-20:] @ target
            pvol = ewma_vol(port_rets, 20.0) * np.sqrt(252)
            if pvol > 1e-6:
                scale = min(0.13 / pvol, 1.0)
                target = target * scale

        # Apply drawdown cut
        target = target * exposure_mult
        return enforce_gross_limit(target, 1.0)


# ===========================================================================
# MAIN: Run all experiments
# ===========================================================================

if __name__ == "__main__":
    from submission import MyStrategy

    print("=" * 120)
    print("SYSTEMATIC STRATEGY EXPERIMENTS — 15 tests against competition evaluator")
    print(f"Baseline: Mean Sharpe 1.098, Min fold 0.962 (sc30, hl20, tv13, lb50, rb5)")
    print("=" * 120)

    results = []

    # BASELINE
    print("\n--- BASELINE ---")
    r = run_cv(MyStrategy, "BASELINE (current submission)")
    results.append(r)

    # EXPERIMENT 4: VWAP
    print("\n--- EXP 4: VWAP vs Closing Tick ---")
    for pm in ["close", "vwap", "median", "open", "mid"]:
        r = run_cv(VWAPStrategy, f"4: Price={pm}", price_method=pm)
        results.append(r)

    # EXPERIMENT 1: Intraday Features
    print("\n--- EXP 1: Intraday Features ---")
    for feat in ["intraday_vol", "open_close_mom", "close_to_open", "last_tick_momentum"]:
        for fw in [0.1, 0.2, 0.3]:
            r = run_cv(IntradayStrategy, f"1: {feat} w={fw}", intraday_feature=feat, feature_weight=fw)
            results.append(r)

    # EXPERIMENT 6: Multi-Lookback
    print("\n--- EXP 6: Multi-Lookback Ensemble ---")
    for lbs in [(30, 50), (30, 50, 80), (20, 40, 60, 80), (50, 80), (30, 50, 80, 120)]:
        for wm in ["equal", "inverse_var", "long_lookback_heavy"]:
            r = run_cv(MultiLookbackStrategy, f"6: lb={lbs} w={wm}", lookbacks=lbs, weighting=wm)
            results.append(r)

    # EXPERIMENT 7: Cost-Aware Rebalance
    print("\n--- EXP 7: Cost-Aware Rebalance ---")
    for ct in [0.0001, 0.0003, 0.0005, 0.001, 0.002]:
        r = run_cv(CostAwareStrategy, f"7: cost_thresh={ct}", cost_threshold=ct)
        results.append(r)

    # EXPERIMENT 2: Conditional Dispersion
    print("\n--- EXP 2: Conditional Dispersion x Momentum ---")
    for dm in ["scale", "binary", "aggressive"]:
        r = run_cv(DispersionStrategy, f"2: dispersion={dm}", dispersion_mode=dm)
        results.append(r)

    # EXPERIMENT 3: Adaptive Vol Target
    print("\n--- EXP 3: Adaptive Vol Target ---")
    for vlb in [40, 60, 80, 120]:
        r = run_cv(AdaptiveVolStrategy, f"3: adaptive_vt vlb={vlb}", vol_regime_lookback=vlb)
        results.append(r)

    # EXPERIMENT 8: Vol-of-Vol
    print("\n--- EXP 8: Vol-of-Vol Secondary Signal ---")
    for vw in [0.1, 0.2, 0.3, 0.4]:
        r = run_cv(VolOfVolStrategy, f"8: vov_weight={vw}", vov_weight=vw)
        results.append(r)

    # EXPERIMENT 5: EW Covariance Risk Overlay
    print("\n--- EXP 5: EW Covariance Risk Overlay ---")
    for rp in [0.1, 0.2, 0.3, 0.5]:
        for hl in [20, 30, 60]:
            r = run_cv(EWCovRiskStrategy, f"5: risk_pen={rp} hl={hl}", risk_penalty=rp, ew_halflife=hl)
            results.append(r)

    # EXPERIMENT 9: Mutual Information
    print("\n--- EXP 9: Mutual Information Sector Ranking ---")
    for mlb in [40, 60, 80]:
        r = run_cv(MutualInfoStrategy, f"9: mi_lookback={mlb}", mi_lookback=mlb)
        results.append(r)

    # EXPERIMENT 10: Regime Switching
    print("\n--- EXP 10: Regime Switching ---")
    for ct in [0.4, 0.5, 0.6, 0.7]:
        r = run_cv(RegimeSwitchStrategy, f"10: corr_thresh={ct}", corr_threshold=ct)
        results.append(r)

    # EXPERIMENT 11: Kelly Criterion
    print("\n--- EXP 11: Kelly Criterion ---")
    for kf in [0.25, 0.5, 0.75, 1.0]:
        r = run_cv(KellyStrategy, f"11: kelly_frac={kf}", kelly_fraction=kf)
        results.append(r)

    # EXPERIMENT 12: Drawdown Budget
    print("\n--- EXP 12: Drawdown Budget Overlay ---")
    for ddt in [-0.03, -0.05, -0.07, -0.10]:
        for cf in [0.3, 0.5, 0.7]:
            r = run_cv(DrawdownBudgetStrategy, f"12: dd={ddt} cut={cf}", dd_threshold=ddt, cut_factor=cf)
            results.append(r)

    # -----------------------------------------------------------------------
    # FINAL SUMMARY
    # -----------------------------------------------------------------------
    print("\n" + "=" * 120)
    print("FINAL RESULTS — sorted by Mean Sharpe")
    print("=" * 120)

    results.sort(key=lambda x: x["mean"], reverse=True)
    print(f"\n{'Strategy':55s}  {'Mean':>8s}  {'Min':>8s}  {'Delta':>8s}  {'F1':>8s}  {'F2':>8s}  {'F3':>8s}")
    print("-" * 110)
    for r in results:
        marker = ">>>" if r["delta"] > 0 else "   "
        print(f"{marker} {r['label']:55s}  {r['mean']:+8.4f}  {r['min']:+8.4f}  {r['delta']:+8.4f}  "
              f"{r['sharpes'][0]:+8.4f}  {r['sharpes'][1]:+8.4f}  {r['sharpes'][2]:+8.4f}")

    # Top improvements
    improvements = [r for r in results if r["delta"] > 0 and r["label"] != "BASELINE (current submission)"]
    if improvements:
        print(f"\n{'='*80}")
        print(f"IMPROVEMENTS OVER BASELINE ({len(improvements)} found)")
        print(f"{'='*80}")
        for r in improvements:
            print(f"  {r['label']:55s}  Δ={r['delta']:+.4f}  Mean={r['mean']:.4f}  Min={r['min']:.4f}")
    else:
        print("\n  No improvements over baseline found.")
