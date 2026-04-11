# prediction_market.py — Fed Rate Prediction Market Strategy
"""
Instruments : R_CUT, R_HOLD, R_HIKE
Payout      : 1000 for the winning contract, 0 for losers.  Exactly one wins.
Invariant   : fair prices always sum to exactly 1000.

Strategy priority (3 and 4 are primary):
  3. Market making    — continuous quotes around probability × 1000, inventory-skewed,
                         with sum-constraint enforcement so we never arb against ourselves.
  4. Cross-asset      — PM probabilities → E[Δr] → yield → C fair value → trade C when
                         the two markets disagree.
  1. Sum arbitrage    — buy/sell all three when sum of asks < 1000 or sum of bids > 1000.
  2. CPI sprint       — immediate directional sweep on every CPI print (highest α/event).
"""

import math
import re
import logging
from typing import Optional
from utcxchangelib import Side

log = logging.getLogger("PM")

# ── Contract names ────────────────────────────────────────────────────────────
PM_SYMS    = ["R_CUT", "R_HOLD", "R_HIKE"]
RESOLUTION = 1000   # payout on winning contract (and the no-arb sum target)

# ── Position limits (fill from Ed post before competition) ───────────────────
PM_MAX_POS      = 50   # max |position| per contract
PM_ARB_SIZE     = 5    # contracts per arb leg
PM_MM_SIZE      = 3    # contracts per passive MM quote
PM_DIR_SIZE     = 10   # contracts per CPI / news directional sweep

# ── Market-making spread (half-spread in price units, 0-1000 scale) ──────────
MM_SPREAD_BASE  = 12   # tight half-spread when calm
MM_SPREAD_WIDE  = 35   # wide half-spread immediately after news
MM_WIDE_DECAY   = 50   # ticks until spread fully collapses to base
SKEW_PER_UNIT   = 0.6  # bid/ask shift per unit of inventory

# ── Sum-arb entry threshold ───────────────────────────────────────────────────
ARB_MIN_PROFIT  = 6    # minimum locked profit (after assuming zero tx cost)

# ── CPI signal calibration ────────────────────────────────────────────────────
# A surprise of +0.001 (0.1 CPI pt) shifts R_HIKE fair value this many points.
CPI_SHIFT_SCALE = 50_000   # 0.001 × 50_000 = 50-pt shift in [0,1000]
CPI_SHIFT_CAP   = 180      # never shift more than this in one print
CPI_MIN_SURPRISE = 0.0001  # ignore noise below this

# ── Smart-money detection ─────────────────────────────────────────────────────
SMART_FILL_THRESHOLD = 5   # fill size that suggests informed flow
SMART_FILL_SHIFT     = 4.0 # probability nudge per smart fill (price units)

# ── Unstructured news keyword regex ──────────────────────────────────────────
_HAWKISH_RE = re.compile(
    r"inflation|inflationary|overheat|above.target|price.pressure|"
    r"tight.labor|job.growth|strong.employ|hawkish|rate.hike|tighten|"
    r"resilient|robust|wage.growth|hot.econom|above.expectation",
    re.IGNORECASE,
)
_DOVISH_RE = re.compile(
    r"recession|slowdown|below.target|cooling|unemployment|layoff|"
    r"weak.growth|dovish|rate.cut|easing|contraction|slowing|"
    r"job.loss|deflat|soft.landing|labor.market.weak",
    re.IGNORECASE,
)
NEWS_SHIFT_PER_HIT = 7.0   # probability nudge per keyword match


class FedProbabilityModel:
    """
    Maintains fair-value estimates for R_CUT, R_HOLD, R_HIKE
    in the prediction market's native 0-1000 price unit scale.

    fv["R_HIKE"] = 220  →  market-implied P(hike) = 22%.

    Sources (blended):
      1. Book mids        — real-time market consensus (weighted highest)
      2. CPI prints       — hard Bayesian-ish updates on inflation data
      3. Unstructured news — soft keyword-driven shifts
    """

    def __init__(self):
        # Uniform prior
        self.fv: dict[str, float] = {
            "R_CUT":  333.0,
            "R_HOLD": 334.0,
            "R_HIKE": 333.0,
        }
        self.ticks_since_news: int = MM_WIDE_DECAY + 1  # start at calm spread

    # ── Getters ───────────────────────────────────────────────────────────────

    @property
    def q_hike(self) -> float:
        return self.fv["R_HIKE"] / RESOLUTION

    @property
    def q_hold(self) -> float:
        return self.fv["R_HOLD"] / RESOLUTION

    @property
    def q_cut(self) -> float:
        return self.fv["R_CUT"] / RESOLUTION

    @property
    def e_delta_r(self) -> float:
        """Expected rate change in basis points."""
        return 25.0 * self.q_hike + 0.0 * self.q_hold + (-25.0) * self.q_cut

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _normalise(self):
        """Clamp each FV to [1,999] and re-scale so sum == 1000 exactly."""
        for k in self.fv:
            self.fv[k] = max(1.0, min(999.0, self.fv[k]))
        total = sum(self.fv.values())
        if total > 0:
            scale = RESOLUTION / total
            for k in self.fv:
                self.fv[k] *= scale

    def _shift_toward(self, buy_sym: str, amount: float):
        """
        Increase fv[buy_sym] by `amount`, proportionally decrease the others.
        """
        others = [s for s in PM_SYMS if s != buy_sym]
        total_others = sum(self.fv[s] for s in others)
        self.fv[buy_sym] += amount
        if total_others > 0:
            for s in others:
                self.fv[s] -= amount * (self.fv[s] / total_others)
        self._normalise()

    # ── Update from market ────────────────────────────────────────────────────

    def blend_with_book(self, mids: dict[str, float]):
        """
        Blend our model with book mids (60 % market, 40 % model).
        Only update contracts that have a valid two-sided book.
        """
        for sym in PM_SYMS:
            m = mids.get(sym)
            if m and 0 < m < RESOLUTION:
                self.fv[sym] = 0.60 * m + 0.40 * self.fv[sym]
        self._normalise()

    # ── Update from CPI ───────────────────────────────────────────────────────

    def on_cpi(self, forecast: float, actual: float):
        """
        Hard probability shift on CPI surprise.
        actual > forecast  → hawkish → R_HIKE up, R_CUT down
        actual < forecast  → dovish  → R_CUT up, R_HIKE down
        Returns (direction_str, shift_amount) for logging.
        """
        surprise = actual - forecast
        if abs(surprise) < CPI_MIN_SURPRISE:
            return None, 0.0

        shift = min(CPI_SHIFT_CAP, abs(surprise) * CPI_SHIFT_SCALE)
        if surprise > 0:
            self._shift_toward("R_HIKE", shift)
            direction = "HAWKISH"
        else:
            self._shift_toward("R_CUT", shift)
            direction = "DOVISH"

        self.ticks_since_news = 0
        log.info(f"[CPI {direction}] surprise={surprise:+.5f} shift={shift:.1f} | "
                 f"fv: cut={self.fv['R_CUT']:.0f} hold={self.fv['R_HOLD']:.0f} "
                 f"hike={self.fv['R_HIKE']:.0f} | E[Δr]={self.e_delta_r:.2f}bps")
        return direction, shift

    # ── Update from unstructured news ─────────────────────────────────────────

    def on_news(self, headline: str) -> int:
        """
        Soft keyword-based probability shift.
        Returns net hawkish score (positive = hawkish, negative = dovish).
        """
        h = len(_HAWKISH_RE.findall(headline))
        d = len(_DOVISH_RE.findall(headline))
        net = h - d
        if net == 0:
            return 0

        shift = NEWS_SHIFT_PER_HIT * abs(net)
        if net > 0:
            self._shift_toward("R_HIKE", shift)
        else:
            self._shift_toward("R_CUT", shift)

        self.ticks_since_news = max(0, self.ticks_since_news - 10)
        log.info(f"[NEWS {'HAWK' if net>0 else 'DOVE'} net={net:+d}] shift={shift:.1f} | "
                 f"{headline[:90]}")
        return net

    # ── Update from smart fills ───────────────────────────────────────────────

    def on_public_trade(self, symbol: str, qty: int):
        """Large public fills hint at informed flow; nudge probability."""
        if symbol not in PM_SYMS or qty < SMART_FILL_THRESHOLD:
            return
        self._shift_toward(symbol, SMART_FILL_SHIFT * (qty / SMART_FILL_THRESHOLD))
        log.debug(f"[SMART] {symbol} qty={qty} → nudge {SMART_FILL_SHIFT:.1f}")


# ─────────────────────────────────────────────────────────────────────────────

class PredictionMarketStrategy:
    """
    Full prediction market trading strategy.

    Plug into the main bot by passing `self` (the XChangeClient) as `client`.
    Required client attributes / methods:
      client.order_books           — {sym: book}  (book.bids, book.asks)
      client.positions             — {sym: int}
      client.open_orders           — {oid: (order_obj, ...)}
      client.place_order(sym, qty, side, price)   — async
      client.cancel_order(oid)                    — async
    """

    def __init__(self, client):
        self.client = client
        self.model  = FedProbabilityModel()

        self.arb_count   = 0
        self.cpi_count   = 0
        self.mm_refresh  = 0

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS — book access
    # ══════════════════════════════════════════════════════════════════════════

    def _best_bid(self, sym: str) -> Optional[int]:
        book = self.client.order_books.get(sym)
        if not book or not book.bids:
            return None
        bids = [k for k, v in book.bids.items() if v > 0]
        return max(bids) if bids else None

    def _best_ask(self, sym: str) -> Optional[int]:
        book = self.client.order_books.get(sym)
        if not book or not book.asks:
            return None
        asks = [k for k, v in book.asks.items() if v > 0]
        return min(asks) if asks else None

    def _mid(self, sym: str) -> Optional[float]:
        b, a = self._best_bid(sym), self._best_ask(sym)
        return (b + a) / 2 if b and a else None

    def _pos(self, sym: str) -> int:
        return self.client.positions.get(sym, 0)

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS — order placement
    # ══════════════════════════════════════════════════════════════════════════

    async def _place(self, sym: str, qty: int, side: Side, price: int):
        price = max(1, min(int(price), RESOLUTION - 1))
        qty   = max(1, int(qty))
        try:
            await self.client.place_order(sym, qty, side, price)
        except Exception as e:
            log.warning(f"[ORDER] {sym} {side.name} {qty}@{price}: {e}")

    async def _cancel_all_pm(self):
        """Cancel every open order on any PM contract."""
        to_cancel = [
            oid for oid, info in list(self.client.open_orders.items())
            if info[0].symbol in PM_SYMS
        ]
        for oid in to_cancel:
            try:
                await self.client.cancel_order(oid)
            except Exception:
                pass

    async def _cancel_sym(self, sym: str):
        to_cancel = [
            oid for oid, info in list(self.client.open_orders.items())
            if info[0].symbol == sym
        ]
        for oid in to_cancel:
            try:
                await self.client.cancel_order(oid)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 1 — SUM ARBITRAGE
    # ══════════════════════════════════════════════════════════════════════════

    async def check_sum_arb(self):
        """
        If sum(asks) < 1000 → buy all three (guaranteed 1000 at settlement).
        If sum(bids) > 1000 → sell all three (guaranteed receipt > 1000).
        """
        bids = {s: self._best_bid(s) for s in PM_SYMS}
        asks = {s: self._best_ask(s) for s in PM_SYMS}
        pos  = {s: self._pos(s) for s in PM_SYMS}

        # ── Buy all three ──
        if all(asks[s] for s in PM_SYMS):
            sum_ask = sum(asks[s] for s in PM_SYMS)
            profit  = RESOLUTION - sum_ask
            if profit >= ARB_MIN_PROFIT:
                room = [PM_MAX_POS - pos[s] for s in PM_SYMS]
                qty  = min(PM_ARB_SIZE, *room)
                if qty > 0:
                    log.info(f"[ARB↑] sum_ask={sum_ask} locked_profit={profit} qty={qty}")
                    for sym in PM_SYMS:
                        await self._place(sym, qty, Side.BUY, asks[sym])
                    self.arb_count += 1

        # ── Sell all three ──
        if all(bids[s] for s in PM_SYMS):
            sum_bid = sum(bids[s] for s in PM_SYMS)
            profit  = sum_bid - RESOLUTION
            if profit >= ARB_MIN_PROFIT:
                room = [PM_MAX_POS + pos[s] for s in PM_SYMS]
                qty  = min(PM_ARB_SIZE, *room)
                if qty > 0:
                    log.info(f"[ARB↓] sum_bid={sum_bid} locked_profit={profit} qty={qty}")
                    for sym in PM_SYMS:
                        await self._place(sym, qty, Side.SELL, bids[sym])
                    self.arb_count += 1

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 2 — CPI SPRINT (called from bot_handle_news)
    # ══════════════════════════════════════════════════════════════════════════

    async def on_cpi(self, forecast: float, actual: float):
        """
        1. Update probability model.
        2. Immediately sweep the market in the implied direction.
        Called directly from bot_handle_news — must be awaited.
        """
        direction, shift = self.model.on_cpi(forecast, actual)
        if direction is None:
            return

        # Cancel stale PM quotes before sweeping (avoid self-trading)
        await self._cancel_all_pm()

        surprise = actual - forecast
        if surprise > 0:
            # Hawkish: buy R_HIKE aggressively, dump R_CUT
            await self._sweep_buy("R_HIKE", PM_DIR_SIZE)
            await self._sweep_sell("R_CUT",  PM_DIR_SIZE)
        else:
            # Dovish: buy R_CUT aggressively, dump R_HIKE
            await self._sweep_buy("R_CUT",  PM_DIR_SIZE)
            await self._sweep_sell("R_HIKE", PM_DIR_SIZE)

        self.cpi_count += 1

    async def _sweep_buy(self, sym: str, qty: int):
        ask = self._best_ask(sym)
        if not ask:
            return
        room = PM_MAX_POS - self._pos(sym)
        qty  = min(qty, room)
        if qty > 0:
            await self._place(sym, qty, Side.BUY, ask)

    async def _sweep_sell(self, sym: str, qty: int):
        bid = self._best_bid(sym)
        if not bid:
            return
        room = PM_MAX_POS + self._pos(sym)
        qty  = min(qty, room)
        if qty > 0:
            await self._place(sym, qty, Side.SELL, bid)

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 3 — MARKET MAKING (primary)
    # ══════════════════════════════════════════════════════════════════════════

    def _current_half_spread(self) -> float:
        """Decay from wide to base spread as ticks_since_news grows."""
        t = self.model.ticks_since_news
        frac = min(1.0, t / MM_WIDE_DECAY)
        return MM_SPREAD_WIDE + frac * (MM_SPREAD_BASE - MM_SPREAD_WIDE)

    async def refresh_quotes(self):
        """
        Primary market-making routine.

        For each contract:
          - Compute ideal bid / ask around fair value with inventory skew.
          - Enforce the sum constraint: sum(asks) >= 1000, sum(bids) <= 1000.
          - Cancel stale orders, post fresh ones.
        """
        self.model.ticks_since_news += 1
        hs = self._current_half_spread()
        fv = self.model.fv

        # ── Compute ideal quotes ──────────────────────────────────────────────
        bids: dict[str, float] = {}
        asks: dict[str, float] = {}
        for sym in PM_SYMS:
            pos    = self._pos(sym)
            skew   = -pos * SKEW_PER_UNIT   # long→shade down, short→shade up
            centre = fv[sym] + skew
            bids[sym] = centre - hs
            asks[sym] = centre + hs

        # ── Enforce sum constraints (critical — prevents arb against ourselves) ──
        #
        # Invariant A: sum(asks) >= RESOLUTION
        #   If violated, someone can buy all three and lock in 1000 profit at our expense.
        sum_asks = sum(asks.values())
        if sum_asks < RESOLUTION:
            deficit = RESOLUTION - sum_asks + 1   # +1 buffer
            bump    = deficit / len(PM_SYMS)
            for sym in PM_SYMS:
                asks[sym] += bump

        # Invariant B: sum(bids) <= RESOLUTION
        #   If violated, someone can sell all three and collect > 1000 guaranteed.
        sum_bids = sum(bids.values())
        if sum_bids > RESOLUTION:
            excess  = sum_bids - RESOLUTION + 1
            trim    = excess / len(PM_SYMS)
            for sym in PM_SYMS:
                bids[sym] -= trim

        # ── Cancel old orders, post new ones ─────────────────────────────────
        for sym in PM_SYMS:
            await self._cancel_sym(sym)

            bid_px = int(max(1, min(bids[sym], RESOLUTION - 2)))
            ask_px = int(max(bid_px + 1, min(asks[sym], RESOLUTION - 1)))
            pos    = self._pos(sym)

            if pos < PM_MAX_POS:
                await self._place(sym, PM_MM_SIZE, Side.BUY,  bid_px)
            if pos > -PM_MAX_POS:
                await self._place(sym, PM_MM_SIZE, Side.SELL, ask_px)

        self.mm_refresh += 1

    # ══════════════════════════════════════════════════════════════════════════
    #  PUBLIC INTERFACE
    # ══════════════════════════════════════════════════════════════════════════

    def update_from_market(self):
        """Blend probability model with current book mids. Call every loop tick."""
        mids = {s: self._mid(s) for s in PM_SYMS}
        self.model.blend_with_book({s: m for s, m in mids.items() if m})

    async def on_news_headline(self, headline: str):
        """Update model from unstructured headline. Optionally trade."""
        net = self.model.on_news(headline)
        if abs(net) >= 2:
            # Strong signal — take a small directional position
            if net > 0:
                await self._sweep_buy("R_HIKE", 2)
                await self._sweep_sell("R_CUT",  2)
            else:
                await self._sweep_buy("R_CUT",  2)
                await self._sweep_sell("R_HIKE", 2)
            self.model.ticks_since_news = 0

    async def on_market_resolved(self, winning_sym: str):
        """Round ended — cancel all PM orders, flatten if needed."""
        await self._cancel_all_pm()
        log.info(f"[RESOLVED] Winner: {winning_sym} | "
                 f"pos: {self.status_positions()}")

    async def cancel_all(self):
        await self._cancel_all_pm()

    # ══════════════════════════════════════════════════════════════════════════
    #  STATUS
    # ══════════════════════════════════════════════════════════════════════════

    def status_positions(self) -> str:
        return " ".join(f"{s}={self._pos(s):+d}" for s in PM_SYMS)

    def status_line(self) -> str:
        fv = self.model.fv
        bids = {s: self._best_bid(s) for s in PM_SYMS}
        asks = {s: self._best_ask(s) for s in PM_SYMS}
        sa = (sum(a for a in asks.values() if a)
              if all(asks.values()) else None)
        sb = (sum(b for b in bids.values() if b)
              if all(bids.values()) else None)
        return (
            f"FV cut={fv['R_CUT']:.0f} hold={fv['R_HOLD']:.0f} "
            f"hike={fv['R_HIKE']:.0f} E[Δr]={self.model.e_delta_r:.1f}bps | "
            f"pos {self.status_positions()} | "
            f"sum_ask={sa} sum_bid={sb} | "
            f"arbs={self.arb_count} cpi={self.cpi_count} mm_refs={self.mm_refresh}"
        )
