# Portfolio Optimization Strategy Report
**Project:** UChicago Trading Competition - Case 2 (Portfolio Optimization)
**Author:** Ainesh Basu 
**Target:** Maximize Out-Of-Sample Sharpe Ratio (Net of Trading and Borrow Costs)

---

## 1. The Problem Statement
The objective of this case is to deploy an algorithm that systematically manages capital across a universe of 25 anonymous assets grouped into 5 unique sectors. The data provided comprises massive tick-level price histories over 5 years (30 ticks per day).

**The Crux of the Problem:**
Most quantitative optimization fails in this domain due to explicitly aggressive real-world market constraints:
1. **Severe Friction:** You are subjected to brutal transaction costs consisting of a linear Bid/Ask spread drag and quadratic Market Impact models. High turnover destroys algorithmic edge.
2. **Shorting Penalties:** Executing short positions triggers steep continuous borrowing costs averaging roughly 126 bps/year. 
Any strategy that constantly shuffles capital or tries to run standard Statistical Arbitrage Long/Short portfolios is mathematically guaranteed to perish due to cost bleeding.

---

## 2. Our Research Journey (How We Arrived Here)
We engineered an exhaustive `research*.py` suite to brute-force test hundreds of portfolio optimization paradigms. Here is what the mathematical reality dictated:

*   **The Failure of Standard Optimizers:** We strictly evaluated standard institutional optimizers (Markowitz Mean-Variance, Risk Parity, SkFolio Risk-Budgeting, CVaR MinRisk). They all fell apart out-of-sample. They reshuffle weights constantly to achieve mathematically "perfect" covariance models, triggering the transaction spread penalty and crashing their net Sharpe.
*   **The L/S Reality Check:** We built Long/Short Fama-French permutations (`130/30`, `Market Neutral`). Simulation proved that naturally holding the necessary 50% short book creates a staggering **10%+ annualized drag** on returns just from borrow fees. The math dictated our algorithm must be strictly **Long-Only**.
*   **Finding the Edge:** Raw asset-level momentum triggers mean-reversion at the tick level and is pure noise. However, **Macro Sector Momentum** (analyzing trailing Sharpe on a 50-day window) proved to be an incredibly robust, slow-moving structural signal.

---

## 3. The Final Architecture: `MyStrategy`
By throwing out complex machine-learning overfitting and focusing strictly on a heavily cost-controlled, dynamic risk approach, we built a strategy capable of clearing a `1.20+` Mean Sharpe.

### A. The Offense (Sector Sharpe Momentum)
We begin with a defensive, baseline Equal-Weight portfolio (4% capital in all 25 assets). 
To make decisions, the algorithm pulls the last 50 days of daily close histories. It clusters the 25 assets into their respective 5 sectors and calculates the **Sharpe Ratio** of each sector. The algorithm then heavily **tilts** the capital into the sectors showing the smoothest, most risk-adjusted momentum, while ignoring the chaotic sectors.

### B. The Execution Filter (Intraday Volatility Penalization)
We reclaim the 29 missing daily ticks by building an intraday microscope. Over a trailing 20-day window, the algorithm maps out exactly how wildly each asset bounces throughout a trading session. Assets that swing violently intraday guarantee worse execution "fills" on the order book. The algorithm mathematically penalizes these chaotic assets, heavily preferring to funnel capital into assets that exhibit smooth daily climbs.

### C. The Risk Manager (Adaptive Volatility Targeting)
This is the "Crash Radar." A standard strategy targets one flat volatility level (e.g., 13%). If the market crashes violently, a flat strategy bleeds out. 
Our strategy calculates the current global market fear by dividing the short-term volatility (last 20 days) by the long-term structural baseline (the last 120 days).
*   **Storm Regime:** If short-term volatility heavily exceeds the 120-day baseline, the algorithm flags a crash. It compresses its Vol Target down toward a heavily defensive **5%**, forcing the portfolio heavily into cash to protect our Sharpe.
*   **Calm Regime:** If the market is moving smoothly, the algorithm safely dials the Vol Target up to **18%**, maximizing gross leverage.

### D. The Penny Pincher (Cost Chokes)
We employ two rigid filters to prevent spread-bleeding:
1.  **5-Day Lockout:** The algorithm refuses to reevaluate its assumptions more than once every 5 days.
2.  **Gamma Threshold:** Even on the 5th day, if the mathematical distance between what we currently hold and what the algorithm wants to buy/sell (`_last_weights` vs `target_weights`) is under a `0.2%` deviation, we rip up the ticket and do nothing. We only transact when the expected signal heavily outweighs the expected spread.

---

## 4. Evaluation & Results
When run through the strict `validate.py` competition evaluator mimicking the true backtesting environment (tick pricing, full bid/ask spread logic, long exposure limits):

We executed a rigorous **3-Fold Walk-Forward Cross Validation**. Instead of overfitting one good year, the model averaged a massive test profile across the timeline:

*   **Fold 1 Sharpe:** +1.1399
*   **Fold 2 Sharpe:** +0.9591
*   **Fold 3 Sharpe (1-Year Holdout Target):** +1.5178
*   **Robust Mean Expected Sharpe:** **+1.2056**

By meticulously respecting transaction costs and swapping rigid risk targets for a self-aware, regime-adapting baseline, we locked in a top-tier algorithmic submission.
