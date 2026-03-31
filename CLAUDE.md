# CLAUDE.md — UChicago Trading Competition 2026, Case 1: Market Making

## Competition Context
This is a **live algorithmic trading competition** held April 11, 2026. We are building a market-making bot that trades on the UChicago X-Change platform against other teams and exchange bots. The bot runs locally on a provided VM via SSH/VSCode. Python is the only officially supported language.

### Structure
- **3 hours of rounds**, each ~15 minutes (10 simulated days × 90 seconds × 5 ticks/sec)
- Positions carry day-to-day within a round, reset between rounds
- Difficulty escalates: later rounds have wider bot spreads, more volatility, smarter bots
- **Later rounds are weighted more heavily** in scoring
- **Nonlinear scoring**: consistent positive P&L dramatically outperforms high-variance strategies. Don't gamble.
- Practice round does NOT count

### Tradeable Instruments
| Instrument | Description |
|---|---|
| **Stock A** | Small-cap, constant P/E, earnings released at t=22s and t=88s each day |
| **Stock B** | Semiconductor — no direct info, trade via options only |
| **Stock C** | Large-cap insurance co, price driven by operations + bond portfolio, linked to Fed prediction market |
| **ETF** | = 1 share A + 1 share B + 1 share C. Swap fee applies for creation/redemption |
| **Prediction Market** | R_CUT, R_HOLD, R_HIKE — probabilities summing to ~1000 |
| **Options on B** | European calls & puts, single expiry, strikes at 950, 1000, 1050 |

---

## Strategy Priority (in order of expected value)

### 1. Stock A — Easiest, implement first
- `fair_value_A = EPS × P/E` (P/E is constant)
- Parse earnings messages at t=22s and t=88s, recompute fair value, quote around it
- Edge is **speed** — react before others

### 2. Prediction Market + Stock C — Highest alpha ceiling
- Prediction market: `E[Δr] = 25 × q_hike + 0 × q_hold − 25 × q_cut`
- Yields: `y_t = y_0 + β_y × E[Δr]`
- C has two components:
  - Operations: `P_ops = EPS_t × PE_0 × e^(−γ(y_t − y_0))`
  - Bond portfolio: `ΔB ≈ B_0 × (−D×Δy + ½×C×Δy²)`
  - Combined: `P_t = EPS_t × PE_t + λ × ΔB/N + noise`
- CPI news: Actual > Forecast → inflationary → hike probability up → yields up → C down
- Unstructured headlines: filter for Fed-relevance, adjust prediction market positions
- Trade BOTH the prediction market contracts AND use them to price C

### 3. ETF Arbitrage
- `NAV = fair_A + fair_B + fair_C`
- ETF expensive vs NAV → sell ETF, buy components
- ETF cheap vs NAV → buy ETF, sell components
- **Factor in swap fee** — only arb if mispricing > fee
- Key insight: when gap exists, ETF is more likely mispriced than the equities

### 4. B Options — Bonus
- Put-Call Parity: `C − P = S − K×e^(−rT)`. Violation = arb.
- Box Spread: bull call spread + bear put spread at two strikes. Payoff = K2 − K1 always. If quoted ≠ PV, arb.
- Black-Scholes: back out implied vol, find mispriced options
- Use options to infer B's spot price for ETF NAV calculation

---

## Risk Management Principles
- **Inventory management**: If accumulating a long position, widen bid / tighten ask to reduce further buying. Vice versa for short.
- **Adaptive spreads**: Widen when uncertain or volatile, tighten when confident in fair value.
- **Smart money detection**: Large trades from informed participants → shift fair value estimate in their direction.
- **Asymmetric quoting**: Skew quotes based on current inventory and conviction, don't always center on fair value.
- **Consistency > magnitude**: The scoring function rewards steady P&L. Avoid huge bets. A few terrible rounds won't ruin you, but a few great rounds won't save you either.

---

## Coding Guidelines

### Architecture
- Keep the bot **modular**: separate modules for exchange connection, order management, fair value models, and risk management
- Each instrument strategy should be its own module/class that can be enabled/disabled independently
- Use a central state object tracking: positions, open orders, fair values, P&L
- All order placement should go through a single order manager that enforces risk limits locally before sending

### Risk Limits (enforced by exchange)
The exchange enforces and silently rejects orders that violate:
- Max order size
- Max open order size (unfilled)
- Outstanding volume (total unfilled)
- Max absolute position
- **Exact values will be posted on Ed before comp day — hardcode them as constants at the top of the bot**
- Implement local pre-checks so we don't waste round-trips on rejected orders

### Performance & Reliability
- **Speed matters for A earnings**: minimize latency between receiving earnings message and placing orders
- Use async or threaded I/O for the exchange connection — don't block on network calls
- Always cancel stale orders before placing new ones to avoid unintended fills
- Log everything: every order, fill, cancel, news message, fair value update. Debug logs are essential between rounds
- **Graceful error handling**: a crash mid-round is catastrophic. Wrap everything in try/except, log errors, keep running
- If the bot gets into an unknown state, it should cancel all open orders and stop quoting until the human intervenes

### Code Style
- Type hints on all functions
- Constants (P/E ratio, risk limits, spread widths, etc.) at the top of the file or in a config dict — easy to tune between rounds
- No magic numbers buried in logic
- Comments explaining the *why* of trading logic, not just the *what*

### Testing
- Test against the practice exchange before competition day
- Simulate edge cases: what happens when fair value jumps 5%? When position hits the limit? When the order book is empty?
- Have a kill switch: a way to instantly cancel all orders and flatten positions

---

## Key Numbers from Practice Snapshot (DONT USE UNLESS NEEDED)
- A mid ~744, B mid ~1099, C mid ~1011, ETF mid ~2849
- Component sum ~2854 vs ETF ~2849 → ETF slightly cheap (~5 points)
- Prediction market: ~20% cut, ~58% hold, ~22% hike → E[Δr] ≈ +0.5 bps
- Equity spreads ~12 points (~1–1.6%)
- Deep ITM calls trade near intrinsic, deep OTM puts near zero

---

## Between-Round Adaptation Checklist
1. Review P&L by instrument — which strategies made/lost money?
2. Check fill rates — are we getting picked off (adverse selection)?
3. Adjust spread widths if getting filled too often (tighten) or not enough (widen)
4. Review logs for any errors, rejected orders, or unexpected behavior
5. Update any parameters (γ, β_y, λ, etc.) if the market regime changed
6. Later rounds: expect wider bot spreads, less volume, more volatility — widen our spreads accordingly

---

## File Structure (target)
```
case1/
├── bot.py              # Main entry point, event loop
├── exchange.py         # Connection, message parsing, order submission
├── fair_value.py       # Fair value models for A, C, prediction market
├── options.py          # B options pricing, PCP/box spread arb
├── etf.py              # ETF NAV calculation and arb logic
├── risk.py             # Position tracking, inventory management, risk limits
├── config.py           # All tunable parameters
└── utils.py            # Logging, helpers
```

---

## Things Claude Should Remember
- **This is a competition, not production software.** Favor speed of development and correctness over elegance.
- **The human is driving.** Suggest approaches, flag risks, implement what's asked. Don't over-engineer unless asked.
- **Reference the case packet** (casepacket.pdf) and strategy phases doc (case1_strategy_phases.md) for formulas and mechanics.
- **When writing trading logic**, always think about: what's the worst case if this code has a bug? A quoting bug that buys unlimited shares at bad prices is much worse than a bug that fails to quote at all.
- **Defensive coding**: prefer failing safely (not trading) over failing dangerously (trading wrong).
- **Test everything with print statements / logs first** before going live. We only get ~12 rounds that count.
