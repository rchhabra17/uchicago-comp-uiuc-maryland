# Stock A Strategy Update v2 — Empirical Findings & Implementation Spec

This doc updates `case1_stock_a_full_strategy.md` based on analysis of two practice rounds of data collected by the observer bot in `data_collector.py`. It is intentionally additive, not a replacement — read the original A doc first for context, then read this for what's changed and why.

The short version: **the packet's `fair = EPS × PE` model is empirically wrong**, post-earnings convergence is much slower than I previously assumed, the pre-earnings drift hypothesis from the original doc is rejected, and A-symbol news events are a huge untapped edge source. The changes below are grounded in specific numerical findings, documented in the "Empirical Findings" section so anyone reading this can understand why each parameter value was chosen.

---

## Empirical Findings

All findings come from the `earnings_*.csv`, `ticks_*.csv`, and `news_*.csv` files in `case1/data/` from the 2026-04-09 practice rounds.

### Finding 1: PE is NOT constant. Fair value is linear in EPS.

Fitting `settled_mid = a + b × EPS` across 20 earnings events in a single round:

```
settled = 186.7 + 993.7 × EPS
R² = 0.9975
residual std = 3.8, max abs residual = 7.2
```

Compared against the packet's constant-PE model (fit as PE = mean(settled/EPS) = 1189):

```
settled = 1189 × EPS
R² = 0.9590
residual std = 15.6, max abs residual = 26.2
```

The constant-PE model has **systematic, monotone residuals**: positive at low EPS, negative at high EPS. At EPS=0.82 it under-predicts by 26 points; at EPS=1.10 it over-predicts by 25 points. That 50-point systematic error band is what teams using EPS×PE will eat on every earnings trade, and it's what our bot will avoid and then exploit.

**Round-to-round the coefficients are not stable.** Round 1 fit was `503 + 760 × EPS`; round 2 was `187 + 994 × EPS`. The *structure* (linear) is stable across rounds; the *parameters* (slope, intercept) must be refit from scratch every round.

**EPS values are continuous, not quantized.** The round 2 data has EPS values like 0.8220, 0.8506, 0.8849, 0.8887, 0.8989, ..., 1.0948. Continuous EPS gives dramatically better regression fits per sample — 3-4 earnings events are enough for a usable fit, 6-8 for a tight one.

### Finding 2: Post-earnings convergence takes ~10 seconds.

Mean absolute deviation of the observed mid from the eventual settled price, as a function of time since the earnings print:

```
+0.5s:  95 pts    ← market has essentially not moved
+1s:    96 pts
+2s:    95 pts
+3s:    89 pts
+4s:    76 pts
+5s:    59 pts    ← halfway there
+6s:    44 pts
+8s:    41 pts
+10s:    5 pts    ← essentially converged
+12s:    3 pts
```

This is the single most important operational finding. At one full second after the earnings print, the observed mid is still ~95 points away from where it will settle. The market as a whole takes 5-6 seconds to reach halfway convergence and a full 10 seconds to reach its new fair value.

This means the sniping window is much larger than the original A doc assumed (which specified a 2-3 second repeat-sweep window). The correct window is **6-8 seconds of continuous sniping with many re-scans**, because stale quotes are appearing and re-appearing across that entire window as slow bots flush and re-post.

### Finding 3: Overshoot is real but smaller than sniping edge.

Post-earnings mids frequently overshoot the eventual settled price before reverting:

```
Upward earnings moves (n=10):   mean overshoot 20 pts, max 52
Downward earnings moves (n=10): mean overshoot  9 pts, max 16
```

Asymmetric: upward moves overshoot about twice as far as downward moves. Possibly a panic-buying effect; possibly sample noise with n=10 each.

**Action:** not worth a dedicated strategy yet. The sniping edge is 95 points at +1s; the overshoot fade is 20 points at best. The sniping trade dominates until it's fully productionized.

### Finding 4: The "pre-earnings drift" hypothesis is rejected.

I previously theorized (from a tiny sample in round 1) that prices drift ~20 points in the direction of upcoming earnings before the print. Tested across 20 earnings in round 2:

```
Correlation between 10s-pre-earnings drift and actual earnings move: -0.05
Sign match: 9/20 (coin flip)
```

**There is no signal.** The original observation was noise. Do not build anything that tries to front-run earnings based on pre-print drift, and do not worry about other teams doing it to us either — the information isn't leaking before the print.

### Finding 5: A-symbol news events are the second-biggest edge source.

Four A-symbol news events in round 2. Two of them produced catastrophic price moves:

**"A loses exclusive rights to flagship technology patent"** — pre=1172
```
+0s:  1172    ← news arrives
+2s:  1168    ← almost no movement
+5s:  1176    ← market actually drifting UP (wrong direction)
+7s:  1183    ← still drifting UP
+9s:  1129    ← sudden -54 drop
+11s: 1048    ← continued crash
+14s: 1040    ← settled at -132
```

**"A suffers from declining consumer brand perception"** — pre=1265
```
+0s:  1265
+2s:  1284    ← UP 19 pts (wrong direction!)
+3s:  1260
+5s:  1188    ← -77
+7s:  1068    ← -197
+9s:  1044
+11s: 1090    ← settled around -175
```

The structural pattern for big A-news events:

1. **5-8 seconds of delay** where the market drifts aimlessly or moves in the wrong direction.
2. **Sudden large correction** in the correct direction.
3. **Settled mid is 100-200 points away** from pre-news mid.

Per the user's working assumption (pending more samples for verification), this 7-9 second delay and the symmetric up/down behavior are stable patterns we can build on.

The implication is that **any bot that parses sentiment at all — even via keyword matching — has a 5-7 second window to trade in the correct direction before the market moves.** That's an enormous edge: 100+ points of conviction trading per share, on a trade that's fully executable within 1-2 seconds of the news arriving.

Macro news (CPI, FEDSPEAK, unsymboled Fed headlines) showed ~10-15 point max moves on A, mostly indistinguishable from noise. **Macro news does not meaningfully move A — ignore it in A's handlers.** (It still matters for C.)

---

## Strategy Changes

Each change below ties to specific findings. Implementations should preserve the original A doc's Layer 1 / Layer 2 structure, but with these updates.

### Change 1: Replace PE calibration with linear-fit calibration.

**Why:** Finding 1. The packet's `EPS × PE` model has a 50-point systematic error band across the EPS range, which means any strategy relying on it is mispricing A whenever EPS is far from 1.0. Teams using the packet formula will consistently buy above fair on high-EPS prints and sell below fair on low-EPS prints.

**What:**

- `FairValueEngine` in `fair_value.py` stores a list of `(eps, settled_mid)` pairs, not a single PE value.
- Calibration uses robust linear regression (`numpy.polyfit` is fine for a first pass; consider Theil-Sen if outliers appear in later rounds) to fit `settled_mid = a + b × eps`.
- Expose two methods:
  - `fair_value(eps) -> float`: returns `a + b * eps` if calibrated, else None.
  - `implied_eps(price) -> float`: returns `(price - a) / b`, used for reading the market's expected EPS off the current mid.
- Refit on every new sample, not just the first. Each earnings event refines the estimate.
- Drop the old PE variable entirely. Do not fall back to `EPS × PE` at any point — either the linear fit is calibrated or it isn't.

**Minimum samples to trade:** require at least **3 distinct EPS samples** before the fit is considered usable. With 2 points you can fit a line but it has no error bars; with 3+ you can sanity-check the residuals. Until then, the bot should not actively snipe or quote tightly on A.

### Change 2: Sampling discipline for calibration.

**Why:** The fit is only as good as the samples. If we record the mid at +1s after earnings, we're recording a mid that's 95 points away from where it will settle, poisoning the regression. Finding 2 gives us the exact window to sample in.

**What:**

- When an earnings event arrives, start a timer. Do **not** record the calibration sample immediately.
- At `earnings_time + 12s`, read the average mid over the window `[+10s, +15s]`. This is the "settled" value.
- Store `(eps, settled_mid)` and refit.
- **Skip contaminated samples:** if another news event (earnings or A-symbol news) arrives in `[earnings_time, earnings_time + 15s]`, throw out that sample — do not store it. A contaminated sample will bias the fit.
- Log every sample to a calibration log so we can audit the fit quality mid-round.

### Change 3: Extend the repeat-sweep window to 6-8 seconds.

**Why:** Finding 2. At +3s after earnings the mean deviation from settled is still 89 points; at +5s it's 59. The old 2-3 second window was capturing only the first third of the sniping opportunity.

**What:**

- After any earnings event on A, enter a "sniping mode" that lasts 8 seconds.
- During sniping mode, on every book update, scan the book for quotes mispriced relative to the newly-computed linear-model fair value and take them aggressively.
- No passive quoting during sniping mode. Cancel any existing quotes before starting.
- At the end of the window, exit back to normal operation.
- Inside sniping mode, the required edge can decay over time: very aggressive (edge = 2) in the first 2 seconds, then rising to edge = 8 by the end of the window. The intuition: earliest fills have the most stale-quote edge; later fills are increasingly likely to be against informed counterparties.

**Sniping mode parameters (starting values, tune from logs):**

```
sniping_duration_s      = 8.0
sniping_edge_start      = 2     # first 2 seconds
sniping_edge_mid        = 4     # seconds 2-5
sniping_edge_late       = 8     # seconds 5-8
sniping_max_per_scan    = 20    # size cap per individual sweep action
```

### Change 4: Add an A-news sentiment module.

**Why:** Finding 5. Big A-symbol news events produce 100-200 point moves, but the market doesn't react for 5-8 seconds. Per our working assumption, this delay is stable and the effect is roughly symmetric up and down. A bot that fires the correct directional trade within 1-2 seconds of the news captures an enormous edge against the ~5 seconds of runway before the rest of the market moves.

**What:**

- New module `case1/news_sentiment.py` with a single class `NewsSentimentClassifier`.
- Input: news message dict (with `symbol` and `content`).
- Output: one of `"bullish"`, `"bearish"`, `"neutral"`, `"unknown"`, plus a confidence score in [0, 1].
- Classifier logic, v1: keyword-based scoring.
  - **Bearish keywords (strong):** `loses`, `lost`, `suffers`, `declining`, `insolvency`, `recall`, `lawsuit`, `investigation`, `missed`, `plunges`, `slips`, `fingers` (as in "slips through A's fingers"), `warns`, `warning`, `downgrade`, `risk`
  - **Bullish keywords (strong):** `awarded`, `wins`, `won`, `alliance`, `expansion`, `partnership`, `innovative`, `reduce costs`, `record`, `boosts`, `beats`, `breakthrough`, `signed`, `contract` (unless combined with a bearish qualifier like "loses contract")
  - Score = (bullish hits - bearish hits). Confidence = |score| / max(1, total hits). If confidence ≥ 0.7 and at least one keyword hit, emit a direction. Otherwise emit "unknown" and do not trade.
- **Only trade on A-symbol news.** If `symbol != "A"`, classify but do not trade on it (other assets' bots will handle their own news).
- **Macro news with no symbol → neutral, do not trade.** (Finding 5 showed these don't move A meaningfully.)

**Trading logic on a high-confidence A-news event:**

1. Compute expected fair value shift. Since we don't know the exact magnitude before the market moves, use a **fixed point estimate** based on historical: assume ~150 point shift in the direction of sentiment. (This is a working assumption; refine with more samples.)
2. Compute target fair = current_fair + 150 × direction.
3. Cancel all open A orders.
4. Aggressively take the opposite side: on bullish news, market-buy into the asks up to our position limit; on bearish news, market-sell into the bids.
5. Size: start with **half of normal max position** until the strategy is validated. Better to capture less edge reliably than over-commit on a misclassification.
6. Enter a "post-news mode" that lasts 10 seconds, during which we don't take new directional positions (we just hold what we captured). After 10 seconds, the market has likely converged, and we can reassess whether to hold or exit.

**Exit logic:** since we bought at roughly pre-news fair, and the market will converge to pre-news_fair + 150 × direction, we can either:
- (a) Hold the position and wait for settlement, or
- (b) Exit aggressively once the market has moved in our direction (say, once mid has moved > 80 points).

Start with option (b) — take the profit once the market has moved, don't hold for settlement. Holding exposes us to the next earnings, which could reverse direction.

**Critical safety check:** the original A doc said "get out of the way when news hits." That's still the right default for unclassified news. The sentiment module is an *additional* path that activates only on high-confidence classifications. If the classifier returns "unknown", the old defensive behavior (cancel orders, pause, wait) takes over.

### Change 5: Remove pre-earnings drift logic.

**Why:** Finding 4. The hypothesis was wrong. Any A1-style flattening pressure that's triggered by "time since last earnings" or "expected drift before earnings" should be removed.

**What:**

- Delete any code path that tries to anticipate earnings from pre-print price movement.
- Inventory management becomes purely reactive: skew away from current position, don't try to predict where earnings is coming from.
- Keep the position cap logic — that's not drift-based, that's risk control.

### Change 6: Macro news is ignored for A.

**Why:** Finding 5. CPI, FEDSPEAK, and unsymboled headlines don't meaningfully move A.

**What:**

- In `bot_handle_news`, if the news symbol is None and the content doesn't contain "A " or start with "A", do not do anything from the A bot's perspective.
- Log the event for cross-bot coordination (the C bot will use it), but don't react.

---

## File Changes Summary

### `case1/fair_value.py`
- Rewrite `FairValueEngine` for A:
  - Store `List[Tuple[float, float]]` of calibration samples.
  - Add `add_sample(eps, settled_mid)`, `fit()`, `fair_value(eps)`, `implied_eps(price)`, `is_calibrated()`, `n_distinct_eps()`.
  - Remove the old `pe` attribute and `calibrate()` method. Do not leave them as deprecated — delete them.

### `case1/bot.py`
- Rewrite `bot_handle_news` for A:
  - Route earnings events to `handle_a_earnings()`.
  - Route A-symbol news events through `NewsSentimentClassifier` and then into `handle_a_news_trade()` if high confidence.
  - Macro news: no-op for A.
- New method `handle_a_earnings(eps)`:
  - Cancel all open A orders.
  - Schedule `record_calibration_sample(eps)` to fire at +12s.
  - Enter sniping mode for 8 seconds.
- New method `record_calibration_sample(eps)`:
  - Compute settled mid as mean of `[+10s, +15s]` window (bot can query its own tick buffer or just store the mid at that moment — both fine, the former is cleaner).
  - Check for contamination: was there another news event in `[earnings_time, +15s]`? If yes, skip.
  - Call `fair_value.add_sample(eps, settled_mid)` and refit.
- New method `handle_a_news_trade(direction, confidence)`:
  - Aggressive directional trade as described in Change 4.
  - Enter post-news mode for 10 seconds.
- New sniping-mode loop: scan book on every book update while sniping mode is active, take mispriced quotes, edge threshold rises with time in window.

### `case1/news_sentiment.py` (new file)
- `NewsSentimentClassifier` class with keyword lists and the classify method.
- Keyword lists should be module-level constants and easy to edit.

### `case1/config.py`
- Add the new parameters from Changes 3 and 4.
- Remove any PE-related config.

### `case1/risk.py`
- No major changes expected. Position caps still apply. Confirm the new news-trade path respects `MAX_POSITION`.

---

## Parameter Starting Values

Put these in `config.py` with comments linking back to the findings that justify them.

```python
# Calibration (Change 1, 2)
MIN_DISTINCT_EPS_FOR_TRADING = 3       # from Finding 1: 3+ samples for usable fit
SETTLED_WINDOW_START_S       = 10      # from Finding 2: market converged by +10s
SETTLED_WINDOW_END_S         = 15      # tail end of convergence window
CONTAMINATION_WINDOW_S       = 15      # skip sample if another news event within this window after earnings

# Sniping mode (Change 3)
SNIPING_DURATION_S           = 8.0     # from Finding 2: convergence takes ~10s
SNIPING_EDGE_EARLY           = 2       # first 2 seconds, maximum aggression
SNIPING_EDGE_MID             = 4       # seconds 2-5
SNIPING_EDGE_LATE            = 8       # seconds 5-8
SNIPING_MAX_PER_SCAN         = 20      # safety cap per sweep action

# News sentiment trading (Change 4)
NEWS_CONFIDENCE_THRESHOLD    = 0.7     # minimum classifier confidence to trade
NEWS_EXPECTED_MOVE           = 150     # from Finding 5: observed moves 120-200 pts
NEWS_TRADE_SIZE_FRACTION     = 0.5     # of MAX_POSITION; halved until validated
POST_NEWS_MODE_S             = 10      # from Finding 5: ~10s for market to converge
NEWS_EXIT_MOVE_THRESHOLD     = 80      # exit aggressively once mid moves this far in our direction
```

---

## Validation Plan

After implementation, before trusting the bot in a real round:

1. **Run the observer bot in parallel** to the trading bot in a practice round. Compare the trading bot's computed fair values (via logs) against the observer's recorded earnings samples. They should agree to within a few points after calibration completes.
2. **Log every calibration sample and fit.** After the round, manually verify the fit is reasonable — R² should stay above 0.97 once 5+ samples are in.
3. **Log every news classification and resulting trade.** After the round, manually check: did the classifier fire on the right events? Were the trades profitable? Were there misclassifications that cost us?
4. **Sanity check the sniping window.** Log every sweep action with its edge-at-time and pnl. The first 2 seconds should show the highest per-trade edge; it should decay over the window.

These are all diagnostics, not runtime gates. Don't block trading on the diagnostics — just make sure we can audit after each round and improve parameters between rounds.

---

## Things NOT To Change

Explicit list of things the original A doc gets right and should be preserved:

- The Layer 1 / Layer 2 split. Still correct.
- The "pure sniper" architectural preference (no tight passive quoting between earnings). Still correct.
- The inventory-aware edge threshold idea (`required_edge_to_buy(pos)`). Still correct.
- The lottery-quote idea at `fair ± 15`. Still a valid optional add-on, though low priority.
- The continuous sniper hook into `bot_handle_book_update`. Critically, this still runs between earnings, catching buggy bots that post mispriced quotes outside of earnings windows.
- The diagnostic-first mindset: log a behavior before trading on it, verify it's real, then turn on the trading.

---

## Open Questions (to revisit after more rounds)

- **Is the 7-9s news delay consistent round-to-round?** Working assumption: yes. If later rounds show the delay shrinking (as other teams' bots get faster), the news trade window tightens and the classifier confidence threshold may need to rise.
- **Does the keyword list generalize?** The keyword lists are from a handful of observed headlines. New rounds will have new phrasings that may not match. Plan: log every A-news event and its classification result, post-round review the ones that were "unknown" and expand the lists.
- **Are there any A-news events where the keyword classification is *wrong* (e.g., sarcasm, double negation)?** Worth manual review of misclassifications after every round.
- **Can we predict the magnitude of a news move from its content?** Currently using a fixed 150-point estimate. If moves cluster by news type (e.g., "patent loss" = 130, "brand perception" = 180), we could size more precisely.
- **Does the overshoot pattern persist across rounds?** If so, a lightweight overshoot-fade could add another few points per earnings, but it's low priority vs. the main sniper and news trades.
