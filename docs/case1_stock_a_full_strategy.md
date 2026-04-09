# Case 1: Stock A — Full Strategy Reference

This document is the source of truth for our Stock A strategy. It is split into two layers:

- **Layer 1 (Packet-Based)** — the "expected" strategy derived directly from the case packet. Calibrate fair value, react to earnings, manage inventory. Most teams will run something close to this.
- **Layer 2 (Aggressive / Anti-Competitor)** — strategies that exploit the fact that *everyone else is also running Layer 1*. This is where our real edge comes from.

The bot should implement Layer 1 as a foundation but lean heavily on Layer 2 in practice. In the limit, we may run a "pure sniper" variant that drops most of Layer 1's passive quoting entirely.

---

## Background

### What the packet tells us about A

- A is a small-cap stock with a **constant P/E ratio per round**.
- Fair value: `fair_A = EPS_A × PE_A`.
- Earnings (EPS) are released as structured news messages.
- **Earnings timing is NOT deterministic.** The original packet implied 22s and 88s per day, but in practice earnings can arrive at arbitrary times and there can be more than two per day. We must react to earnings events as they come, not on a schedule.
- P/E is unknown at round start and must be calibrated from the first earnings + market mid.

### Risk limits (placeholder — update when published)

- `MAX_POSITION` = 100
- `MAX_ORDER_SIZE` = 40

---

## Layer 1: Packet-Based Strategy

This is the baseline. It's correct, it works, and it's what most teams will do. It is not where our edge comes from, but it's the foundation everything else sits on.

### Fair Value Calibration

P/E is constant within a round but unknown at the start. Calibration procedure:

1. On round start, set `calibrating = True`. No fair value yet.
2. When the **first earnings** arrives while `calibrating = True`:
   - Record the EPS.
   - Fire an async task that waits ~10 seconds, then reads market mid and computes `PE = mid / EPS`.
3. After the delay, calibration completes. Fair value is now `EPS × PE` and updates on every subsequent earnings.

Why the 10-second delay: immediately after earnings, the market is in flux. Using the mid right at the release would calibrate against a stale or transient price. Waiting lets faster bots move the market to its true post-earnings level, which gives us a clean read.

### Earnings Reaction Lifecycle

On every earnings release **after** calibration is complete:

1. Compute new fair value (`EPS × PE`).
2. Cancel all open orders on A (prevent stale-quote pickoff).
3. Sweep the book aggressively — buy asks below `fair - edge`, sell bids above `fair + edge`.
4. Re-quote around new fair (Layer 1 only — Layer 2 may skip this).

Sweeping is the highest-EV action in Layer 1. It exploits other bots that haven't reacted yet and leaves stale orders on the book.

### Passive Quoting (Between Earnings)

A `trade_loop` runs every 5 seconds and:

1. Cancels existing quotes
2. Posts a new bid at `fair - spread` and ask at `fair + spread`
3. Collects spread from anyone who trades against us

**Inventory management is critical** for this to not bleed money:

- **Skew**: `skew = -position × 0.15`. Quotes shift against position. Long → quotes move down, making the ask cheaper to encourage offloading.
- **Hard cutoffs**: don't post a bid if `position > 40`; don't post an ask if `position < -40`.
- **Sweep cutoffs**: also cap sweep direction at `±50` so an earnings event can't blow through limits.
- **Sizing**: 5 lots per quote (not 10) for slower accumulation.
- **Pre-calibration**: wider spreads (±10), tighter position limits (±20), since fair value is uncertain.

### Layer 1 Parameters

```
spread       = 6      # half-spread for normal quoting
sweep_edge   = 1      # min edge to sweep
order_size   = 5      # lots per quote
skew_coef    = 0.15   # skew per share of inventory
soft_pos     = 40     # stop one side at this position
hard_pos     = 50     # don't sweep past this
pre_cal_spread = 10
pre_cal_size   = 5
pre_cal_pos    = 20
```

### Why Layer 1 Alone Is Not Enough

In a competition full of sophisticated bots, **passive quoting is mostly a bleed**. The reason:

- Tight quoters get all the uninformed flow (random buyers/sellers).
- You only get filled when a tight quoter is *unwilling* to fill — i.e., when the order is toxic.
- Wider quotes don't make you safer; they concentrate your fills in adverse-selection scenarios.

You can prove this to yourself by watching the fill log: when the price is drifting one direction, your quote on the wrong side gets hammered while the other side barely fills. That's not noise — that's the market telling you informed flow is going through you.

This is why Layer 2 exists.

---

## Layer 2: Aggressive / Anti-Competitor Strategy

The mental model shift: **we are not a market maker. We are a sniper.** Our edge is taking liquidity from people who are wrong, not providing liquidity hoping to get paid.

### Edge Source A: Stale quotes after news

When earnings drops, bots react at different speeds. The slowest ones still have orders on the book at pre-news prices for a few hundred milliseconds to a few seconds. We sweep them. This is Layer 1's sweep logic, but Layer 2 amplifies it:

#### A1. Pre-earnings flatten

Most teams accumulate inventory passively between earnings. When earnings hit, half the market is caught on the wrong side of a surprise.

We don't know exactly when earnings will arrive (since timing is non-deterministic), but we can use proxies:
- **Time since last earnings**: if it's been a long time, increase flattening pressure.
- **Position size**: if we're at `|pos| > 20`, actively work toward flat regardless of timing.
- **Continuous flattening preference**: rather than scheduling flattening events, build "stay near flat" into the quoting logic itself by raising the required edge for trades that increase position.

The clean version: make staying flat a soft constraint built into every trade decision. Don't quote in a way that builds inventory in the first place.

#### A2. Repeat-sweep window

After the initial earnings sweep, don't immediately switch to quoting. Instead, **keep scanning the book for 2–3 seconds**:

- After sweep #1, wait 200ms.
- Re-read the book.
- If new mispriced quotes appeared (slow bots that finally cancelled and re-quoted at *still-stale* prices), sweep them too.
- Repeat 4–5 times.

This catches the "I forgot to update my fair value" bots that re-post stale quotes after the initial flush.

#### A3. Surprise-magnitude scaling

Not all earnings are equal. A 2-point fair value shift creates a small sweep window with low edge. A 50-point shift creates a long window with huge edge.

- **Big surprise** (|Δfair| > 30): take maximum size, accept lower edge per fill, sweep further from fair.
- **Small surprise** (|Δfair| < 10): be picky, demand higher per-fill edge, smaller size.

Implementation: `sweep_edge = max(1, |Δfair| × 0.05)` or similar.

### Edge Source B: Bad-fair-value bots (continuous sniping)

Some teams will have buggy bots: miscalibrated PE, news parsing errors, math bugs, off-by-ones. These bots will sit on the book with quotes that violate our fair value *all the time*, not just at earnings.

The Layer 2 mechanism for catching this: **hook into `bot_handle_book_update`**. Every time the book changes, ask "is there now a quote on the book that violates my fair value by more than my edge threshold?" If yes, sweep it.

```python
async def bot_handle_book_update(self, symbol):
    if symbol != "A":
        return
    fair = self.fair_value.get("A")
    if fair is None:
        return
    await self.scan_and_snipe("A", fair)
```

This is fundamentally different from the Layer 1 trade_loop: it's event-driven rather than time-driven, so we react the instant a bad quote appears rather than waiting for the next 5-second cycle.

### Inventory Management for Layer 2

Since Layer 2 only takes +EV trades, it naturally drifts less than Layer 1. But it can still build inventory if the market is one-sided (e.g., post-bad-earnings, lots of stale bids → we sell a lot).

Solution: **edge threshold scales with inventory.**

```python
def required_edge_to_buy(pos):
    if pos < 0:    return 1                    # already short, happy to buy
    if pos < 20:   return 1
    if pos < 40:   return 5
    if pos < 60:   return 10
    return 999                                  # don't buy

def required_edge_to_sell(pos):
    return required_edge_to_buy(-pos)
```

We can even be willing to take **negative-edge trades to flatten** if our position is dangerous. Better to lose 3 points per share to exit than carry 60 shares into an earnings surprise that costs 50 points each.

### Optional: Far-from-fair lottery quotes

A small Layer 2 addition: post very small (1–2 lot) passive quotes at `fair ± 15`. These almost never fill, but when they do — fat finger, broken bot, flash crash — they hit massive edge. Free optionality with minimal risk capital.

This is the *only* passive quoting Layer 2 should consider. Skip the tight ±6 stuff entirely.

### The Pure Sniper Architecture

If Layer 2 is fully embraced, the bot looks like this:

```
On round start:
  - calibrating = True
  - no quotes posted
  - book scanner inactive

On first earnings:
  - record EPS
  - schedule calibrate_after_delay(10s)

After calibration:
  - book scanner active
  - on every book_update for A: check for mispriced quotes, snipe them
  - on every earnings: cancel any open orders, run repeat-sweep window

(Optional) far-from-fair lottery quotes posted every 30s

No tight passive quoting. No trade_loop posting bids/asks at fair±6.
```

The expected behavior: lower volume, lower variance, higher per-trade edge, much better fit with nonlinear scoring.

### Diagnostic to Validate Before Going Pure Sniper

Before committing to a quoteless bot, **add a logging-only book scanner** to the current Layer 1 bot. Every time the scanner *would* fire (a quote on the book violates fair by > edge), log it without trading. After running for a few minutes, check:

- How often do mispriced quotes appear in the book between earnings?
- How big is the average mispricing?
- How long do the mispriced quotes sit before being taken by someone else?

If mispricings are frequent and meaningful → pure sniper is +EV.
If the book is always tight and the scanner rarely fires → keep some Layer 1 quoting, but very wide.

---

## Recommended Build Plan

1. **Get Layer 1 working cleanly.** This is mostly done. Ensure inventory management (skew + cutoffs) is solid.
2. **Add the diagnostic book scanner** (logging only). Run for 5 minutes between earnings, look at the logs.
3. **Implement Edge Source B** (continuous sniping via `bot_handle_book_update`). Start with conservative edge thresholds.
4. **Implement Edge Source A2** (repeat-sweep window after earnings).
5. **Implement Edge Source A3** (surprise-magnitude scaling).
6. **Implement inventory-aware edge thresholds** for the sniper.
7. **Decide on the quoting layer**: tight passive (Layer 1), wide-only, far-from-fair lottery only, or none. Use the diagnostic data to choose.
8. **Optional A1**: build flattening pressure into the trade decisions rather than scheduling.

---

## Code Architecture

### Files

- **`bot.py`** — main bot logic, subclasses `XChangeClient`
- **`fair_value.py`** — `FairValueEngine` (EPS, PE, calibration state)
- **`config.py`** — all tunable parameters
- **`risk.py`** — `RiskManager` (position tracking, limit checks)

### Key Functions in bot.py

| Function | Purpose | Layer |
|----------|---------|-------|
| `cancel_all_orders(symbol)` | Cancel all open orders for a symbol | 1 |
| `sweep_book(symbol, fair)` | Walk the book and take mispriced orders | 1 |
| `scan_and_snipe(symbol, fair)` | Continuous version of sweep, called on book updates | 2 |
| `quote_around(symbol, fair)` | Post bid/ask with skew + cutoffs | 1 |
| `calibrate_after_delay(symbol, eps)` | Async: wait 10s, read mid, compute PE | 1 |
| `bot_handle_news(news)` | Earnings → cancel → sweep → (maybe) quote | 1 |
| `bot_handle_book_update(symbol)` | Snipe trigger for Layer 2 | 2 |
| `trade_loop()` | Time-driven re-quote (Layer 1 only) | 1 |

### Event Flow (Pure Sniper Variant)

```
Round starts
  └─> No quotes posted, no trade_loop active
  
First earnings event
  └─> Record EPS, fire calibrate_after_delay()
  └─> 10s later: PE calibrated, fair_value available
  └─> Book scanner activates

Subsequent earnings
  └─> Cancel any open orders
  └─> Run repeat-sweep window (4-5 sweeps over 2-3 seconds)
  └─> Resume passive book-update sniping

Continuous (between earnings)
  └─> Every book_update for A: check fair value, snipe if mispriced
  └─> Inventory-aware edge thresholds
  └─> (Optional) lottery quotes at fair ± 15
```

---

## Lessons Already Learned

1. **Sweeps are the moneymaker.** 30–60 points of edge per fill on earnings reactions.
2. **Naive passive quoting is a money pit** — accumulates inventory in one direction, bleeds on adverse moves. Inventory management is mandatory if quoting at all.
3. **Wider spreads do not actually make passive quoting safer** — they just concentrate fills in adverse scenarios.
4. **Pre-calibration must be conservative** — wide spreads, small size, tight position limits.
5. **Don't quote if very little time remains** in a day — risk of carrying stale orders into the next day exceeds the spread you'd collect.

---

## Open Questions

- How frequent are mispriced quotes between earnings? (Diagnostic needed.)
- What does the `MAX_POSITION` actually end up being from the published risk limits?
- Are there any patterns in *which* earnings come at *which* times that we can exploit?
- Should the lottery quotes be at a fixed offset or scaled by recent volatility?
- How do we detect when a "bad fair value bot" is actually informed and we shouldn't snipe them? (Possible solution: track fill outcomes — if our snipes are losing on average, the "stale" quotes were actually predictive.)
