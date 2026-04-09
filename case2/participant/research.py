"""Research pipeline: test portfolio optimization approaches from 4 libraries
on the 25-asset competition universe, then identify what to re-implement in submission.py.

Libraries: riskfolio-lib, skfolio, cvxportfolio, simulated-bifurcation
Constraint: final submission can only use numpy/pandas/sklearn/scipy
"""

from __future__ import annotations
import warnings
import time
import traceback
import math

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Data Loading (competition format)
# ---------------------------------------------------------------------------

N_ASSETS = 25
TICKS_PER_DAY = 30
TRADING_DAYS_PER_YEAR = 252
ASSET_COLUMNS = [f"A{i:02d}" for i in range(N_ASSETS)]

def load_data():
    prices_raw = pd.read_csv("prices.csv", index_col="tick")
    prices_tick = prices_raw[ASSET_COLUMNS].to_numpy(dtype=float)
    meta = pd.read_csv("meta.csv")

    # Daily closing prices
    daily_close = prices_tick[TICKS_PER_DAY - 1 :: TICKS_PER_DAY]
    n_days = daily_close.shape[0]

    # Build daily returns DataFrame
    daily_prices_df = pd.DataFrame(daily_close, columns=ASSET_COLUMNS)
    daily_returns_df = daily_prices_df.pct_change().dropna()
    daily_prices_df = daily_prices_df.iloc[1:]  # align with returns

    print(f"Total days: {n_days}, Returns: {len(daily_returns_df)}")
    return prices_tick, daily_prices_df, daily_returns_df, meta

# ---------------------------------------------------------------------------
# Split: 60% train, 20% val, 20% test
# ---------------------------------------------------------------------------

def split_data(daily_returns_df, daily_prices_df):
    n = len(daily_returns_df)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)

    splits = {
        "train": (daily_returns_df.iloc[:train_end], daily_prices_df.iloc[:train_end]),
        "val": (daily_returns_df.iloc[train_end:val_end], daily_prices_df.iloc[train_end:val_end]),
        "test": (daily_returns_df.iloc[val_end:], daily_prices_df.iloc[val_end:]),
    }
    for k, (r, p) in splits.items():
        print(f"  {k}: {len(r)} days")
    return splits

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

DT_YEAR = 1.0 / (TRADING_DAYS_PER_YEAR * TICKS_PER_DAY)

def evaluate_weights_series(weights_list, returns_df, meta_df, label=""):
    """Evaluate a list of weight vectors against daily returns with costs."""
    spread = meta_df["spread_bps"].to_numpy(dtype=float) / 1e4
    borrow = meta_df["borrow_bps_annual"].to_numpy(dtype=float) / 1e4

    returns = returns_df.to_numpy(dtype=float)
    n_days = min(len(weights_list), len(returns))

    daily_rets = []
    prev_w = np.zeros(N_ASSETS)

    for t in range(n_days):
        w = np.asarray(weights_list[t], dtype=float)
        # Enforce gross limit
        gross = np.sum(np.abs(w))
        if gross > 1.0 + 1e-12:
            w = w / gross

        # Transaction costs
        delta = w - prev_w
        linear_cost = float(np.sum((spread / 2.0) * np.abs(delta)))
        quad_cost = float(np.sum(2.5 * spread * delta**2))

        # Borrow cost (30 ticks in a day)
        borrow_cost = float(np.sum(np.maximum(-w, 0.0) * borrow)) * (30 * DT_YEAR)

        # Portfolio return
        port_ret = float(np.sum(w * returns[t]))
        net_ret = port_ret - linear_cost - quad_cost - borrow_cost
        daily_rets.append(net_ret)
        prev_w = w.copy()

    daily_rets = np.array(daily_rets)

    # Metrics
    mu = np.mean(daily_rets)
    sd = np.std(daily_rets, ddof=1)
    sharpe = math.sqrt(252) * mu / sd if sd > 1e-12 else 0.0
    total_ret = float(np.prod(1 + daily_rets) - 1)
    cum = np.cumprod(1 + daily_rets)
    max_dd = float(np.min(cum / np.maximum.accumulate(cum) - 1))

    # Sortino
    downside = daily_rets[daily_rets < 0]
    down_std = np.std(downside, ddof=1) if len(downside) > 1 else 1e-12
    sortino = math.sqrt(252) * mu / down_std if down_std > 1e-12 else 0.0

    ann_ret = (1 + total_ret) ** (252 / max(len(daily_rets), 1)) - 1
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-12 else 0.0

    return {
        "label": label,
        "sharpe": round(sharpe, 4),
        "total_return": round(total_ret * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "ann_return": round(ann_ret * 100, 2),
        "ann_vol": round(sd * math.sqrt(252) * 100, 2),
        "n_days": n_days,
    }


def static_weights_eval(w, returns_df, meta_df, label=""):
    """Evaluate static (rebalanced daily) weights."""
    weights_list = [w] * len(returns_df)
    return evaluate_weights_series(weights_list, returns_df, meta_df, label)


# ---------------------------------------------------------------------------
# Walk-Forward Helper
# ---------------------------------------------------------------------------

def walk_forward_eval(fit_fn, daily_returns_df, daily_prices_df, meta_df,
                      train_size=252, test_size=60, label=""):
    """Walk-forward: fit on train_size days, hold for test_size days, slide."""
    returns = daily_returns_df.to_numpy(dtype=float)
    n = len(returns)

    all_weights = []
    all_returns_idx = []

    start = 0
    while start + train_size + test_size <= n:
        train_ret = daily_returns_df.iloc[start:start + train_size]
        train_px = daily_prices_df.iloc[start:start + train_size]

        try:
            w = fit_fn(train_ret, train_px, meta_df)
            w = np.asarray(w, dtype=float).flatten()
            if len(w) != N_ASSETS:
                w = np.ones(N_ASSETS) / N_ASSETS
            if not np.all(np.isfinite(w)):
                w = np.ones(N_ASSETS) / N_ASSETS
        except Exception as e:
            w = np.ones(N_ASSETS) / N_ASSETS

        for t in range(test_size):
            idx = start + train_size + t
            if idx < n:
                all_weights.append(w)
                all_returns_idx.append(idx)

        start += test_size

    if not all_weights:
        return {"label": label, "sharpe": 0, "total_return": 0, "max_dd": 0,
                "sortino": 0, "calmar": 0, "ann_return": 0, "ann_vol": 0, "n_days": 0}

    sub_returns = daily_returns_df.iloc[all_returns_idx]
    return evaluate_weights_series(all_weights, sub_returns, meta_df, label)


# ---------------------------------------------------------------------------
# BASELINES
# ---------------------------------------------------------------------------

def baseline_equal_weight():
    return np.ones(N_ASSETS) / N_ASSETS

def baseline_inverse_vol(train_ret_df, *args):
    vol = train_ret_df.std().to_numpy()
    vol = np.maximum(vol, 1e-10)
    w = 1.0 / vol
    return w / w.sum()

def baseline_min_variance(train_ret_df, *args):
    from sklearn.covariance import LedoitWolf
    cov = LedoitWolf().fit(train_ret_df.to_numpy()).covariance_
    from scipy.optimize import minimize
    n = cov.shape[0]
    x0 = np.ones(n) / n

    def obj(w):
        return w @ cov @ w

    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    return res.x if res.success else x0

# ---------------------------------------------------------------------------
# GROUP A: Riskfolio-Lib
# ---------------------------------------------------------------------------

def riskfolio_strategy(train_ret_df, train_px_df, meta_df, rm="CVaR", obj="MinRisk", method_cov="hist"):
    import riskfolio as rp
    port = rp.Portfolio(returns=train_ret_df)
    port.assets_stats(method_mu="hist", method_cov=method_cov)
    w = port.optimization(model="Classic", rm=rm, obj=obj, rf=0, hist=True)
    if w is None:
        return np.ones(N_ASSETS) / N_ASSETS
    return w.to_numpy().flatten()

def riskfolio_hrp(train_ret_df, train_px_df, meta_df, rm="CVaR"):
    import riskfolio as rp
    port = rp.HCPortfolio(returns=train_ret_df)
    w = port.optimization(model="HRP", rm=rm, rf=0)
    if w is None:
        return np.ones(N_ASSETS) / N_ASSETS
    return w.to_numpy().flatten()

# ---------------------------------------------------------------------------
# GROUP B: skfolio
# ---------------------------------------------------------------------------

def skfolio_mean_risk(train_ret_df, train_px_df, meta_df,
                      risk_measure="CVaR", objective="minimize_risk",
                      l2_coef=0.0):
    from skfolio import RiskMeasure
    from skfolio.optimization import MeanRisk, ObjectiveFunction

    rm_map = {
        "CVaR": RiskMeasure.CVAR,
        "CDaR": RiskMeasure.CDAR,
        "Variance": RiskMeasure.VARIANCE,
        "MAD": RiskMeasure.MAD,
    }
    obj_map = {
        "minimize_risk": ObjectiveFunction.MINIMIZE_RISK,
        "maximize_ratio": ObjectiveFunction.MAXIMIZE_RATIO,
    }

    model = MeanRisk(
        risk_measure=rm_map.get(risk_measure, RiskMeasure.CVAR),
        objective_function=obj_map.get(objective, ObjectiveFunction.MINIMIZE_RISK),
        l2_coef=l2_coef,
        min_weights=0.0,  # long-only
    )
    X = train_ret_df.to_numpy()
    model.fit(X)
    return model.weights_

def skfolio_hrp(train_ret_df, train_px_df, meta_df, risk_measure="CVaR"):
    from skfolio import RiskMeasure
    from skfolio.optimization import HierarchicalRiskParity

    rm_map = {
        "CVaR": RiskMeasure.CVAR,
        "CDaR": RiskMeasure.CDAR,
        "Variance": RiskMeasure.VARIANCE,
    }

    model = HierarchicalRiskParity(
        risk_measure=rm_map.get(risk_measure, RiskMeasure.CVAR),
    )
    X = train_ret_df.to_numpy()
    model.fit(X)
    return model.weights_

def skfolio_nco(train_ret_df, train_px_df, meta_df):
    from skfolio import RiskMeasure
    from skfolio.optimization import (
        MeanRisk, ObjectiveFunction,
        NestedClustersOptimization,
    )

    inner = MeanRisk(
        risk_measure=RiskMeasure.CVAR,
        objective_function=ObjectiveFunction.MINIMIZE_RISK,
    )
    outer = MeanRisk(
        risk_measure=RiskMeasure.CDAR,
        objective_function=ObjectiveFunction.MINIMIZE_RISK,
    )

    model = NestedClustersOptimization(
        inner_estimator=inner,
        outer_estimator=outer,
    )
    X = train_ret_df.to_numpy()
    model.fit(X)
    return model.weights_

def skfolio_risk_budgeting(train_ret_df, train_px_df, meta_df, risk_measure="CVaR"):
    from skfolio import RiskMeasure
    from skfolio.optimization import RiskBudgeting

    rm_map = {
        "CVaR": RiskMeasure.CVAR,
        "CDaR": RiskMeasure.CDAR,
        "Variance": RiskMeasure.VARIANCE,
    }

    model = RiskBudgeting(
        risk_measure=rm_map.get(risk_measure, RiskMeasure.CVAR),
        min_weights=0.0,
    )
    X = train_ret_df.to_numpy()
    model.fit(X)
    return model.weights_

# ---------------------------------------------------------------------------
# GROUP D: Simulated Bifurcation
# ---------------------------------------------------------------------------

def sb_markowitz(train_ret_df, train_px_df, meta_df, k=15):
    try:
        from simulated_bifurcation.models import Markowitz
        mu = train_ret_df.mean().to_numpy() * 252
        cov = train_ret_df.cov().to_numpy() * 252

        model = Markowitz(mu, cov, k=k)
        model.optimize(
            agents=20,
            best_only=True,
            ballistic=True,
        )
        w = model.get_best_vector()
        w = np.asarray(w, dtype=float)
        w = np.maximum(w, 0)
        total = w.sum()
        if total > 1e-10:
            w = w / total
        else:
            w = np.ones(N_ASSETS) / N_ASSETS
        return w
    except Exception as e:
        print(f"    SB failed: {e}")
        return np.ones(N_ASSETS) / N_ASSETS


# ---------------------------------------------------------------------------
# CURRENT STRATEGY (for comparison)
# ---------------------------------------------------------------------------

def current_sector_sharpe(train_ret_df, train_px_df, meta_df):
    """Replicate current submission logic."""
    sector_ids = meta_df["sector_id"].to_numpy(dtype=int)
    daily_rets = train_ret_df.to_numpy()
    n_assets = daily_rets.shape[1]
    lookback = 50

    if daily_rets.shape[0] < lookback:
        return np.ones(n_assets) / n_assets

    recent = daily_rets[-lookback:]
    log_rets = np.log(1 + recent)  # approximate
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = np.empty(len(unique_sectors))

    for i, s in enumerate(unique_sectors):
        sector_rets = log_rets[:, sector_ids == s].mean(axis=1)
        mu = sector_rets.mean()
        vol = sector_rets.std()
        sector_sharpes[i] = mu / max(vol, 1e-10)

    mu_s, sigma_s = sector_sharpes.mean(), sector_sharpes.std()
    if sigma_s < 1e-10:
        return np.ones(n_assets) / n_assets

    signal = np.zeros(n_assets)
    for i, s in enumerate(unique_sectors):
        signal[sector_ids == s] = (sector_sharpes[i] - mu_s) / sigma_s

    base = np.ones(n_assets) / n_assets
    clipped = np.clip(signal, -2.0, 2.0)
    tilted = base * (1.0 + 20.0 * clipped)
    tilted = np.maximum(tilted, 0.0)
    total = tilted.sum()
    if total < 1e-10:
        return base
    w = tilted / total

    # Vol targeting
    if daily_rets.shape[0] >= 20:
        port_rets = daily_rets[-20:] @ w
        # EWMA vol
        alpha = 1.0 - np.exp(-np.log(2.0) / 15.0)
        var = port_rets[0] ** 2
        for r in port_rets[1:]:
            var = alpha * r * r + (1 - alpha) * var
        port_vol = np.sqrt(var) * np.sqrt(252)
        if port_vol > 1e-6:
            scale = min(0.14 / port_vol, 1.0)
            w = w * scale

    gross = np.sum(np.abs(w))
    if gross > 1.0:
        w = w / gross
    return w


# ---------------------------------------------------------------------------
# ADVANCED: Sector-aware Min-CVaR with Ledoit-Wolf
# ---------------------------------------------------------------------------

def sector_aware_min_cvar(train_ret_df, train_px_df, meta_df, alpha=0.05):
    """Min-CVaR with sector exposure constraints."""
    from scipy.optimize import minimize

    returns = train_ret_df.to_numpy()
    n_days, n_assets = returns.shape
    sector_ids = meta_df["sector_id"].to_numpy(dtype=int)

    def cvar_objective(w):
        port_rets = returns @ w
        sorted_rets = np.sort(port_rets)
        cutoff = int(n_days * alpha)
        if cutoff < 1:
            cutoff = 1
        tail_mean = sorted_rets[:cutoff].mean()
        return -tail_mean  # minimize negative tail = minimize CVaR

    x0 = np.ones(n_assets) / n_assets
    bounds = [(0, 0.15)] * n_assets  # max 15% per asset

    # Constraints: sum = 1, sector <= 0.35
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    unique_sectors = np.unique(sector_ids)
    for s in unique_sectors:
        mask = sector_ids == s
        cons.append({"type": "ineq", "fun": lambda w, m=mask: 0.35 - np.sum(w[m])})

    res = minimize(cvar_objective, x0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-10})
    return res.x if res.success else x0


# ---------------------------------------------------------------------------
# ADVANCED: Sector Momentum + Min-Variance Blend
# ---------------------------------------------------------------------------

def sector_mom_minvar_blend(train_ret_df, train_px_df, meta_df, mom_weight=0.5):
    """Blend sector momentum signal with minimum variance weights."""
    from sklearn.covariance import LedoitWolf

    # Min-variance component
    cov = LedoitWolf().fit(train_ret_df.to_numpy()).covariance_
    from scipy.optimize import minimize
    n = cov.shape[0]
    x0 = np.ones(n) / n
    def obj(w): return w @ cov @ w
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    w_mv = res.x if res.success else x0

    # Sector momentum component
    w_mom = current_sector_sharpe(train_ret_df, train_px_df, meta_df)

    # Blend
    w = mom_weight * w_mom + (1 - mom_weight) * w_mv
    w = np.maximum(w, 0)
    w = w / w.sum()
    return w


# ---------------------------------------------------------------------------
# ADVANCED: Risk Parity (equal risk contribution) via scipy
# ---------------------------------------------------------------------------

def risk_parity_scipy(train_ret_df, train_px_df, meta_df):
    """Risk parity using Ledoit-Wolf covariance."""
    from sklearn.covariance import LedoitWolf
    from scipy.optimize import minimize

    cov = LedoitWolf().fit(train_ret_df.to_numpy()).covariance_
    n = cov.shape[0]

    def risk_contrib_obj(w):
        port_vol = np.sqrt(w @ cov @ w)
        marginal = cov @ w
        rc = w * marginal / port_vol
        target_rc = port_vol / n
        return np.sum((rc - target_rc) ** 2)

    x0 = np.ones(n) / n
    bounds = [(0.001, 1)] * n
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    res = minimize(risk_contrib_obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    return res.x if res.success else x0


# ---------------------------------------------------------------------------
# ADVANCED: Max Diversification
# ---------------------------------------------------------------------------

def max_diversification(train_ret_df, train_px_df, meta_df):
    """Maximize diversification ratio: sum(w*sigma) / port_vol."""
    from sklearn.covariance import LedoitWolf
    from scipy.optimize import minimize

    cov = LedoitWolf().fit(train_ret_df.to_numpy()).covariance_
    vols = np.sqrt(np.diag(cov))
    n = cov.shape[0]

    def neg_div_ratio(w):
        port_vol = np.sqrt(w @ cov @ w)
        weighted_vol = w @ vols
        return -weighted_vol / max(port_vol, 1e-12)

    x0 = np.ones(n) / n
    bounds = [(0, 1)] * n
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    res = minimize(neg_div_ratio, x0, method="SLSQP", bounds=bounds, constraints=cons)
    return res.x if res.success else x0


# ---------------------------------------------------------------------------
# ADVANCED: Mean-CVaR with Sector Momentum Expected Returns
# ---------------------------------------------------------------------------

def mean_cvar_sector_signal(train_ret_df, train_px_df, meta_df, risk_aversion=1.0):
    """Mean-CVaR optimization using sector Sharpe as expected return signal."""
    from scipy.optimize import minimize

    returns = train_ret_df.to_numpy()
    n_days, n_assets = returns.shape
    sector_ids = meta_df["sector_id"].to_numpy(dtype=int)

    # Compute sector sharpe signal as expected return proxy
    lookback = min(50, n_days)
    recent = returns[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = np.empty(len(unique_sectors))
    for i, s in enumerate(unique_sectors):
        sr = recent[:, sector_ids == s].mean(axis=1)
        sector_sharpes[i] = sr.mean() / max(sr.std(), 1e-10)

    mu_s, sig_s = sector_sharpes.mean(), sector_sharpes.std()
    expected_ret = np.zeros(n_assets)
    if sig_s > 1e-10:
        for i, s in enumerate(unique_sectors):
            expected_ret[sector_ids == s] = (sector_sharpes[i] - mu_s) / sig_s

    # Scale to daily return magnitude
    expected_ret = expected_ret * returns.std(axis=0).mean()

    def objective(w):
        port_rets = returns @ w
        mean_ret = np.mean(port_rets) + w @ expected_ret * 0.1  # blend historical + signal
        sorted_rets = np.sort(port_rets)
        cutoff = max(int(n_days * 0.05), 1)
        cvar = -sorted_rets[:cutoff].mean()
        return -(mean_ret - risk_aversion * cvar)

    x0 = np.ones(n_assets) / n_assets
    bounds = [(0, 0.15)] * n_assets
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    res = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500})
    return res.x if res.success else x0


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

def main():
    print("=" * 70)
    print("PORTFOLIO OPTIMIZATION RESEARCH PIPELINE")
    print("25-asset, 5-sector competition universe")
    print("=" * 70)

    prices_tick, daily_prices_df, daily_returns_df, meta_df = load_data()
    splits = split_data(daily_returns_df, daily_prices_df)

    train_ret, train_px = splits["train"]
    val_ret, val_px = splits["val"]
    test_ret, test_px = splits["test"]

    results = []

    def run_strategy(name, fit_fn, static=False):
        """Run a strategy with walk-forward + validation + test evaluation."""
        print(f"\n--- {name} ---")
        t0 = time.time()

        try:
            if static:
                w = fit_fn
                wf = static_weights_eval(w, train_ret, meta_df, f"{name} [train]")
                val = static_weights_eval(w, val_ret, meta_df, f"{name} [val]")
                test = static_weights_eval(w, test_ret, meta_df, f"{name} [test]")
            else:
                # Walk-forward on train+val combined
                combined_ret = pd.concat([train_ret, val_ret])
                combined_px = pd.concat([train_px, val_px])
                wf = walk_forward_eval(fit_fn, combined_ret, combined_px, meta_df,
                                       train_size=252, test_size=60, label=f"{name} [WF]")

                # Fit on full train, evaluate on val
                w_val = fit_fn(train_ret, train_px, meta_df)
                val = static_weights_eval(w_val, val_ret, meta_df, f"{name} [val]")

                # Fit on train+val, evaluate on test
                w_test = fit_fn(combined_ret, combined_px, meta_df)
                test = static_weights_eval(w_test, test_ret, meta_df, f"{name} [test]")

            elapsed = time.time() - t0

            row = {
                "strategy": name,
                "wf_sharpe": wf["sharpe"],
                "wf_maxdd": wf["max_dd"],
                "val_sharpe": val["sharpe"],
                "val_maxdd": val["max_dd"],
                "test_sharpe": test["sharpe"],
                "test_maxdd": test["max_dd"],
                "test_sortino": test["sortino"],
                "test_calmar": test["calmar"],
                "runtime_s": round(elapsed, 1),
            }
            results.append(row)
            print(f"  WF Sharpe={wf['sharpe']:.3f}  Val Sharpe={val['sharpe']:.3f}  "
                  f"Test Sharpe={test['sharpe']:.3f}  MaxDD={test['max_dd']:.1f}%  "
                  f"({elapsed:.1f}s)")

        except Exception as e:
            elapsed = time.time() - t0
            print(f"  FAILED ({elapsed:.1f}s): {e}")
            traceback.print_exc()
            results.append({
                "strategy": name, "wf_sharpe": None, "val_sharpe": None,
                "test_sharpe": None, "test_maxdd": None, "runtime_s": round(elapsed, 1),
                "wf_maxdd": None, "val_maxdd": None, "test_sortino": None, "test_calmar": None,
            })

    # -----------------------------------------------------------------------
    # BASELINES
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 1: BASELINES")
    print("=" * 70)

    run_strategy("B1: Equal Weight", baseline_equal_weight(), static=True)
    run_strategy("B2: Inverse Vol", baseline_inverse_vol)
    run_strategy("B3: Min Variance (LW)", baseline_min_variance)
    run_strategy("B4: Current Sector Sharpe", current_sector_sharpe)

    # -----------------------------------------------------------------------
    # GROUP A: Riskfolio-Lib
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 2: RISKFOLIO-LIB")
    print("=" * 70)

    for rm in ["CVaR", "CDaR", "MAD", "MSV"]:
        for cov_method in ["hist", "ledoit"]:
            name = f"A: RF {rm} MinRisk ({cov_method})"
            run_strategy(name,
                         lambda r, p, m, _rm=rm, _cm=cov_method: riskfolio_strategy(r, p, m, rm=_rm, method_cov=_cm))

    run_strategy("A: RF HRP-CVaR", lambda r, p, m: riskfolio_hrp(r, p, m, rm="CVaR"))
    run_strategy("A: RF HRP-CDaR", lambda r, p, m: riskfolio_hrp(r, p, m, rm="CDaR"))

    # -----------------------------------------------------------------------
    # GROUP B: skfolio
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 3: SKFOLIO")
    print("=" * 70)

    for rm in ["CVaR", "CDaR", "Variance"]:
        for obj in ["minimize_risk", "maximize_ratio"]:
            name = f"B: SK {rm} {obj[:3]}"
            run_strategy(name,
                         lambda r, p, m, _rm=rm, _obj=obj: skfolio_mean_risk(r, p, m, risk_measure=_rm, objective=_obj))

    for rm in ["CVaR", "CDaR", "Variance"]:
        name = f"B: SK HRP-{rm}"
        run_strategy(name,
                     lambda r, p, m, _rm=rm: skfolio_hrp(r, p, m, risk_measure=_rm))

    run_strategy("B: SK NCO (CVaR+CDaR)", skfolio_nco)

    for rm in ["CVaR", "CDaR"]:
        name = f"B: SK RiskBudget-{rm}"
        run_strategy(name,
                     lambda r, p, m, _rm=rm: skfolio_risk_budgeting(r, p, m, risk_measure=_rm))

    # L2 sweep
    for l2 in [0.001, 0.01, 0.05, 0.1]:
        name = f"B: SK CVaR MaxRatio L2={l2}"
        run_strategy(name,
                     lambda r, p, m, _l2=l2: skfolio_mean_risk(r, p, m, risk_measure="CVaR",
                                                                objective="maximize_ratio", l2_coef=_l2))

    # -----------------------------------------------------------------------
    # GROUP D: Simulated Bifurcation
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 4: SIMULATED BIFURCATION")
    print("=" * 70)

    for k in [10, 15, 20]:
        name = f"D: SB Markowitz K={k}"
        run_strategy(name,
                     lambda r, p, m, _k=k: sb_markowitz(r, p, m, k=_k))

    # -----------------------------------------------------------------------
    # CUSTOM STRATEGIES (scipy-implementable)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 5: CUSTOM (scipy-implementable)")
    print("=" * 70)

    run_strategy("C: Risk Parity (LW)", risk_parity_scipy)
    run_strategy("C: Max Diversification", max_diversification)
    run_strategy("C: Sector Min-CVaR", sector_aware_min_cvar)
    run_strategy("C: Mean-CVaR SectorSignal", mean_cvar_sector_signal)

    for blend in [0.3, 0.5, 0.7]:
        name = f"C: SectorMom+MinVar blend={blend}"
        run_strategy(name,
                     lambda r, p, m, _b=blend: sector_mom_minvar_blend(r, p, m, mom_weight=_b))

    # -----------------------------------------------------------------------
    # RESULTS SUMMARY
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    df = pd.DataFrame(results)
    df = df.sort_values("test_sharpe", ascending=False, na_position="last")

    print("\n" + df.to_string(index=False))

    df.to_csv("research_results.csv", index=False)
    print("\nSaved to research_results.csv")

    # Top strategies
    valid = df.dropna(subset=["test_sharpe"])
    if len(valid) > 0:
        print("\n" + "=" * 70)
        print("TOP 5 BY TEST SHARPE")
        print("=" * 70)
        top5 = valid.head(5)
        for _, row in top5.iterrows():
            print(f"  {row['strategy']:40s}  Sharpe={row['test_sharpe']:.3f}  "
                  f"MaxDD={row['test_maxdd']:.1f}%  Sortino={row['test_sortino']:.3f}  "
                  f"WF={row['wf_sharpe']:.3f}")

    print("\n" + "=" * 70)
    print("TOP 5 BY WALK-FORWARD SHARPE (most reliable)")
    print("=" * 70)
    valid_wf = valid.sort_values("wf_sharpe", ascending=False)
    for _, row in valid_wf.head(5).iterrows():
        print(f"  {row['strategy']:40s}  WF={row['wf_sharpe']:.3f}  "
              f"Val={row['val_sharpe']:.3f}  Test={row['test_sharpe']:.3f}  "
              f"MaxDD={row['test_maxdd']:.1f}%")


if __name__ == "__main__":
    main()
