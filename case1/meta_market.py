# meta_market.py — Meta Market: total exchange fill count by tick 4400
"""
The meta market settles on the total number of fill messages broadcast
by the exchange through tick 4400 of the round.

Our edge: we see every trade message in real-time via bot_handle_trade_msg,
so we can project the final count faster than a manual MM can reprice.

Strategy: TAKER ONLY — pick off stale MM quotes.
  A manual MM is quoting these 4 contracts. They'll be slow to react to:
    - Fill rate spikes (news events at t=22s, t=88s cause bursts)
    - Gradual regime shifts (fill rate drifting up/down over minutes)
    - Cross-bracket transitions (projection crossing 100k/200k/300k boundaries)

  We detect "shift events" — moments when our fair value changes faster
  than the MM can reprice — and hit their stale quotes.

  Pacing matters: if we pick them off too aggressively, they'll widen or
  stop quoting. Take what's there without being obvious.

  We NEVER post passive quotes. Every order is a take at the resting price.
"""

import logging
import math
from typing import Optional
from collections import deque

from utcxchangelib import Side

log = logging.getLogger("META")

# ── Settlement ────────────────────────────────────────────────────────────────
SETTLEMENT_TICK = 4400
TOTAL_TICKS     = 4500   # full round (10 days × 90s × 5 ticks/s)

# ── Meta market contracts ─────────────────────────────────────────────────────
# 4 brackets on TOTAL exchange-wide fill messages by tick 4400.
# Exactly one pays out 1000; the rest pay 0. Sum of fair values = 1000.
# NOTE: symbol names may differ on exchange — update if needed.
META_SYMS = ["FILL_1", "FILL_2", "FILL_3", "FILL_4"]

META_BRACKETS: dict[str, tuple[float, float]] = {
    "FILL_1": (0,      100_000),    # < 100k fills
    "FILL_2": (100_000, 200_000),   # [100k, 200k)
    "FILL_3": (200_000, 300_000),   # [200k, 300k)
    "FILL_4": (300_000, float("inf")),  # >= 300k fills
}

# ── Projection tuning ────────────────────────────────────────────────────────
WINDOW_TICKS       = 200   # rolling window for rate estimation (200 ticks ~ 40s)
MIN_TICKS_TO_TRADE = 100   # don't trade until we have this many ticks of data

# ── Taker strategy — pick off stale MM quotes ────────────────────────────────
META_MAX_POS       = 25    # max |position| per contract
BASE_ORDER_QTY     = 3     # default take size — small enough not to spook MM
SHIFT_ORDER_QTY    = 8     # size up during detected shift events
MIN_EDGE_EARLY     = 40    # pts of edge required early (uncertain, wide threshold)
MIN_EDGE_LATE      = 10    # pts of edge required late (converged, tight threshold)
SHIFT_THRESHOLD    = 15    # FV must move this many pts to trigger a "shift event"
SHIFT_COOLDOWN     = 20    # ticks between shift-triggered trades (don't be obvious)
TAKE_COOLDOWN      = 8     # ticks between normal takes on the same contract

# ── Safety guards ────────────────────────────────────────────────────────────
FREEZE_TICK        = 4200  # stop opening new positions after this tick (200 before settlement)
MAX_NET_EXPOSURE   = 40    # max sum(|pos|) across all 4 contracts — caps total capital at risk
MAX_LOSS           = 5000  # kill switch: stop trading if estimated loss exceeds this
MIN_BOOK_QTY       = 2     # don't take quotes with less than this resting — likely bait
MAX_ORDER_SIZE     = 40    # exchange-enforced max per order
MAX_ABS_POSITION   = 200   # exchange-enforced max |position| per instrument
PROJECTION_SANITY_LO = 10_000   # projection below this is probably broken
PROJECTION_SANITY_HI = 800_000  # projection above this is probably broken


class MetaMarketStrategy:
    """
    Tracks exchange fill count in real-time and projects the total
    by tick 4400. Trades meta market contracts when our projection
    diverges from market pricing.

    Plug into bot by:
      1. Call on_trade_msg() from bot_handle_trade_msg for EVERY symbol
      2. Call on_tick(tick) from the trade loop or any tick-aware handler
      3. Call get_projection() to read the current estimate
      4. Call check_and_trade(client) from the trade loop
    """

    def __init__(self):
        # ── Fill counting ─────────────────────────────────────────────────
        self.total_fills: int = 0         # cumulative fill messages seen
        self.total_fill_qty: int = 0      # cumulative contracts filled
        self.current_tick: int = 0

        # ── Rolling window for rate estimation ────────────────────────────
        # Each entry: (tick, cumulative_fills_at_that_tick)
        self.fill_history: deque[tuple[int, int]] = deque(maxlen=2000)

        # ── Per-tick fill counts for granular analysis ────────────────────
        self.fills_per_tick: dict[int, int] = {}

        # ── Projection state ─────────────────────────────────────────────
        self.last_projection: Optional[float] = None
        self.projection_confidence: float = 0.0  # 0-1, grows with data

        # ── Shift detection ──────────────────────────────────────────────
        # Track prior fair values to detect when our model shifts faster
        # than the MM can reprice
        self.prev_fair_values: dict[str, float] = {}
        self.in_shift: bool = False           # True during a detected shift event
        self.shift_start_tick: int = 0        # when the current shift started
        self.last_shift_trade_tick: int = 0   # pacing: last shift-triggered take

        # ── Per-contract take pacing ─────────────────────────────────────
        # Don't hammer the same contract every loop — space out takes
        self.last_take_tick: dict[str, int] = {s: 0 for s in META_SYMS}

        # ── Trading stats ────────────────────────────────────────────────
        self.trade_count: int = 0
        self.shift_count: int = 0

        # ── Safety state ─────────────────────────────────────────────────
        self.killed: bool = False          # kill switch tripped — no more trading
        self.frozen: bool = False          # near settlement — only reduce positions
        self.estimated_pnl: float = 0.0    # running P&L estimate
        self.cash_spent: float = 0.0       # total cash out on buys
        self.cash_received: float = 0.0    # total cash in from sells

    # ══════════════════════════════════════════════════════════════════════════
    #  DATA COLLECTION — call these from bot event handlers
    # ══════════════════════════════════════════════════════════════════════════

    def on_trade_msg(self, symbol: str, price: int, qty: int, tick: int):
        """
        Called on EVERY bot_handle_trade_msg — this is the raw fill count.
        Each call = 1 fill message from the exchange.
        """
        self.total_fills += 1
        self.total_fill_qty += qty
        self.current_tick = max(self.current_tick, tick)

        # Record in history for windowed rate calc
        self.fill_history.append((tick, self.total_fills))

        # Per-tick granularity
        self.fills_per_tick[tick] = self.fills_per_tick.get(tick, 0) + 1

    def on_tick(self, tick: int):
        """Update current tick even if no fills happened (e.g., from book updates)."""
        self.current_tick = max(self.current_tick, tick)

    # ══════════════════════════════════════════════════════════════════════════
    #  PROJECTION — the core edge
    # ══════════════════════════════════════════════════════════════════════════

    def get_projection(self) -> Optional[float]:
        """
        Project total fills at tick 4400.

        Uses a blended approach:
          - Lifetime average rate (stable but slow to react)
          - Windowed rate (reactive but noisy)
          - Weighted blend that shifts toward windowed as we get more data

        Returns projected total fill count, or None if insufficient data.
        """
        if self.current_tick < 10 or self.total_fills < 5:
            return None

        ticks_elapsed = self.current_tick
        ticks_remaining = max(0, SETTLEMENT_TICK - ticks_elapsed)

        # If we're past settlement, projection = actual count
        if ticks_remaining <= 0:
            self.last_projection = float(self.total_fills)
            self.projection_confidence = 1.0
            return self.last_projection

        # ── Lifetime average rate ─────────────────────────────────────────
        lifetime_rate = self.total_fills / max(1, ticks_elapsed)

        # ── Windowed rate (last WINDOW_TICKS ticks) ───────────────────────
        window_rate = self._windowed_rate()

        # ── Blend: more weight on window as round progresses ──────────────
        # Early: trust lifetime (less noise). Late: trust window (more current).
        progress = min(1.0, ticks_elapsed / SETTLEMENT_TICK)
        window_weight = 0.3 + 0.5 * progress  # 0.3 early → 0.8 late
        blended_rate = (1 - window_weight) * lifetime_rate + window_weight * window_rate

        # ── Project ───────────────────────────────────────────────────────
        projected = self.total_fills + blended_rate * ticks_remaining

        # ── Confidence: higher when more data and closer to settlement ────
        data_confidence = min(1.0, self.total_fills / 100)
        time_confidence = progress
        self.projection_confidence = 0.5 * data_confidence + 0.5 * time_confidence

        self.last_projection = projected
        return projected

    def _windowed_rate(self) -> float:
        """Fills per tick over the recent window."""
        if len(self.fill_history) < 2:
            return 0.0

        latest_tick = self.fill_history[-1][0]
        latest_fills = self.fill_history[-1][1]

        # Find the entry closest to WINDOW_TICKS ago
        target_tick = latest_tick - WINDOW_TICKS
        window_start_fills = latest_fills  # fallback
        window_start_tick = latest_tick

        for tick, fills in self.fill_history:
            if tick >= target_tick:
                window_start_fills = fills
                window_start_tick = tick
                break

        tick_span = latest_tick - window_start_tick
        fill_span = latest_fills - window_start_fills

        if tick_span <= 0:
            return 0.0
        return fill_span / tick_span

    # ══════════════════════════════════════════════════════════════════════════
    #  TRADING — compare projection vs market
    # ══════════════════════════════════════════════════════════════════════════

    async def check_and_trade(self, client):
        """
        Main entry point. Called from trade loop every ~0.5s.
        All safety guards are checked here before any trading logic runs.
        """
        if not META_SYMS:
            return

        # ── Guard: kill switch ────────────────────────────────────────────
        if self.killed:
            return

        # ── Guard: check max loss ─────────────────────────────────────────
        self._update_pnl_estimate(client)
        if self.estimated_pnl < -MAX_LOSS:
            if not self.killed:
                self.killed = True
                log.warning(f"[META KILLED] estimated loss {self.estimated_pnl:.0f} "
                            f"exceeds MAX_LOSS {MAX_LOSS} — stopping all meta trading")
            return

        projection = self.get_projection()
        if projection is None:
            return
        if self.current_tick < MIN_TICKS_TO_TRADE:
            return

        # ── Guard: sanity check projection ────────────────────────────────
        if projection < PROJECTION_SANITY_LO or projection > PROJECTION_SANITY_HI:
            log.warning(f"[META SANITY] projection {projection:.0f} outside "
                        f"[{PROJECTION_SANITY_LO}, {PROJECTION_SANITY_HI}] — skipping")
            return

        # ── Guard: freeze near settlement ─────────────────────────────────
        if self.current_tick >= FREEZE_TICK:
            if not self.frozen:
                self.frozen = True
                log.info(f"[META FROZEN] tick {self.current_tick} >= {FREEZE_TICK} — "
                         f"only reducing positions from here")

        # ── Guard: net exposure cap ───────────────────────────────────────
        net_exposure = sum(abs(client.positions.get(s, 0)) for s in META_SYMS)
        if net_exposure >= MAX_NET_EXPOSURE and not self.frozen:
            log.info(f"[META EXPOSURE] net={net_exposure} >= {MAX_NET_EXPOSURE} — "
                     f"only reducing until exposure drops")

        await self._trade_brackets(client, projection)

    def _update_pnl_estimate(self, client):
        """
        Rough P&L estimate: cash flow + mark-to-market of open positions.
        Uses book mids for MTM. Not exact, but enough for a kill switch.
        """
        mtm = 0.0
        for sym in META_SYMS:
            pos = client.positions.get(sym, 0)
            if pos == 0:
                continue
            book = client.order_books.get(sym)
            if not book:
                continue
            bids = [k for k, v in book.bids.items() if v > 0] if book.bids else []
            asks = [k for k, v in book.asks.items() if v > 0] if book.asks else []
            if bids and asks:
                mid = (max(bids) + min(asks)) / 2
                mtm += pos * mid
        # Cash positions tracked by exchange in client.positions["cash"],
        # but we only care about meta-market cash flow which we track ourselves
        self.estimated_pnl = self.cash_received - self.cash_spent + mtm

    def _estimate_std(self) -> float:
        """
        Estimate standard deviation of our projection.

        Fill arrivals are roughly Poisson-like: variance ~ count.
        But fills are clustered (news spikes), so actual variance is higher.
        We use overdispersion factor that shrinks as we get more data.

        The std of the REMAINING fills scales with sqrt(ticks_remaining),
        so our total projection uncertainty shrinks as settlement approaches.
        """
        ticks_elapsed = max(1, self.current_tick)
        ticks_remaining = max(0, SETTLEMENT_TICK - ticks_elapsed)

        if ticks_remaining <= 0:
            return 0.0

        lifetime_rate = self.total_fills / ticks_elapsed
        window_rate = self._windowed_rate()
        rate_disagreement = abs(lifetime_rate - window_rate)

        # Base std from Poisson-like process on remaining fills
        expected_remaining = max(1, lifetime_rate * ticks_remaining)
        poisson_std = math.sqrt(expected_remaining)

        # Overdispersion: fills are bursty (news events create spikes).
        # At 100k-scale brackets, Poisson sqrt alone is ~300 which is tiny
        # vs 100k bracket width. Scale up significantly early on.
        progress = min(1.0, ticks_elapsed / SETTLEMENT_TICK)
        overdispersion = 8.0 - 5.0 * progress   # 8x early → 3x late

        # Rate uncertainty: if lifetime and windowed rates disagree,
        # that disagreement projected over remaining ticks is a major
        # source of uncertainty
        rate_std = rate_disagreement * ticks_remaining

        # Minimum floor: brackets are 100k wide. Early on we should have
        # std ~ 30-50k (real uncertainty). Shrinks toward settlement.
        projection = self.total_fills + lifetime_rate * ticks_remaining
        floor_pct = 0.20 - 0.18 * progress   # 20% early → 2% late
        floor_std = projection * floor_pct

        raw_std = math.sqrt((poisson_std * overdispersion) ** 2 + rate_std ** 2)
        return max(raw_std, floor_std)

    def _bracket_fair_values(self, projection: float) -> dict[str, float]:
        """
        Compute fair value (0-1000) for each bracket contract using
        a normal distribution centered on our projection.

        P(bracket) = Phi(hi) - Phi(lo)  where Phi is the CDF of
        Normal(projection, std^2).

        Returns dict of {symbol: fair_value_in_0_to_1000}.
        """
        std = self._estimate_std()
        std = max(1.0, std)  # avoid division by zero

        fair_values: dict[str, float] = {}

        for sym, (lo, hi) in META_BRACKETS.items():
            # CDF at boundaries
            if hi == float("inf"):
                p_hi = 1.0
            else:
                p_hi = 0.5 * (1 + math.erf((hi - projection) / (std * math.sqrt(2))))

            if lo <= 0:
                p_lo = 0.0
            else:
                p_lo = 0.5 * (1 + math.erf((lo - projection) / (std * math.sqrt(2))))

            prob = max(0.001, p_hi - p_lo)  # floor at 0.1% — never fully rule out
            fair_values[sym] = prob * 1000

        # Renormalize to exactly 1000 (rounding / floor adjustments)
        total = sum(fair_values.values())
        if total > 0:
            for sym in fair_values:
                fair_values[sym] = fair_values[sym] * 1000 / total

        return fair_values

    async def _trade_brackets(self, client, projection: float):
        """
        Taker-only bracket trading.

        1. Compute fair values from our projection + uncertainty model
        2. Detect shift events (our FV moved significantly since last check)
        3. Pick off stale MM quotes with pacing
        """
        fair_values = self._bracket_fair_values(projection)
        std = self._estimate_std()

        # ── Shift detection ───────────────────────────────────────────────
        # Compare current FV to previous. If any contract's FV moved more
        # than SHIFT_THRESHOLD, the MM is probably stale — size up.
        max_fv_move = 0.0
        if self.prev_fair_values:
            for sym in META_SYMS:
                old = self.prev_fair_values.get(sym, 500)
                new = fair_values.get(sym, 500)
                max_fv_move = max(max_fv_move, abs(new - old))

        if max_fv_move >= SHIFT_THRESHOLD:
            if not self.in_shift:
                self.in_shift = True
                self.shift_start_tick = self.current_tick
                self.shift_count += 1
                log.info(f"[META SHIFT] FV moved {max_fv_move:.0f}pts — "
                         f"looking for stale MM quotes")
        else:
            # End shift after cooldown expires
            if self.in_shift and (self.current_tick - self.shift_start_tick > SHIFT_COOLDOWN):
                self.in_shift = False

        self.prev_fair_values = dict(fair_values)

        # ── Edge threshold: scales with confidence ────────────────────────
        # Early round: high uncertainty, need large edge to justify take
        # Late round: converged, even small edge is reliable
        progress = min(1.0, self.current_tick / SETTLEMENT_TICK)
        min_edge = MIN_EDGE_EARLY + (MIN_EDGE_LATE - MIN_EDGE_EARLY) * progress

        # During shift events, reduce threshold — MM is probably stale
        if self.in_shift:
            min_edge *= 0.6

        # ── Log current state ─────────────────────────────────────────────
        shift_str = "SHIFT" if self.in_shift else "calm"
        log.info(
            f"[META] proj={projection:.0f} std={std:.0f} edge>={min_edge:.0f} "
            f"[{shift_str}] | "
            + " ".join(f"{s}={fair_values[s]:.0f}" for s in META_SYMS)
        )

        # ── Scan all contracts for takeable quotes ────────────────────────
        for sym in META_SYMS:
            if sym not in fair_values:
                continue
            await self._take_if_stale(client, sym, fair_values[sym], min_edge)

    async def _take_if_stale(self, client, sym: str, fair: float, min_edge: float):
        """
        Check if the MM's resting quote on `sym` is stale (edge > min_edge).
        If so, take it. All safety guards applied before placing any order.
        """
        # ── Pacing: don't hammer the same contract ────────────────────────
        ticks_since_last = self.current_tick - self.last_take_tick.get(sym, 0)
        if self.in_shift:
            if ticks_since_last < SHIFT_COOLDOWN:
                return
        else:
            if ticks_since_last < TAKE_COOLDOWN:
                return

        book = client.order_books.get(sym)
        if not book:
            return

        bids = [k for k, v in book.bids.items() if v > 0] if book.bids else []
        asks = [k for k, v in book.asks.items() if v > 0] if book.asks else []

        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None
        pos = client.positions.get(sym, 0)

        # ── Guard: exchange position limit ────────────────────────────────
        if abs(pos) >= MAX_ABS_POSITION:
            return

        # ── Guard: net exposure cap (across all 4 contracts) ──────────────
        net_exposure = sum(abs(client.positions.get(s, 0)) for s in META_SYMS)
        exposure_allows_increase = net_exposure < MAX_NET_EXPOSURE

        # ── Size: bigger during shift events, capped by exchange limit ────
        base_qty = SHIFT_ORDER_QTY if self.in_shift else BASE_ORDER_QTY
        base_qty = min(base_qty, MAX_ORDER_SIZE)

        # ── Buy: market ask is cheap vs our fair value ────────────────────
        if best_ask is not None:
            edge = fair - best_ask

            # Guard: check resting depth — don't take thin bait quotes
            ask_qty_at_best = book.asks.get(best_ask, 0)
            if ask_qty_at_best < MIN_BOOK_QTY:
                edge = 0  # skip — too thin, likely bait or stale remnant

            if edge > min_edge:
                is_reducing = pos < 0  # buying reduces a short

                # Guard: freeze mode — only allow trades that reduce position
                if self.frozen and not is_reducing:
                    pass  # skip — can't increase position near settlement
                # Guard: exposure cap — only allow if reducing or under cap
                elif not exposure_allows_increase and not is_reducing:
                    pass  # skip — exposure too high
                else:
                    qty = min(base_qty, META_MAX_POS - pos, MAX_ABS_POSITION - pos)
                    qty = max(0, qty)
                    if qty > 0:
                        try:
                            await client.place_order(sym, qty, Side.BUY, best_ask)
                            self.trade_count += 1
                            self.last_take_tick[sym] = self.current_tick
                            self.cash_spent += qty * best_ask
                            log.info(f"[META BUY] {sym} {qty}@{best_ask} "
                                     f"fair={fair:.0f} edge={edge:.0f}"
                                     f"{' SHIFT' if self.in_shift else ''}"
                                     f"{' FREEZE-REDUCE' if self.frozen else ''}")
                        except Exception as e:
                            log.warning(f"[META ORDER ERR] {e}")

        # ── Sell: market bid is expensive vs our fair value ───────────────
        if best_bid is not None:
            edge = best_bid - fair

            # Guard: check resting depth
            bid_qty_at_best = book.bids.get(best_bid, 0)
            if bid_qty_at_best < MIN_BOOK_QTY:
                edge = 0  # skip — too thin

            if edge > min_edge:
                is_reducing = pos > 0  # selling reduces a long

                # Guard: freeze mode
                if self.frozen and not is_reducing:
                    pass
                # Guard: exposure cap
                elif not exposure_allows_increase and not is_reducing:
                    pass
                else:
                    qty = min(base_qty, META_MAX_POS + pos, MAX_ABS_POSITION + pos)
                    qty = max(0, qty)
                    if qty > 0:
                        try:
                            await client.place_order(sym, qty, Side.SELL, best_bid)
                            self.trade_count += 1
                            self.last_take_tick[sym] = self.current_tick
                            self.cash_received += qty * best_bid
                            log.info(f"[META SELL] {sym} {qty}@{best_bid} "
                                     f"fair={fair:.0f} edge={edge:.0f}"
                                     f"{' SHIFT' if self.in_shift else ''}"
                                     f"{' FREEZE-REDUCE' if self.frozen else ''}")
                        except Exception as e:
                            log.warning(f"[META ORDER ERR] {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  STATUS / DEBUG
    # ══════════════════════════════════════════════════════════════════════════

    def status_line(self) -> str:
        proj = self.last_projection
        rate = self._windowed_rate()
        mode = "KILLED" if self.killed else "FROZEN" if self.frozen else \
               "SHIFT" if self.in_shift else "calm"
        return (
            f"fills={self.total_fills} tick={self.current_tick} "
            f"rate={rate:.2f}/tick "
            f"proj={proj:.0f if proj else '?'} "
            f"conf={self.projection_confidence:.2f} "
            f"[{mode}] takes={self.trade_count} shifts={self.shift_count} "
            f"pnl={self.estimated_pnl:+.0f}"
        )

    def fills_remaining_estimate(self) -> Optional[float]:
        """How many more fills we expect between now and tick 4400."""
        proj = self.get_projection()
        if proj is None:
            return None
        return max(0, proj - self.total_fills)
