# CLAUDE.md — UTC 2026 (UChicago Trading Competition)

This file provides guidance to Claude Code when working on code in this repository.
Read this before touching anything.

---

## Competition Overview

**UTC 2026 — 14th Annual UChicago Trading Competition**

- **Location:** Convene Willis Tower, 233 S. Wacker Drive, Chicago
- **Case 1 (Live Trading):** Saturday April 11, 2026 — algorithmic + click trading on a live exchange
- **Case 2 (Portfolio Optimization):** Take-home, due **11:59 PM CST Thursday April 9, 2026**
- **Scoring:** Rank-based across ALL teams globally (not just your room). Penalty = (rank − 1)². Lower is better.
- **Language:** Python is the **only officially supported language**. No explicit support for other languages.
- **Awards:** Cash prizes for winning team of each individual case + top 3 overall aggregate scores.
- **Attendance:** All sessions Friday and Saturday are **mandatory** to be eligible for prizes.

### Schedule (CDT)

**Friday April 10:**
- 5:00–8:00 PM — Poker Tournament at Kimpton Gray Hotel (122 W. Monroe), sponsored by DRW

**Saturday April 11:**
- 8:00–8:45 AM — Breakfast & Arrival
- 8:45–9:00 AM — Welcome & Agenda
- 9:00–9:15 AM — Tech Case Prep
- 9:15 AM–12:15 PM — **Case 1 Live Trading** (3 hours of rounds)
- 12:15–2:00 PM — Lunch & Employer Career Fair
- 2:00–2:45 PM — Overview of Cases 1 & 2 + Q&A
- 2:45–4:15 PM — Networking Reception
- 4:15–5:00 PM — Awards Presentation

---

## Case 1: Live Trading (Market Making)

### Exchange Connectivity

| Parameter                  | Value                                                |
| -------------------------- | ---------------------------------------------------- |
| Practice UI                | https://practice.uchicago.exchange/                  |
| Practice bot endpoint      | `practice.uchicago.exchange:3333` (no HTTPS)         |
| Competition day endpoint   | TBD (same AWS VPC as your EC2 instance)              |
| Exchange tick rate         | every 200 ms                                         |
| Round duration             | 15 minutes                                           |

- Bot connects via **gRPC** using `utcxchangelib` (Python 3.12)
- Install: `pip install git+ssh://git@github.com/UChicagoFM/utcxchangelib.git`
- **Always use the latest version** — reinstall before competition day
- Example bot: `utcxchangelib/examples/example_bot.py`
- On competition day you get a standard AWS EC2 instance to run your bot
- One team member is responsible for **manually starting** the algo at the beginning of each round
- Must know how to use **VSCode** and **SSH** into the provided box

### Round Structure & Scoring

- **3 hours** of successive rounds, each **15 minutes** long
- Each round = **10 simulated days**, each day = **90 seconds** with **5 ticks** per day (tick every 200 ms within a second)
- Positions **hold over** day-to-day within a round but **reset between rounds**
- **Settlement price** for all assets at end of each round; P&L calculated from final prices
- ETF settlement price = **NAV** (fair value)
- Everything is **marked to fair value** at settlement
- **Difficulty increases** over rounds: opposing market makers tighten spreads, volume decreases, volatility increases
- **Later rounds weighted more heavily** in scoring
- **Nonlinear grading:** converts P&L into points. Consistent profits >> high-variance strategies. Outlier results (positive or negative) have diminished marginal impact.
- Practice round does **NOT** count toward final points

### Tradable Instruments

| Symbol                            | Description                                                                 |
| --------------------------------- | --------------------------------------------------------------------------- |
| `A`                               | Small-cap equity. Priced via P/E × EPS. P/E **constant at 10**.             |
| `B`                               | Large-cap equity (liquid semiconductor). No direct pricing info given.      |
| `B_C_950`, `B_C_1000`, `B_C_1050` | **European** call options on B at strikes 950 / 1000 / 1050               |
| `B_P_950`, `B_P_1000`, `B_P_1050` | **European** put options on B at strikes 950 / 1000 / 1050                |
| `C`                               | Bond/rate-sensitive insurance company. Priced via business ops + bond portfolio. |
| `ETF`                             | ETF = 1 share each of A, B, and C                                           |
| `R_CUT`                           | Prediction market: probability of Fed rate **cut** (−25 bps)               |
| `R_HOLD`                          | Prediction market: probability of Fed rate **hold** (0 bps)                |
| `R_HIKE`                          | Prediction market: probability of Fed rate **hike** (+25 bps)              |

### Risk Limits

**Will be announced via pinned Ed post in advance of competition. Subject to change on competition day.**

Risk limits apply **per instrument** across all tradable assets:

| Limit                    | Description                                         | Current Value |
| ------------------------ | --------------------------------------------------- | ------------- |
| `max_order_size`         | Max lots per single order                           | 40            |
| `max_open_orders`        | Max number of unfilled orders                       | 50            |
| `max_outstanding_volume` | Total volume of unfilled orders                     | 120           |
| `max_absolute_position`  | Sum of long and short positions                     | 200           |

**Never hardcode these as literals.** Use named constants/variables so you can update instantly on the day. Exceeding any limit → entire order rejected. You are **not told which limit** you breached.

### Instrument Pricing Models

#### Asset A — Small-Cap Equity

- Quarterly earnings released **twice per day** as structured news
- Pricing: `price_A = P/E × EPS` where **P/E is constant at 10**
- Simple: just track EPS from earnings announcements and multiply by 10

#### Asset B — Options on Semiconductor Stock

- **One underlying path** for B per round
- No direct pricing information for B itself — focus on the **options**
- Quoted prices for **European** option chain across 3 strikes at each tick
- Options can **only be exercised at expiration** (not American-style)
- **Put-Call Parity (PCP):** `C − P = S − K·e^(−rT)` where C/P are call/put prices, S is spot, K is strike, r is risk-free rate, T is time to expiry
- A long call + short put at the same strike replicates a long forward
- If PCP is violated → **riskless arbitrage** opportunity
- **Box Spread:** Bull call spread + bear put spread at strikes K₁ < K₂. Payoff is always K₂ − K₁ regardless of underlying. If the box price ≠ PV of (K₂ − K₁) → arbitrage.

#### Asset C — Insurance Company (Bond + Business)

C's P/E ratio is **NOT constant** — it is inversely proportional to expected bond yields.

**Operating price (business component):**
```
P_t^op = EPS_t · PE_t
PE_t = PE_0 · exp(−γ · (y_t − y_0))
```

**Bond portfolio component (Taylor expansion):**
```
ΔB_t ≈ B_0 · (−D·Δy_t + (1/2)·C·(Δy_t)²)
```
where D = duration, C = convexity constants.

**Combined price of C:**
```
P_t = EPS_t · PE_t + λ · (ΔB_t / N) + noise
```
where N = number of outstanding shares, λ = weighting constant.

- Earnings news **twice per day** (at seconds 22 and 88 of each 90-second day)
- News includes structured (Forecasted vs Actual CPI) and unstructured (headlines)

#### Prediction Markets (R_CUT, R_HOLD, R_HIKE)

- Predict what the hypothetical Fed will do with rates: **hike (+25 bps), hold (0), or cut (−25 bps)**
- Quoted probabilities for each outcome provided
- Structured news: **Forecasted vs Actual CPI** prints. Actual > Forecasted → inflation → points toward rate hikes. Vice versa → rate cuts.
- Unstructured news: headlines that may or may not relate to the Fed's decision

**Expected rate change:**
```
E[Δr_t] = (+25)·q_t^hike + (0)·q_t^hold + (−25)·q_t^cut
```

**Yield update:**
```
y_t = y_0 + β_y · E[Δr_t]
```

#### ETF

- ETF = **1 share each** of A, B, and C
- Creation/redemption: swap between 1 ETF share ↔ 1 share each of A, B, C (small fee)
- Can also swap short (sell ETF, buy components) for a small fee
- Settlement price = **NAV (fair value)**
- Hint from case packet: when ETF and equity prices disagree, it's **more likely the ETF is mispriced**

### Revealed Model Parameters

```python
# For pricing C (bond instrument) and prediction markets
Y0       = 0.045    # Initial yield
PE0      = 14.0     # Initial P/E for C (NOT for A; A's P/E = 10 always)
EPS0     = 2.00     # Initial EPS for C
GAMMA    = ???      # γ — PE sensitivity to yield changes (not yet revealed)
BETA_Y   = ???      # β_y — yield sensitivity to expected rate change (not yet revealed)
D        = ???      # Duration of C's bond portfolio (not yet revealed)
CONVEX   = ???      # Convexity of C's bond portfolio (not yet revealed)
B0       = ???      # Initial bond portfolio value (not yet revealed)
N        = ???      # Number of outstanding shares for C (not yet revealed)
LAMBDA   = ???      # λ — weighting constant for bond component (not yet revealed)
```

# Risk Limits
  A:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_C_950:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_P_950:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_C_1000:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_P_1000:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_C_1050:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  B_P_1050:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  C:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  ETF:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  R_CUT:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  R_HOLD:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200
  R_HIKE:
    max_order_size: 40
    max_open_orders: 50
    max_outstanding_volume: 120
    max_absolute_position: 200

> **NOTE:** Several parameters are marked `???` — they may be revealed on competition day or via Ed posts. Monitor Ed closely.

### Key Strategies (from case packet)

1. **Provide Liquidity:** Post continuous bid/ask quotes. Spread = payment for risk. Think about optimal spread width.
2. **Manage Risk:** Monitor exposure. Consistent profits score higher than high-variance strategies.
3. **Strategic Adaptation:** Trade against "dumb" money bots. Smart money bots should inform your price expectations.
4. **Understand News:** Build a quantitative model for how structured + unstructured news moves prices. Every significant move = some news + some noise.
5. **Market Impact:** Your trades move prices. Impact depends on overall market liquidity.
6. **Put-Call Parity Arb:** If calls/puts violate PCP, trade the mispriced leg against a synthetic replication.
7. **Box Spread Arb:** If box price ≠ PV(K₂ − K₁), arbitrage is available.
8. **ETF Arb:** When ETF price diverges from NAV, create/redeem. Factor in fees. The ETF is more likely mispriced than the equities.
9. **Earnings Trading:** If you get info before the market, where to buy/sell? Not always best to post at "new price" — consider adverse selection.
10. **Asymmetric Quoting:** Adjust quotes if your model fair value diverges from market mid. Consider what happens if only one side gets filled.

### Miscellaneous Tips

- Different strategies work better at different competition stages — adapt between rounds
- Early rounds: wider spreads, more passive strategies work; Late rounds: need to be faster, more aggressive
- Consider whether to hold positions to settlement or pay to exit risk
- When to swap short on ETF vs. hold?

---

## Case 2: Portfolio Optimization

### Overview

- **25 assets** grouped into **5 sectors**
- **5 years** of historical data provided for each asset
- Given a CSV with **intraday tick prices** (30 ticks per day)
- Algorithm evaluated on the **12 months** immediately after the training data period
- **Scored on annual Sharpe ratio:** `Sharpe = sqrt(252) · mean(r_t) / std(r_t)` where r_t = daily returns
- **Due: 11:59 PM CST Thursday April 9, 2026**
- Case 2 is run **before** competition day; results announced at the event
- Code submitted past deadline **will not be accepted**; incomplete/non-compiling code → disqualification (0 points)

### Algorithm Requirements

- Receives price history observed so far each trading day
- Must output **weights** for each of the 25 assets
- Weights can be positive, negative, or zero
- **Sum of absolute weights must be ≤ 1** (if violated, evaluator rescales proportionally)
- Short positions incur an **annualized borrow cost** (in basis points) × fraction of year per tick

### Evaluation Mechanics

- Uses **intraday** price path, not just daily closes
- Portfolio return per tick = sum across assets of (weight × asset's simple return derived from log return)
- Short exposure charged: borrow cost × short exposure × (fraction of year per tick)
- **Rebalancing at end of each day** from old weights to new weights
- **Transaction costs** have two parts:
  1. **Linear:** 0.5 × spread × |Δweight|
  2. **Quadratic:** 2.5 × spread × (Δweight)²
- The starter code includes a reference evaluator — **reproduce these mechanics locally**

### Execution Environment

- **Python 3.12** only
- Available packages: **NumPy, pandas, scikit-learn, SciPy**
- Additional packages may be requested via Ed
- You may use any tools/languages for research, but **submitted code must be Python**
- Test locally before submitting — code that doesn't compile = 0 points

### Educational Concepts (from case packet)

**Markowitz / Mean-Variance Optimization:**
- Efficient frontier of max-return-for-given-risk portfolios
- Requires estimates of expected returns and covariance matrix
- Historical returns are weak predictors of future returns; covariance matrices are more stable
- Large covariance matrix estimates are numerically unstable → practical difficulties

**Risk Parity Allocation (RPA):**
- Ignore expected returns; equalize risk contribution from each asset
- Risk contribution of asset i: `w_i · (Σw)_i / sqrt(w^T · Σ · w)`
- Less risky assets get more weight; riskier assets get less weight
- Historical risk contribution is a reliable estimate

**Return Prediction:**
- Go beyond static allocation — exploit **predictable structure** in the data
- Key phenomena: **momentum, mean reversion, cross-asset correlations, volatility clustering**
- The 5-sector structure reflects real economic relationships
- Cross-sector and within-sector signals can compound into meaningful edges
- Daily movement ≠ intraday movement — understand both dynamics

### Key Tips (from case packet)

1. **Analyze returns, not prices.** Prices are non-stationary; returns are generally stationary.
2. **Don't test on training data.** Hold out a portion for out-of-sample validation. Overfitting kills you.
3. **Daily vs. intraday are different processes.** Portfolio optimization trades off short-term volatility with long-term predictability.
4. **Transaction costs matter.** Frequent rebalancing must justify its additional cost vs. lower-frequency approaches.
5. **Starter code is for understanding only** — do NOT take it as predictive of your final score.

---

## Code & Repo Conventions

### General

- Python 3.12 for all code
- Use type hints where practical
- Keep bot code modular: separate pricing logic, order management, risk management
- All magic numbers should be named constants at the top of the file

### Risk Limit Constants (update these on competition day)

```python
# --- RISK LIMITS (update from Ed post before competition) ---
MAX_ORDER_SIZE         = 40
MAX_OPEN_ORDERS        = 50
MAX_OUTSTANDING_VOLUME = 120
MAX_ABSOLUTE_POSITION  = 200
```

### Model Parameters (update as revealed)

```python
# --- MODEL PARAMETERS ---
# Asset A
A_PE_RATIO = 10.0  # Constant

# Asset C & Prediction Markets
Y0         = 0.045   # Initial yield
PE0_C      = 14.0    # Initial P/E for C
EPS0_C     = 2.00    # Initial EPS for C
GAMMA      = None    # PE sensitivity to yield (TBD)
BETA_Y     = None    # Yield sensitivity to rate change (TBD)
DURATION   = None    # Bond portfolio duration (TBD)
CONVEXITY  = None    # Bond portfolio convexity (TBD)
B0_BONDS   = None    # Initial bond portfolio value (TBD)
N_SHARES   = None    # Outstanding shares for C (TBD)
LAMBDA_W   = None    # Bond component weighting (TBD)

# News schedule: earnings at seconds 22 and 88 of each 90-second day
NEWS_SECONDS = [22, 88]
```

### Pre-Competition Checklist

- [ ] Reinstall latest `utcxchangelib`
- [ ] Verify bot connects to practice exchange
- [ ] Update risk limits from Ed post
- [ ] Update any newly revealed model parameters
- [ ] Test SSH into AWS EC2 box
- [ ] Ensure one team member can manually start the bot
- [ ] Case 2 code submitted by 11:59 PM CST April 9
- [ ] Test Case 2 code compiles and runs with Python 3.12 + numpy/pandas/sklearn/scipy only
- [ ] Verify Case 2 evaluator runs locally without errors

---

## Quick Reference: Key Formulas

### Asset A
```
Price_A = 10 × EPS_A
```

### Asset C — Operating Component
```
PE_t = PE_0 · exp(−γ · (y_t − y_0))
P_t^op = EPS_t · PE_t
```

### Asset C — Bond Component
```
ΔB_t ≈ B_0 · (−D · Δy_t + 0.5 · C · (Δy_t)²)
```

### Asset C — Combined
```
P_C = EPS_t · PE_t + λ · (ΔB_t / N) + noise
```

### Yield from Prediction Markets
```
E[Δr_t] = 25 · q_hike + 0 · q_hold − 25 · q_cut
y_t = y_0 + β_y · E[Δr_t]
```

### Put-Call Parity (European)
```
C − P = S − K · e^(−rT)
```

### Box Spread (strikes K₁ < K₂)
```
Fair value of box = (K₂ − K₁) · e^(−rT)
```

### Sharpe Ratio (Case 2)
```
Sharpe = sqrt(252) · mean(r_t) / std(r_t)
```

### Portfolio Variance
```
σ²_p = Σ_i Σ_j w_i · w_j · Cov(r_i, r_j)
```

### Transaction Cost (Case 2 rebalancing)
```
cost = 0.5 · spread · |Δw| + 2.5 · spread · (Δw)²
```
