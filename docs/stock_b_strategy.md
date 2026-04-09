# Stock B Options Strategy Architecture

## Overview
The Stock B ecosystem is a high-frequency, complex options chain incorporating three strikes (`950`, `1000`, `1050`) across both Calls and Puts, plus the underlying stock `B`. To capture alpha against both the central exchange market makers and erratic competitor bots, we have designed a **Unified Boundary Enforcement** architecture. 

Instead of treating each option as an independent trading problem, the entire family is aggregated into a single mathematical net. The bot fundamentally relies on Put-Call Parity (PCP) equations to establish the "Fair Value" floor and ceiling of the network, systematically exploiting competitors who execute inside or outside those boundaries.

---

## 1. Primary Exploit Algorithms (Active)

### Strategy A: The Dead-Leg Trap (Liquidity Provision)
In highly volatile competition phases, competitor bots will frequently execute "Market Orders" to dump options they no longer want, regardless of the true mathematical value.
* **Mechanism:** The bot instantly scans the order book for any option that has an active `Ask` (someone selling) but an entirely empty `Bid` (nobody buying). 
* **Exploit:** Recognizing a "dead leg," the bot actively provides floor liquidity by securely posting a permanent limit order to `BUY` at exactly `$1`. When a desperate competitor bot blindly market-sells, it is forced to cross the spread all the way down to our $1 floor, instantly feeding us zero-risk variance for pennies on the dollar.
* **Maintenance:** A dedicated background GC (Garbage Collection) loop runs every 500ms to scrub stale limit orders, but relies on safe protobuf extraction (`info[0].limit.px`) to explicitly exempt any order tagged at `$1`, keeping our trap nets eternally bolted to the exchange without spamming the RPC.

### Strategy B: Stale Sniping (Statistical Arbitrage)
Because 50 different teams are providing latency-bound quotes on 7 different assets asynchronously, pricing fragmentation is inevitable.
* **Mechanism:** Using an $O(1)$ pre-computed `quotes` hashmap, `compute_fair_b()` evaluates PCP across all tradeable strikes to triangulate the singular theoretical `fair_b` value. 
* **Exploit:** If the options network implies `fair_b` is trending at 1045, but the underlying stock `B` evaluates to an Ask of 1040, the sniper algorithm (`find_stale_orders`) instantly buys the lagging stock. We enforce a defined `B_SWEEP_EDGE` minimum gap to protect against delta risk.

---

## 2. Risk Management & Governance
To ensure the Trap and Snipe algorithms never inadvertently double-leverage the account or breach exchange bounds, we utilize an **Atomic Netting Gateway**:
1. **Total Package Evaluation:** All proposed trades for any given tick are pooled into a `total_changes` dictionary. 
2. **Gross Family Limit:** `can_trade_b_package` enforces rigid bounds, stopping execution if total isolated exposure ever exceeds `MAX_POSITION` (currently `40` for stock `B`, `20` for individual options legs), or if the cumulative asset weight exceeds `B_MAX_GROSS_FAMILY`.
3. **Roll & Release:** Because traps fill quickly, it is expected that humans will monitor the bounds and lightly adjust them, or clear accumulating delta dynamically during quiet periods to recycle the capital for more traps.

---

## 3. Core Arbitrage Engines (Backup)
In the event that the market makers tighten their liquidity bounds to the point where traps are no longer viable (or the volatility drops to zero), the bot is pre-equipped with pure, risk-free mathematical arbitrage functions. 

> [!NOTE]
> These modules are currently placed behind `# [TOGGLE START]` and `# [TOGGLE END]` markers in `bot.py` to prioritize latency budget for the Trap/Snipe loop. They can be instantly re-activated by uncommenting the blocks.

### Put-Call Parity (PCP) Arbitrage
Calculates $C - P = S - K * e^{-rT}$. If the gap widens past `config.B_PARITY_THRESHOLD`, the bot executes a simultaneous 3-leg trade (Buy/Sell Call, Buy/Sell Put, Buy/Sell Stock) locking in immediate mathematical edge.

### Box Spread Arbitrage
Evaluates $(C_{k1} - C_{k2}) + (P_{k2} - P_{k1})$. If the premium deviates from the risk-free $K2 - K1$ difference by `config.B_BOX_THRESHOLD`, the bot executes a 4-leg atomic Box, locking in a synthetic zero-coupon bond. Note: This requires absolute liquidity cross-checking natively built into `detect_box_signal` to prevent incomplete Box fractures.

### Butterfly Arbitrage
A pure structural, model-free exploit monitoring three adjacent strikes (e.g., `950`, `1000`, `1050`). The code actively calculates if $C_{950} + C_{1050} - 2 * C_{1000} < 0$. If this boundary is violated, the bot buys the wings and sells the middle (or vice-versa for Puts) to lock in instantaneous edge without relying on any pricing models.

### Vertical Spread Constraints
A fast, model-free bound enforcing that a lower-strike call MUST be more expensive than a higher-strike call ($C_{low} \geq C_{high}$). If a chaotic market pushes $C_{1000}$ below $C_{1050}$, the solver executes a 2-leg vertical spread to capture the guaranteed reversion.
