import sys
sys.path.insert(0, ".")
from validate import run_backtest, annualized_sharpe, TICKS_PER_DAY, TRADING_DAYS_PER_YEAR
from submission import load_prices, load_meta, _EW, N_ASSETS, ewma_vol, enforce_gross_limit, tilt_weights, sector_sharpe_signal, StrategyBase
from research_combine import CombinedStrategy
import numpy as np
prices = load_prices()
meta = load_meta()
ticks_per_year = TRADING_DAYS_PER_YEAR * TICKS_PER_DAY

for vlb in [60, 120]:
    for iw in [0.0, 0.1, 0.2, 0.3]:
        for scale in [30, 40, 50]:
            sharpes = []
            for k in range(2, 5):
                train_end = k * ticks_per_year
                test_end = (k + 1) * ticks_per_year
                if test_end > prices.shape[0]: break
                strat = CombinedStrategy(base_tv=0.13, vol_regime_lb=vlb, intraday_w=iw, scale=scale, lookback=50)
                res = run_backtest(prices[:train_end], prices[train_end:test_end], strat, meta)
                sharpes.append(annualized_sharpe(res["daily_returns"]))
            mean_s = np.mean(sharpes)
            print(f"vlb={vlb} iw={iw} scale={scale} | Mean={mean_s:.4f} F1={sharpes[0]:.4f} F2={sharpes[1]:.4f} F3={sharpes[2]:.4f}")
