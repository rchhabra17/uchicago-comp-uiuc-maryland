# Case 1: Stock A — Market Making Strategy

## Overview

Stock A is a small-cap stock with a **constant P/E ratio** per round. Earnings (EPS) are released twice per day at tick 22s and 88s. Fair value is simply `EPS × P/E`. Our edge comes from reacting to earnings faster than other participants and collecting spread between earnings releases.

The bot operates in two modes: **sweeping** (aggressive, post-earnings) and **quoting** (passive, between earnings).

---

## Fair Value Model

```
fair_A = EPS_A × PE_A
```

- P/E is constant within a round but unknown at the start.
- We **calibrate P/E** using the first earnings release (at 22s of day 1): wait ~10 seconds for the market to settle, then compute `PE = market_mid / EPS`.
- All subsequent earnings use the calibrated P/E to compute fair value instantly.

---

## Strategy Lifecycle (Per Day)

### 1. Pre-Calibration (0s–32s, Day 1 only)

- No earnings-derived fair value yet. Use market mid as a proxy.
- Quote **very wide** (±10) with **small size** (5 lots) and **tight position limits** (±20).
- Goal: collect a little spread without taking on risk from an uncertain fair value.

### 2. Calibration (22s–32s, Day 1 only)

- First earnings arrives at 22s. Record EPS but do NOT trade on it (we don't know P/E yet).
- Fire an async task that waits 10 seconds, then reads the market mid and computes `PE = mid / EPS`.
- After calibration completes, the bot switches to normal mode.

### 3. Earnings Reaction (22s and 88s, all subsequent)

On every earnings release after calibration:

1. **Compute new fair value** (`EPS × PE`)
2. **Cancel all open orders on A** (prevent getting picked off at stale prices)
3. **Sweep the book** — aggressively buy asks below `fair - edge` and sell bids above `fair + edge`
4. **Re-quote** around new fair (only at 22s earnings; skip quoting at 88s since only 2 seconds remain in the day)

Sweeping is where the big money is. When fair value shifts by 50+ points and stale orders are sitting on the book, we can capture 30–60 points per fill instantly.

### 4. Passive Quoting (Between Earnings)

Every 5 seconds, the trade loop:

1. Cancels all existing quotes on A
2. Posts a new bid and ask around fair value
3. Collects spread when other participants trade against us

---

## Inventory Management (Critical)

Without inventory management, passive quoting bleeds money. The market trends in one direction, smart money sells into our bid repeatedly, and we accumulate a massive one-sided position that loses on mark-to-market.

### Skew

Quotes are shifted based on current position:

```
skew = -position × 0.15
bid = fair + skew - spread
ask = fair + skew + spread
```

If we're long 30 shares, skew = -4.5. This makes our ask cheaper (more likely to get hit, reducing position) and our bid lower (less likely to get filled, preventing further accumulation).

### Hard Cutoffs

- **Position > 40**: stop posting bids entirely (only post asks to reduce)
- **Position < -40**: stop posting asks entirely (only post bids to reduce)
- Sweep logic also caps at ±50 to prevent a single earnings event from overloading us

### Sizing

Order size is 5 lots per quote (not 10). Slower accumulation = more time to manage.

---

## Key Parameters (config.py)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `spread` | 6 | Half-spread for quoting. Wider = safer but less flow |
| `sweep_edge` | 1 | Min edge to sweep (don't sweep at exactly fair) |
| `order_size` | 5 | Lots per quote |
| `pe` | 1150 | Default PE, overwritten by calibration |
| `MAX_POSITION` | 100 | Exchange risk limit (update when published) |

Pre-calibration uses spread=10, size=5, position limit=±20.

---

## Code Architecture

### Files

- **`bot.py`** — Main bot logic. Subclasses `XChangeClient` and implements all event handlers.
- **`fair_value.py`** — `FairValueEngine` class. Stores EPS, PE, computes fair values. Handles calibration state.
- **`config.py`** — All tunable parameters in one place.
- **`risk.py`** — `RiskManager` class. Position tracking and limit checks.

### Key Functions in bot.py

| Function | Purpose |
|----------|---------|
| `cancel_all_orders(symbol)` | Cancel all open orders for a symbol |
| `sweep_book(symbol, fair)` | Walk the book and aggressively take mispriced orders |
| `quote_around(symbol, fair)` | Post bid/ask with inventory skew and hard cutoffs |
| `calibrate_after_delay(symbol, eps)` | Async task: wait 10s, read mid, compute PE |
| `bot_handle_news(news)` | Dispatches earnings → cancel → sweep → quote |
| `trade_loop()` | Every 5s: cancel stale quotes, re-quote around fair |

### Event Flow

```
Round starts
  └─> trade_loop begins (quote around market mid, wide spread)
  
Day 1, tick 22s: First earnings
  └─> Record EPS, fire calibrate_after_delay()
  └─> 10s later: PE calibrated, fair_value now available
  └─> trade_loop switches to tight quoting around real fair

Day 1, tick 88s: Second earnings
  └─> Compute new fair, cancel all, sweep book
  └─> Do NOT re-quote (only 2s left in day)

Day 2+: Same pattern
  └─> 22s earnings: cancel → sweep → quote
  └─> 88s earnings: cancel → sweep → no quote
  └─> Between earnings: trade_loop re-quotes every 5s
```

---

## Lessons Learned from Testing

1. **Sweeps are the moneymaker.** Captured 30–60 points of edge per fill on earnings reactions. This is where speed matters.
2. **Passive quoting without inventory management is a money pit.** Without skew and cutoffs, the bot accumulated 100+ shares in one direction and bled on mark-to-market.
3. **Wider spreads are worth it.** Going from ±4 to ±6 reduced fill rate but each fill was more profitable and provided more cushion against adverse price moves.
4. **Pre-calibration quoting should be conservative.** Wide spreads, small size, tight position limits until we have a real fair value.
5. **Don't quote after 88s earnings.** Only 2 seconds left — not worth the risk of carrying stale quotes into the next day.

---

## Next Steps / Improvements

- **Tune skew coefficient** (currently 0.15) — backtest different values
- **Dynamic spread widening** when position is large or volatility spikes
- **Smart money detection** — if a large trade hits our quote, adjust fair value in their direction
- **Extend to Stock C, prediction markets, ETF arb** (Phases 3–5)
- **Track fills for real-time PnL** rather than relying on position snapshots
