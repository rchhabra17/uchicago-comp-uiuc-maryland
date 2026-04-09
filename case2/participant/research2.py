"""Phase 2 research: deeper exploration of promising strategies.

Key finding from Phase 1: SectorMom+MinVar blend is the most promising.
Also: fix skfolio MeanRisk, test more blend ratios, and evaluate through validate.py.
"""

from __future__ import annotations
import warnings
import time
import math
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

N_ASSETS = 25
TICKS_PER_DAY = 30
TRADING_DAYS_PER_YEAR = 252
ASSET_COLUMNS = [f"A{i:02d}" for i in range(N_ASSETS)]
DT_YEAR = 1.0 / (TRADING_DAYS_PER_YEAR * TICKS_PER_DAY)


def load_data():
    prices_raw = pd.read_csv("prices.csv", index_col="tick")
    prices_tick = prices_raw[ASSET_COLUMNS].to_numpy(dtype=float)
    meta = pd.read_csv("meta.csv")
    daily_close = prices_tick[TICKS_PER_DAY - 1 :: TICKS_PER_DAY]
    daily_prices_df = pd.DataFrame(daily_close, columns=ASSET_COLUMNS)
    daily_returns_df = daily_prices_df.pct_change().dropna()
    daily_prices_df = daily_prices_df.iloc[1:]
    return prices_tick, daily_prices_df, daily_returns_df, meta


def evaluate_weights_series(weights_list, returns_df, meta_df, label=""):
    spread = meta_df["spread_bps"].to_numpy(dtype=float) / 1e4
    borrow = meta_df["borrow_bps_annual"].to_numpy(dtype=float) / 1e4
    returns = returns_df.to_numpy(dtype=float)
    n_days = min(len(weights_list), len(returns))
    daily_rets = []
    prev_w = np.zeros(N_ASSETS)
    for t in range(n_days):
        w = np.asarray(weights_list[t], dtype=float)
        gross = np.sum(np.abs(w))
        if gross > 1.0 + 1e-12:
            w = w / gross
        delta = w - prev_w
        linear_cost = float(np.sum((spread / 2.0) * np.abs(delta)))
        quad_cost = float(np.sum(2.5 * spread * delta**2))
        borrow_cost = float(np.sum(np.maximum(-w, 0.0) * borrow)) * (30 * DT_YEAR)
        port_ret = float(np.sum(w * returns[t]))
        net_ret = port_ret - linear_cost - quad_cost - borrow_cost
        daily_rets.append(net_ret)
        prev_w = w.copy()
    daily_rets = np.array(daily_rets)
    mu = np.mean(daily_rets)
    sd = np.std(daily_rets, ddof=1)
    sharpe = math.sqrt(252) * mu / sd if sd > 1e-12 else 0.0
    total_ret = float(np.prod(1 + daily_rets) - 1)
    cum = np.cumprod(1 + daily_rets)
    max_dd = float(np.min(cum / np.maximum.accumulate(cum) - 1))
    downside = daily_rets[daily_rets < 0]
    down_std = np.std(downside, ddof=1) if len(downside) > 1 else 1e-12
    sortino = math.sqrt(252) * mu / down_std if down_std > 1e-12 else 0.0
    ann_ret = (1 + total_ret) ** (252 / max(len(daily_rets), 1)) - 1
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-12 else 0.0
    return {
        "label": label, "sharpe": round(sharpe, 4), "total_return": round(total_ret * 100, 2),
        "max_dd": round(max_dd * 100, 2), "sortino": round(sortino, 4), "calmar": round(calmar, 4),
        "ann_return": round(ann_ret * 100, 2), "ann_vol": round(sd * math.sqrt(252) * 100, 2),
    }


def walk_forward_eval(fit_fn, daily_returns_df, daily_prices_df, meta_df,
                      train_size=252, test_size=60, label=""):
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
            if len(w) != N_ASSETS or not np.all(np.isfinite(w)):
                w = np.ones(N_ASSETS) / N_ASSETS
        except Exception:
            w = np.ones(N_ASSETS) / N_ASSETS
        for t in range(test_size):
            idx = start + train_size + t
            if idx < n:
                all_weights.append(w)
                all_returns_idx.append(idx)
        start += test_size
    if not all_weights:
        return {"label": label, "sharpe": 0, "max_dd": 0}
    sub_returns = daily_returns_df.iloc[all_returns_idx]
    return evaluate_weights_series(all_weights, sub_returns, meta_df, label)


# ---------------------------------------------------------------------------
# Strategy components
# ---------------------------------------------------------------------------

def get_sector_sharpe_signal(daily_rets, sector_ids, lookback=50):
    n_assets = daily_rets.shape[1]
    if daily_rets.shape[0] < lookback:
        return np.zeros(n_assets)
    recent = daily_rets[-lookback:]
    unique_sectors = np.unique(sector_ids)
    sector_sharpes = np.empty(len(unique_sectors))
    for i, s in enumerate(unique_sectors):
        sr = recent[:, sector_ids == s].mean(axis=1)
        sector_sharpes[i] = sr.mean() / max(sr.std(), 1e-10)
    mu_s, sig_s = sector_sharpes.mean(), sector_sharpes.std()
    if sig_s < 1e-10:
        return np.zeros(n_assets)
    signal = np.zeros(n_assets)
    for i, s in enumerate(unique_sectors):
        signal[sector_ids == s] = (sector_sharpes[i] - mu_s) / sig_s
    return signal


def get_min_variance_weights(returns, shrinkage="ledoit"):
    from sklearn.covariance import LedoitWolf, OAS
    from scipy.optimize import minimize
    n = returns.shape[1]
    if shrinkage == "ledoit":
        cov = LedoitWolf().fit(returns).covariance_
    elif shrinkage == "oas":
        cov = OAS().fit(returns).covariance_
    else:
        cov = np.cov(returns, rowvar=False)
    x0 = np.ones(n) / n
    def obj(w): return w @ cov @ w
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    return res.x if res.success else x0, cov


def get_risk_parity_weights(cov):
    from scipy.optimize import minimize
    n = cov.shape[0]
    def risk_contrib_obj(w):
        port_vol = np.sqrt(w @ cov @ w)
        marginal = cov @ w
        rc = w * marginal / max(port_vol, 1e-12)
        target_rc = port_vol / n
        return np.sum((rc - target_rc) ** 2)
    x0 = np.ones(n) / n
    bounds = [(0.001, 1)] * n
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    res = minimize(risk_contrib_obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    return res.x if res.success else x0


def sector_mom_tilt(signal, scale=20.0):
    base = np.ones(N_ASSETS) / N_ASSETS
    clipped = np.clip(signal, -2.0, 2.0)
    tilted = base * (1.0 + scale * clipped)
    tilted = np.maximum(tilted, 0.0)
    total = tilted.sum()
    if total < 1e-10:
        return base
    return tilted / total


def vol_target(weights, daily_rets, target_vol=0.14, lookback=20, half_life=15.0):
    if daily_rets.shape[0] < lookback:
        return weights
    port_rets = daily_rets[-lookback:] @ weights
    alpha = 1.0 - np.exp(-np.log(2.0) / half_life)
    var = port_rets[0] ** 2
    for r in port_rets[1:]:
        var = alpha * r * r + (1 - alpha) * var
    port_vol = np.sqrt(var) * np.sqrt(252)
    if port_vol > 1e-6:
        scale = min(target_vol / port_vol, 1.0)
        weights = weights * scale
    return weights


def enforce_gross(w, budget=1.0):
    gross = np.sum(np.abs(w))
    if gross > budget + 1e-12:
        w = w * (budget / gross)
    return w


# ---------------------------------------------------------------------------
# Strategy factories
# ---------------------------------------------------------------------------

def make_sector_mom_minvar_blend(mom_weight=0.5, vol_tgt=0.14, lookback=50, scale=20.0):
    def fit_fn(train_ret_df, train_px_df, meta_df):
        returns = train_ret_df.to_numpy()
        sector_ids = meta_df["sector_id"].to_numpy(dtype=int)
        signal = get_sector_sharpe_signal(returns, sector_ids, lookback)
        w_mom = sector_mom_tilt(signal, scale)
        w_mv, _ = get_min_variance_weights(returns)
        w = mom_weight * w_mom + (1 - mom_weight) * w_mv
        w = np.maximum(w, 0)
        w = w / w.sum()
        w = vol_target(w, returns, vol_tgt)
        return enforce_gross(w)
    return fit_fn


def make_sector_mom_riskparity_blend(mom_weight=0.5, vol_tgt=0.14, lookback=50, scale=20.0):
    def fit_fn(train_ret_df, train_px_df, meta_df):
        returns = train_ret_df.to_numpy()
        sector_ids = meta_df["sector_id"].to_numpy(dtype=int)
        signal = get_sector_sharpe_signal(returns, sector_ids, lookback)
        w_mom = sector_mom_tilt(signal, scale)
        _, cov = get_min_variance_weights(returns)
        w_rp = get_risk_parity_weights(cov)
        w = mom_weight * w_mom + (1 - mom_weight) * w_rp
        w = np.maximum(w, 0)
        w = w / w.sum()
        w = vol_target(w, returns, vol_tgt)
        return enforce_gross(w)
    return fit_fn


def make_sector_mom_pure(vol_tgt=0.14, lookback=50, scale=20.0):
    """Current strategy baseline."""
    def fit_fn(train_ret_df, train_px_df, meta_df):
        returns = train_ret_df.to_numpy()
        sector_ids = meta_df["sector_id"].to_numpy(dtype=int)
        signal = get_sector_sharpe_signal(returns, sector_ids, lookback)
        w = sector_mom_tilt(signal, scale)
        w = vol_target(w, returns, vol_tgt)
        return enforce_gross(w)
    return fit_fn


def make_min_cvar_with_signal(risk_aversion=0.5, signal_weight=0.1, lookback=50):
    """Min-CVaR with sector momentum as expected returns."""
    def fit_fn(train_ret_df, train_px_df, meta_df):
        from scipy.optimize import minimize
        returns = train_ret_df.to_numpy()
        n_days, n_assets = returns.shape
        sector_ids = meta_df["sector_id"].to_numpy(dtype=int)

        signal = get_sector_sharpe_signal(returns, sector_ids, lookback)
        expected_ret = signal * returns.std(axis=0).mean()

        def objective(w):
            port_rets = returns @ w
            mean_ret = np.mean(port_rets) + w @ expected_ret * signal_weight
            sorted_rets = np.sort(port_rets)
            cutoff = max(int(n_days * 0.05), 1)
            cvar = -sorted_rets[:cutoff].mean()
            return -(mean_ret - risk_aversion * cvar)

        x0 = np.ones(n_assets) / n_assets
        bounds = [(0, 0.15)] * n_assets
        cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        res = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 500})
        w = res.x if res.success else x0
        w = vol_target(w, returns, 0.14)
        return enforce_gross(w)
    return fit_fn


def make_sector_block_cov_rp(lookback=50, scale=20.0, vol_tgt=0.14, mom_weight=0.5):
    """Sector-block shrinkage covariance + risk parity + sector momentum blend."""
    def fit_fn(train_ret_df, train_px_df, meta_df):
        returns = train_ret_df.to_numpy()
        n_days, n_assets = returns.shape
        sector_ids = meta_df["sector_id"].to_numpy(dtype=int)

        # Sector-block structured covariance target
        sample_cov = np.cov(returns, rowvar=False)
        vols = np.sqrt(np.diag(sample_cov))
        corr = sample_cov / np.outer(vols, vols)

        unique_sectors = np.unique(sector_ids)
        target_corr = np.zeros_like(corr)
        for s in unique_sectors:
            mask = sector_ids == s
            intra = corr[np.ix_(mask, mask)]
            avg_intra = (intra.sum() - np.trace(intra)) / max(mask.sum() * (mask.sum() - 1), 1)
            target_corr[np.ix_(mask, mask)] = avg_intra
        # Inter-sector average
        inter_vals = []
        for i in range(len(unique_sectors)):
            for j in range(i + 1, len(unique_sectors)):
                m1 = sector_ids == unique_sectors[i]
                m2 = sector_ids == unique_sectors[j]
                inter_vals.append(corr[np.ix_(m1, m2)].mean())
        avg_inter = np.mean(inter_vals) if inter_vals else 0.0
        for i in range(n_assets):
            for j in range(n_assets):
                if sector_ids[i] != sector_ids[j]:
                    target_corr[i, j] = avg_inter
        np.fill_diagonal(target_corr, 1.0)
        target_cov = target_corr * np.outer(vols, vols)

        # Shrink toward sector-block target
        shrinkage = 0.5
        cov = (1 - shrinkage) * sample_cov + shrinkage * target_cov

        # Risk parity on sector-block cov
        w_rp = get_risk_parity_weights(cov)

        # Sector momentum tilt
        signal = get_sector_sharpe_signal(returns, sector_ids, lookback)
        w_mom = sector_mom_tilt(signal, scale)

        # Blend
        w = mom_weight * w_mom + (1 - mom_weight) * w_rp
        w = np.maximum(w, 0)
        w = w / w.sum()
        w = vol_target(w, returns, vol_tgt)
        return enforce_gross(w)
    return fit_fn


# ---------------------------------------------------------------------------
# Fix skfolio MeanRisk
# ---------------------------------------------------------------------------

def skfolio_meanrisk(train_ret_df, train_px_df, meta_df, risk_measure="CVaR",
                     objective="minimize_risk", l2_coef=0.0):
    from skfolio import RiskMeasure
    from skfolio.optimization import MeanRisk, ObjectiveFunction

    rm_map = {
        "CVaR": RiskMeasure.CVAR,
        "CDaR": RiskMeasure.CDAR,
        "Variance": RiskMeasure.VARIANCE,
    }
    obj_map = {
        "minimize_risk": ObjectiveFunction.MINIMIZE_RISK,
        "maximize_ratio": ObjectiveFunction.MAXIMIZE_RATIO,
    }
    model = MeanRisk(
        risk_measure=rm_map[risk_measure],
        objective_function=obj_map[objective],
        l2_coef=l2_coef,
        min_weights=0.0,
    )
    X = train_ret_df.to_numpy()
    model.fit(X)
    return model.weights_


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 70)
    print("PHASE 2 RESEARCH: DEEPER STRATEGY EXPLORATION")
    print("=" * 70)

    prices_tick, daily_prices_df, daily_returns_df, meta_df = load_data()
    n = len(daily_returns_df)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)

    train_ret = daily_returns_df.iloc[:train_end]
    train_px = daily_prices_df.iloc[:train_end]
    val_ret = daily_returns_df.iloc[train_end:val_end]
    val_px = daily_prices_df.iloc[train_end:val_end]
    test_ret = daily_returns_df.iloc[val_end:]
    test_px = daily_prices_df.iloc[val_end:]
    combined_ret = pd.concat([train_ret, val_ret])
    combined_px = pd.concat([train_px, val_px])

    print(f"Train: {len(train_ret)}, Val: {len(val_ret)}, Test: {len(test_ret)}")

    results = []

    def run(name, fit_fn):
        print(f"\n--- {name} ---")
        t0 = time.time()
        try:
            wf = walk_forward_eval(fit_fn, combined_ret, combined_px, meta_df,
                                   train_size=252, test_size=60, label=name)
            w_val = fit_fn(train_ret, train_px, meta_df)
            val = evaluate_weights_series([w_val] * len(val_ret), val_ret, meta_df, name)
            w_test = fit_fn(combined_ret, combined_px, meta_df)
            test = evaluate_weights_series([w_test] * len(test_ret), test_ret, meta_df, name)
            elapsed = time.time() - t0
            row = {
                "strategy": name,
                "wf_sharpe": wf["sharpe"], "wf_maxdd": wf["max_dd"],
                "val_sharpe": val["sharpe"], "val_maxdd": val["max_dd"],
                "test_sharpe": test["sharpe"], "test_maxdd": test["max_dd"],
                "test_sortino": test["sortino"], "test_calmar": test["calmar"],
                "runtime": round(elapsed, 1),
            }
            results.append(row)
            print(f"  WF={wf['sharpe']:.3f}  Val={val['sharpe']:.3f}  "
                  f"Test={test['sharpe']:.3f}  MaxDD={test['max_dd']:.1f}%  ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  FAILED ({elapsed:.1f}s): {e}")
            import traceback; traceback.print_exc()

    # -----------------------------------------------------------------------
    # 1. SectorMom+MinVar blend sweep (finest granularity)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SECTOR MOM + MIN-VARIANCE BLEND SWEEP")
    print("=" * 70)

    for blend in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        for vol_tgt in [0.12, 0.14, 0.16, 0.20]:
            name = f"Mom+MV b={blend} tv={vol_tgt}"
            run(name, make_sector_mom_minvar_blend(blend, vol_tgt))

    # -----------------------------------------------------------------------
    # 2. SectorMom+RiskParity blend sweep
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SECTOR MOM + RISK PARITY BLEND SWEEP")
    print("=" * 70)

    for blend in [0.3, 0.5, 0.7, 1.0]:
        for vol_tgt in [0.12, 0.14, 0.16]:
            name = f"Mom+RP b={blend} tv={vol_tgt}"
            run(name, make_sector_mom_riskparity_blend(blend, vol_tgt))

    # -----------------------------------------------------------------------
    # 3. Sector-block covariance + RP + momentum
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SECTOR-BLOCK COV + RISK PARITY + MOMENTUM")
    print("=" * 70)

    for blend in [0.3, 0.5, 0.7]:
        for vol_tgt in [0.14, 0.16]:
            name = f"SectorCov+RP+Mom b={blend} tv={vol_tgt}"
            run(name, make_sector_block_cov_rp(mom_weight=blend, vol_tgt=vol_tgt))

    # -----------------------------------------------------------------------
    # 4. Lookback sensitivity
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("LOOKBACK SENSITIVITY (blend=0.7, tv=0.14)")
    print("=" * 70)

    for lb in [30, 40, 50, 60, 80]:
        name = f"Mom+MV b=0.7 lb={lb}"
        run(name, make_sector_mom_minvar_blend(0.7, 0.14, lb))

    # -----------------------------------------------------------------------
    # 5. Signal scale sensitivity
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SIGNAL SCALE SENSITIVITY (blend=0.7, tv=0.14)")
    print("=" * 70)

    for sc in [10, 15, 20, 25, 30]:
        name = f"Mom+MV b=0.7 sc={sc}"
        run(name, make_sector_mom_minvar_blend(0.7, 0.14, 50, sc))

    # -----------------------------------------------------------------------
    # 6. skfolio MeanRisk (fixed)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SKFOLIO MEANRISK (fixed)")
    print("=" * 70)

    for rm in ["CVaR", "CDaR", "Variance"]:
        for obj in ["minimize_risk", "maximize_ratio"]:
            name = f"SK {rm} {obj[:3]}"
            run(name, lambda r, p, m, _rm=rm, _obj=obj: skfolio_meanrisk(r, p, m, _rm, _obj))

    for l2 in [0.001, 0.01, 0.05, 0.1]:
        name = f"SK CVaR MaxRatio L2={l2}"
        run(name, lambda r, p, m, _l2=l2: skfolio_meanrisk(r, p, m, "CVaR", "maximize_ratio", _l2))

    # -----------------------------------------------------------------------
    # 7. Min-CVaR with signal
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("MIN-CVAR WITH SECTOR SIGNAL")
    print("=" * 70)

    for ra in [0.3, 0.5, 1.0, 2.0]:
        for sw in [0.05, 0.1, 0.2]:
            name = f"MinCVaR ra={ra} sw={sw}"
            run(name, make_min_cvar_with_signal(ra, sw))

    # -----------------------------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    df = pd.DataFrame(results)
    df = df.sort_values("test_sharpe", ascending=False, na_position="last")
    print("\n" + df.to_string(index=False))

    print("\n" + "=" * 70)
    print("TOP 10 BY TEST SHARPE")
    print("=" * 70)
    for _, row in df.head(10).iterrows():
        print(f"  {row['strategy']:40s}  Test={row['test_sharpe']:.3f}  "
              f"WF={row['wf_sharpe']:.3f}  Val={row['val_sharpe']:.3f}  "
              f"MaxDD={row['test_maxdd']:.1f}%  Sortino={row['test_sortino']:.3f}")

    # Save best parameters for the top strategy
    print("\n" + "=" * 70)
    print("TOP 10 BY WF SHARPE (most reliable)")
    print("=" * 70)
    df_wf = df.sort_values("wf_sharpe", ascending=False)
    for _, row in df_wf.head(10).iterrows():
        print(f"  {row['strategy']:40s}  WF={row['wf_sharpe']:.3f}  "
              f"Val={row['val_sharpe']:.3f}  Test={row['test_sharpe']:.3f}  "
              f"MaxDD={row['test_maxdd']:.1f}%")

    # Consistency check: strategies that are good on BOTH WF and test
    print("\n" + "=" * 70)
    print("BEST RISK-ADJUSTED (WF >= 0.6 AND test >= 1.3)")
    print("=" * 70)
    good = df[(df["wf_sharpe"] >= 0.6) & (df["test_sharpe"] >= 1.3)]
    good = good.sort_values("test_sharpe", ascending=False)
    for _, row in good.iterrows():
        print(f"  {row['strategy']:40s}  WF={row['wf_sharpe']:.3f}  "
              f"Test={row['test_sharpe']:.3f}  MaxDD={row['test_maxdd']:.1f}%  "
              f"Sortino={row['test_sortino']:.3f}")

    df.to_csv("research2_results.csv", index=False)
    print("\nSaved to research2_results.csv")


if __name__ == "__main__":
    main()
