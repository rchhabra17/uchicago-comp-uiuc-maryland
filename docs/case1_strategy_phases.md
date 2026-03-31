# Case 1: Market Making — Strategy Phases

## Phase 1 — Skeleton Bot
Get a bot that connects to the exchange, receives market data (order books, news events, positions), and can place/cancel orders. Even if it just quotes dumbly around last traded price. This is the foundation everything else bolts onto.

## Phase 2 — Stock A (Easiest Fair Value)
- A has a **constant P/E ratio**. Earnings (EPS) released twice per day (at 22s and 88s).
- Fair value = EPS × P/E. Simple plug-and-chug.
- Parse earnings messages, compute fair value, quote bid/ask around it.
- Edge is **speed** — react to earnings before other participants.
- Gets you comfortable with news parsing + order management.

## Phase 3 — Prediction Market + Stock C (Linked)
- Prediction market: R_CUT, R_HOLD, R_HIKE — probabilities that should sum to ~1000.
- E[Δr] = 25 × q_hike + 0 × q_hold − 25 × q_cut
- y_t = y_0 + β_y × E[Δr]
- C's price has two components:
  - **Operations**: P_ops = EPS_t × PE_t, where PE_t = PE_0 × e^(−γ(y_t − y_0))
  - **Bond portfolio**: ΔB ≈ B_0 × (−D×Δy + ½×C×Δy²)
  - **Combined**: P_t = EPS_t × PE_t + λ × ΔB/N + noise
- CPI news (structured): Actual > Forecast → inflationary → hike more likely → yields up → C down
- Unstructured news headlines: may or may not be relevant to Fed decision
- Trade the prediction market itself AND use it to price C.

## Phase 4 — B's Options (Arbitrage Focus)
- No direct info on B's price; infer from options.
- **Put-Call Parity**: C − P = S − K×e^(−rT). If violated, arb exists.
- **Box Spread**: Bull call spread + bear put spread at two strikes. Payoff = K2 − K1 always. If quoted price ≠ PV of that, arb.
- **Black-Scholes**: Back out implied vol, identify over/underpriced options.
- Strikes available: 950, 1000, 1050 (calls and puts at each).

## Phase 5 — ETF Arbitrage
- ETF = 1 share A + 1 share B + 1 share C.
- NAV = fair_A + fair_B + fair_C.
- ETF too expensive vs NAV → sell ETF, buy components (creation).
- ETF too cheap vs NAV → buy ETF, sell components (redemption).
- There's a swap fee — arb only works if mispricing > fee.
- Hint from packet: when gap exists, ETF is more likely mispriced than the equities.

## Phase 6 — Polish & Risk Management
- **Inventory management**: If getting long, widen bid / tighten ask to discourage more buying (vice versa for short).
- **Adaptive spreads**: Widen when uncertain or volatile, tighten when confident.
- **Smart money detection**: Large trades from informed participants → adjust fair value in their direction.
- **Asymmetric quoting**: Don't always quote symmetrically around fair — skew based on inventory and conviction.

---

## Key Observations from Practice Exchange Snapshot
- A mid ~744, B mid ~1099, C mid ~1011, ETF mid ~2849
- Component sum ~2854 vs ETF ~2849 → ETF slightly cheap (potential arb)
- Prediction market pricing: ~20% cut, ~58% hold, ~22% hike → E[Δr] ≈ +0.5 bps
- Options: deep ITM calls trade near intrinsic (little time value), deep OTM puts near zero
- Spreads on equities ~12 points (~1-1.6%)

## Priority
Phases 1-3 are where most points come from. A is easy money. C + prediction market is where fewer competitors will nail it. B options and ETF arb are bonus edges. A mediocre bot that trades A, C, and ETF beats a perfect bot that only trades B options.

## General Tips from Packet
- Nonlinear scoring: consistent positive P&L >> high variance strategies
- Later rounds are harder (wider bot spreads, more volatile, smarter bots) and weighted more heavily
- Adapt between rounds as edges shrink
- Practice round doesn't count
