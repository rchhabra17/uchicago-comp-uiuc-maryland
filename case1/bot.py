"""
bot.py — Prediction Market: Sum Arb + Aggressive News/CPI (NO Market Making)

Why no MM: Prediction markets TREND toward binary outcomes (0 or 1000).
Every MM fill is adverse selection — we buy at 340, market goes to 300.
The 12pt spread doesn't cover the 40pt directional move we eat.
MM works on mean-reverting instruments. PM contracts don't mean-revert.

What makes money:
1. Sum arb — truly risk-free locked profit
2. CPI — strong informational edge, size up aggressively
3. News — directional sweep on T1/T2 headlines
4. Position flattening — unwind inventory toward zero before settlement
"""

import re
import asyncio
import socket
import logging
from typing import Optional

from utcxchangelib import XChangeClient, Side

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("BOT")

# ── Exchange ───────────────────────────────────────────────────────────────────
SERVER   = "34.197.188.76"
USERNAME = "maryland_uiuc"
PASSWORD = "torch-karma-beacon"

# ── Risk limits (from Ed post) ────────────────────────────────────────────────
MAX_ORDER_SIZE         = 40
MAX_ABSOLUTE_POSITION  = 200

# ── Prediction market ──────────────────────────────────────────────────────────
PM_SYMS    = ["R_CUT", "R_HOLD", "R_HIKE"]
RESOLUTION = 1000

# ── Sum Arb ───────────────────────────────────────────────────────────────────
ARB_MIN_PROFIT = 4
ARB_SIZE       = 5       # smaller — partial fills create unhedged exposure
ARB_COOLDOWN   = 5
ARB_SKIP_BELOW = 30      # wider skip — illiquid legs don't fill
ARB_MAX_NET    = 10      # max NET position from arb across all contracts

# ── CPI sweep ─────────────────────────────────────────────────────────────────
CPI_BUY_BASE     = 15    # aggressive — CPI is our strongest edge
CPI_SELL_BASE    = 10
CPI_MIN_SURPRISE = 0.0001

# ── News sweep ────────────────────────────────────────────────────────────────
NEWS_BUY_SIZE   = {1: 12,   2: 6,    3: 0}
NEWS_SELL_SIZE  = {1: 8,    2: 4,    3: 0}

# ── Market-follow after ANY news ──────────────────────────────────────────────
# After any news (even unparsed), snapshot mids and watch for the first
# significant move. Follow it — the market's reaction IS the signal.
FOLLOW_WINDOW    = 0.6   # seconds to watch after news (~3 ticks)
FOLLOW_THRESHOLD = 8     # min pts a contract must move to trigger
FOLLOW_QTY       = 6     # contracts per follow trade

# ── Position management ──────────────────────────────────────────────────────
PM_SOFT_LIMIT   = 25     # tighter — never get stuck with huge positions

TRADE_INTERVAL  = 0.5

# ── News regexes ──────────────────────────────────────────────────────────────
# ── T1: Direct, unambiguous Fed language ──────────────────────────────────────
_HAWK_T1 = re.compile(
    r"\brate\s+hike\b|\bhike\s+(?:rates?|interest)\b|"
    r"\b(?:raises?|raised|raising)\s+(?:rates?|interest\s+rates?)\b|"
    r"\bfomc\s+(?:hike|raise|increase)\b|"
    r"\bhawkish\s+(?:pivot|turn|shift|stance)\b|"
    r"\b\d+\s*(?:basis\s*points?|bps?)\s+(?:rate\s+)?hike\b|"
    r"\bhike\b.{0,20}\brates?\b|"
    r"\btighten(?:ing)?\s+(?:monetary|policy|stance)\b|"
    r"\brestrictive\s+(?:policy|stance|posture)\b(?!.{0,20}(?:no longer|not\s+needed|unnecessary|unwarranted))|"
    r"\bpremature\s+easing\b|"                             # "warn premature easing" = hawk
    r"\breignite\b.{0,20}\b(?:inflation|price|pressure)\b|"
    r"\bpolicy\s+will\s+stay\s+firm\b",
    re.IGNORECASE,
)
_DOVE_T1 = re.compile(
    r"\brate\s+cut\b|\bcut\s+(?:rates?|interest)\b|"
    r"\b(?:lowers?|lowered|lowering|reduces?)\s+(?:rates?|interest\s+rates?)\b|"
    r"\bfomc\s+(?:cut|lower|decrease)\b|"
    r"\bdovish\s+(?:pivot|turn|shift|stance)\b|"
    r"\b\d+\s*(?:basis\s*points?|bps?)\s+(?:rate\s+)?cut\b|"
    r"\bcut\b.{0,20}\brates?\b|"
    r"\bpreemptive\s+cut\b|"
    r"\beas(?:e|ing)\s+(?:monetary|policy|stance)\b|"
    r"\b(?:monetary|policy)\s+eas(?:e|ing)\b|"            # "policy easing" (reversed order)
    r"\baccommodat(?:ion|ive|ing)\b|"
    r"\bloos(?:en|er|ening)\s+(?:monetary|policy)\b|"
    r"\brate\s+relief\b|"
    r"\brestrictive.{0,20}no\s+longer\b|"                 # "restrictive stance may no longer be needed"
    r"\bno\s+longer.{0,10}restrictive\b",
    re.IGNORECASE,
)

# Context words that NEGATE dovish T1 matches (e.g. "warn...easing could reignite")
_ANTI_DOVE = re.compile(
    r"\bwarn|premature\s+easing|reignite|too\s+(?:early|soon)|risk.{0,15}easing",
    re.IGNORECASE,
)

# ── T2: Macro data signals ───────────────────────────────────────────────────
_HAWK_T2 = re.compile(
    r"inflation\s+(?:above|surge|spike|jump|rise|elevated|persist|high|accelerat)|"
    r"(?:cpi|pce|ppi)\s+(?:above|beat|exceed|hot|surge)|"
    r"above\s+(?:target|expectation|forecast)|"
    r"(?:labor|job)\s+market\s+(?:tight|strong|hot)|"
    r"unemployment\s+(?:fall|drop|low|decline|near.{0,10}low|hit.{0,10}low)|"
    r"(?:strong|robust|solid)\s+(?:jobs?|payroll|nfp|employment)|"
    r"(?:low|record.low|historic.{0,5}low)\s+unemployment|"
    r"jobless\s+claims?\s+(?:fall|drop|decline|low|plunge)|"
    r"hiring\s+(?:surge|boom|strong|accelerat|jump)|"
    r"(?:jobs?|employment)\s+(?:surge|boom|soar|jump)|"
    r"wage\s+(?:growth|pressure|rise|inflation)\b(?!.{0,15}(?:decelerat|slow|cool|eas|fall|declin))|"
    r"overheating|price\s+pressure",
    re.IGNORECASE,
)
_DOVE_T2 = re.compile(
    r"recession|contraction|gdp\s+(?:fall|drop|decline|shrink|negative)|"
    r"inflation\s+(?:below|cool|ease|slow|fall|drop|decline|miss)|"
    r"(?:cpi|pce|ppi)\s+(?:below|miss|cool|ease|soft)|"
    r"below\s+(?:target|expectation|forecast)|"
    r"unemployment\s+(?:rise|spike|jump|high|climb|surge|soar|hit.{0,10}high)|"
    r"(?:high|record.high|historic.{0,5}high|rising)\s+unemployment|"
    r"jobless\s+claims?\s+(?:rise|surge|spike|jump|soar|high)|"
    r"(?:job|payroll)\s+(?:loss|decline|weak|miss|cut)|"
    r"layoff|slowdown|deflation|weak\s+(?:growth|demand|consumer)|"
    r"wage.{0,15}(?:decelerat|slow|cool|eas|fall|declin)|"  # "wage growth decelerating"
    r"disinflation",
    re.IGNORECASE,
)

# ── T3: Soft/contextual signals ──────────────────────────────────────────────
_HAWK_T3 = re.compile(
    r"resilient|robust\s+growth|consumer\s+(?:spending|confidence)\s+(?:up|strong|rise)|"
    r"inflationary|hawkish|tighten", re.IGNORECASE,
)
_DOVE_T3 = re.compile(
    r"headwind|uncertainty|cooling(?!\s+inflation)|\bdovish\b|"
    r"easing\s+(?:inflation|pressure)|soft\s+landing|below\s+expectation|"
    r"safeguard\s+growth|protect\s+growth|support\s+growth",
    re.IGNORECASE,
)

# Negation words that flip T2 hawk signals to dove
_NEGATION = re.compile(
    r"\b(?:decelerat|slow(?:ing|ed|s)?|cool(?:ing|ed|s)?|eas(?:ing|ed|es)|"
    r"fall(?:ing|s)?|declin(?:ing|ed|es)?|drop(?:ping|ped|s)?|weak(?:en|ing|ed)?|"
    r"fad(?:ing|ed|es)?|moder(?:at|ating|ated))\b",
    re.IGNORECASE,
)


class PMBot(XChangeClient):

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password)
        self.current_tick = 0
        self.loop_count   = 0
        self.arb_cooldown = 0

        self.arb_pending: set[str] = set()
        self.arb_busy = False

        # Market-follow state: after news, watch for first significant move
        import time as _time
        self._time = _time
        self.follow_snapshot: dict[str, float] = {}  # mids at news time
        self.follow_deadline: float = 0.0
        self.follow_fired: bool = True  # True = no active window

        # Stale order tracking — cancel directional orders after timeout
        self.directional_orders: list[tuple[str, float]] = []  # (oid, expiry_time)
        self.news_traded_syms: set[str] = set()  # syms traded this news cycle (prevent follow pileup)

        # P&L tracking
        self.pnl = {"arb": 0, "cpi": 0, "news": 0, "follow": 0, "total": 0}
        self.fill_source: dict[str, str] = {}

    async def start(self):
        self.trade_task = asyncio.create_task(self.trade_loop())
        try:
            await self.connect()
            log.info("Connected")
        except Exception as e:
            log.error(f"Connection failed: {e}")
        await self.trade_task

    # ── Book helpers ───────────────────────────────────────────────────────────

    def _best_bid(self, sym: str) -> Optional[int]:
        book = self.order_books.get(sym)
        if not book:
            return None
        bids = [k for k, v in book.bids.items() if v > 0]
        return max(bids) if bids else None

    def _best_ask(self, sym: str) -> Optional[int]:
        book = self.order_books.get(sym)
        if not book:
            return None
        asks = [k for k, v in book.asks.items() if v > 0]
        return min(asks) if asks else None

    def _mid(self, sym: str) -> Optional[float]:
        b, a = self._best_bid(sym), self._best_ask(sym)
        return (b + a) / 2 if b and a else None

    def _pos(self, sym: str) -> int:
        return self.positions.get(sym, 0)

    # ── Order helper ──────────────────────────────────────────────────────────

    async def _place(self, sym: str, qty: int, side: Side, price: int, tag: str = ""):
        curr = self._pos(sym)
        if side == Side.BUY and curr >= PM_SOFT_LIMIT:
            return None
        if side == Side.SELL and curr <= -PM_SOFT_LIMIT:
            return None
        qty = min(qty, MAX_ORDER_SIZE)
        if qty <= 0:
            return None
        try:
            oid = await self.place_order(sym, qty, side, max(1, int(price)))
            oid_str = str(oid)
            if tag:
                self.fill_source[oid_str] = tag
            if tag == "arb":
                self.arb_pending.add(oid_str)
            # Track directional orders for auto-cancel after 2s
            if tag in ("cpi", "news", "follow"):
                self.directional_orders.append((oid_str, self._time.time() + 2.0))
            return oid_str
        except Exception as e:
            log.warning(f"[ORDER ERR] {sym} {side.name} {qty}@{price}: {e}")
            return None

    async def _cancel_stale_directional(self):
        """Cancel directional orders older than 2s — if they didn't fill, the edge is gone."""
        now = self._time.time()
        still_live = []
        for oid, expiry in self.directional_orders:
            if now >= expiry and oid in self.open_orders:
                try:
                    await self.cancel_order(oid)
                except Exception:
                    pass
            elif oid in self.open_orders:
                still_live.append((oid, expiry))
        self.directional_orders = still_live

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 1: SUM ARB
    # ══════════════════════════════════════════════════════════════════════════

    async def _cancel_arb(self):
        for oid in list(self.arb_pending):
            if oid in self.open_orders:
                try:
                    await self.cancel_order(oid)
                except Exception:
                    pass
        self.arb_pending.clear()
        self.arb_busy = False

    async def _check_sum_arb(self):
        if self.arb_cooldown > 0:
            self.arb_cooldown -= 1
            return
        if self.arb_busy:
            return

        # Block arb if we already have a big imbalance from previous partial fills
        pos  = {s: self._pos(s) for s in PM_SYMS}
        max_pos = max(abs(pos[s]) for s in PM_SYMS)
        if max_pos >= ARB_MAX_NET:
            return

        bids = {s: self._best_bid(s) for s in PM_SYMS}
        asks = {s: self._best_ask(s) for s in PM_SYMS}

        if all(asks[s] for s in PM_SYMS):
            if min(asks[s] for s in PM_SYMS) >= ARB_SKIP_BELOW:
                sum_ask = sum(asks[s] for s in PM_SYMS)
                profit  = RESOLUTION - sum_ask
                if profit >= ARB_MIN_PROFIT:
                    qty = min(ARB_SIZE, min(PM_SOFT_LIMIT - pos[s] for s in PM_SYMS))
                    if qty > 0:
                        log.info(f"[ARB BUY] sum={sum_ask}  profit={profit}  qty={qty}")
                        self.arb_busy = True
                        for sym in PM_SYMS:
                            await self._place(sym, qty, Side.BUY, asks[sym], "arb")
                        self.arb_cooldown = ARB_COOLDOWN
                        return

        if all(bids[s] for s in PM_SYMS):
            if min(bids[s] for s in PM_SYMS) >= ARB_SKIP_BELOW:
                sum_bid = sum(bids[s] for s in PM_SYMS)
                profit  = sum_bid - RESOLUTION
                if profit >= ARB_MIN_PROFIT:
                    qty = min(ARB_SIZE, min(PM_SOFT_LIMIT + pos[s] for s in PM_SYMS))
                    if qty > 0:
                        log.info(f"[ARB SELL] sum={sum_bid}  profit={profit}  qty={qty}")
                        self.arb_busy = True
                        for sym in PM_SYMS:
                            await self._place(sym, qty, Side.SELL, bids[sym], "arb")
                        self.arb_cooldown = ARB_COOLDOWN

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 2: CPI SWEEP
    # ══════════════════════════════════════════════════════════════════════════

    async def _cpi_sweep(self, surprise: float):
        magnitude = min(3.0, abs(surprise) / 0.001)
        buy_qty  = max(1, int(CPI_BUY_BASE  * magnitude))
        sell_qty = max(1, int(CPI_SELL_BASE * magnitude))
        if surprise > 0:
            buy_sym, sell_sym, label = "R_HIKE", "R_CUT", "HAWKISH"
        else:
            buy_sym, sell_sym, label = "R_CUT", "R_HIKE", "DOVISH"
        log.info(f"[CPI {label}] surprise={surprise:+.5f}  "
                 f"buy {buy_qty}x{buy_sym}  sell {sell_qty}x{sell_sym}")
        # Adjust for existing position
        buy_qty  = self._adjust_qty_for_position(buy_sym, buy_qty, Side.BUY)
        sell_qty = self._adjust_qty_for_position(sell_sym, sell_qty, Side.SELL)

        # Price aggressively — pay up to 10 above best ask to sweep depth
        ask = self._best_ask(buy_sym)
        if ask:
            qty = min(buy_qty, PM_SOFT_LIMIT - self._pos(buy_sym))
            if qty > 0:
                await self._place(buy_sym, qty, Side.BUY, ask + 10, "cpi")
                self.news_traded_syms.add(buy_sym)
        bid = self._best_bid(sell_sym)
        if bid:
            qty = min(sell_qty, PM_SOFT_LIMIT + self._pos(sell_sym))
            if qty > 0:
                await self._place(sell_sym, qty, Side.SELL, max(1, bid - 10), "cpi")
                self.news_traded_syms.add(sell_sym)

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 3: NEWS SWEEP
    # ══════════════════════════════════════════════════════════════════════════

    def _score_headline(self, text: str, msg_type: str = "") -> tuple[int, int]:
        # T1: direct Fed language
        if _HAWK_T1.search(text): return +1, 1
        if _DOVE_T1.search(text):
            # Check for anti-dove context: "warn premature easing" is HAWK not dove
            if _ANTI_DOVE.search(text):
                return +1, 1  # flip to hawk — they're arguing against easing
            return -1, 1

        # T2: macro data — check for negation on inflation/wage signals only
        # (NOT unemployment/jobless — "unemployment falling" is hawkish, not negated)
        h2, d2 = len(_HAWK_T2.findall(text)), len(_DOVE_T2.findall(text))

        has_negation = bool(_NEGATION.search(text))
        is_labor = bool(re.search(r"unemploy|jobless|hiring|payroll|jobs?\b|employment", text, re.IGNORECASE))

        # Only flip hawk→dove via negation for non-labor signals
        if h2 > 0 and has_negation and d2 == 0 and not is_labor:
            d2 = h2
            h2 = 0

        if h2 > d2: return +1, 2
        if d2 > h2: return -1, 2
        if h2 > 0 and d2 > 0: return 0, 0

        # T3: soft signals
        h3, d3 = len(_HAWK_T3.findall(text)), len(_DOVE_T3.findall(text))
        if h3 > d3: return +1, 3
        if d3 > h3: return -1, 3

        return 0, 0

    def _adjust_qty_for_position(self, sym: str, base_qty: int, side: Side) -> int:
        """
        Reduce size when ADDING to existing directional risk.
        Keep full size when REDUCING risk or flat.
        """
        pos = self._pos(sym)
        if side == Side.BUY and pos > 5:
            # Already long — buying more increases risk, halve it
            return max(1, base_qty // 2)
        if side == Side.SELL and pos < -5:
            # Already short — selling more increases risk, halve it
            return max(1, base_qty // 2)
        return base_qty

    async def _news_sweep(self, buy_sym: str, sell_sym: str, buy_qty: int, sell_qty: int, tag: str = "news"):
        """Sweep through depth — price aggressively to ensure fill."""
        placed = []

        # Adjust sizes based on existing position
        buy_qty  = self._adjust_qty_for_position(buy_sym, buy_qty, Side.BUY)
        sell_qty = self._adjust_qty_for_position(sell_sym, sell_qty, Side.SELL)

        ask = self._best_ask(buy_sym)
        if ask and buy_qty > 0:
            qty = min(buy_qty, PM_SOFT_LIMIT - self._pos(buy_sym))
            if qty > 0:
                oid = await self._place(buy_sym, qty, Side.BUY, ask + 5, tag)
                if oid:
                    placed.append(f"BUY {qty}x{buy_sym}@{ask+5}")
                    self.news_traded_syms.add(buy_sym)
        bid = self._best_bid(sell_sym)
        if bid and sell_qty > 0:
            qty = min(sell_qty, PM_SOFT_LIMIT + self._pos(sell_sym))
            if qty > 0:
                oid = await self._place(sell_sym, qty, Side.SELL, max(1, bid - 5), tag)
                if oid:
                    placed.append(f"SELL {qty}x{sell_sym}@{max(1,bid-5)}")
                    self.news_traded_syms.add(sell_sym)
        if placed:
            log.info(f"[{tag.upper()} SWEEP] {', '.join(placed)}")
        else:
            log.info(f"[{tag.upper()} SWEEP] no orders placed — position limit or no book")

    # ══════════════════════════════════════════════════════════════════════════
    #  STRATEGY 4: FOLLOW THE MARKET AFTER ANY NEWS
    # ══════════════════════════════════════════════════════════════════════════

    def _open_follow_window(self):
        """Snapshot mids right when news arrives. Book updates will check for moves."""
        self.follow_snapshot = {}
        self.news_traded_syms.clear()  # reset — will be populated by CPI/news sweep
        for sym in PM_SYMS:
            m = self._mid(sym)
            if m is not None:
                self.follow_snapshot[sym] = m
        self.follow_deadline = self._time.time() + FOLLOW_WINDOW
        self.follow_fired = False

    async def _try_follow(self):
        """
        Called on every PM book update during the follow window.
        Find the contract that moved the most from the snapshot.
        If it moved up → buy it (momentum — news is pushing it).
        If it moved down → sell it.
        The contract that moved first and hardest is the one the
        market is telling us the news is about.
        """
        if self.follow_fired or not self.follow_snapshot:
            return
        if self._time.time() > self.follow_deadline:
            self.follow_fired = True
            return

        # Find biggest mover — skip contracts we already traded via CPI/news
        best_sym, best_delta = None, 0.0
        for sym in PM_SYMS:
            if sym in self.news_traded_syms:
                continue  # already traded this contract, don't pile on
            m = self._mid(sym)
            if m is None or sym not in self.follow_snapshot:
                continue
            delta = m - self.follow_snapshot[sym]
            if abs(delta) > abs(best_delta):
                best_sym, best_delta = sym, delta

        if best_sym is None or abs(best_delta) < FOLLOW_THRESHOLD:
            return  # not enough movement yet — wait

        # Follow the move
        if best_delta > 0:
            ask = self._best_ask(best_sym)
            if ask:
                qty = min(FOLLOW_QTY, PM_SOFT_LIMIT - self._pos(best_sym))
                if qty > 0:
                    await self._place(best_sym, qty, Side.BUY, ask + 3, "follow")
                    log.info(f"[FOLLOW] BUY {qty}x{best_sym}@{ask+3}  "
                             f"delta={best_delta:+.0f}")
        else:
            bid = self._best_bid(best_sym)
            if bid:
                qty = min(FOLLOW_QTY, PM_SOFT_LIMIT + self._pos(best_sym))
                if qty > 0:
                    await self._place(best_sym, qty, Side.SELL, max(1, bid - 3), "follow")
                    log.info(f"[FOLLOW] SELL {qty}x{best_sym}@{max(1,bid-3)}  "
                             f"delta={best_delta:+.0f}")

        self.follow_fired = True  # one shot per window

    # ══════════════════════════════════════════════════════════════════════════
    #  EVENT HANDLERS
    # ══════════════════════════════════════════════════════════════════════════

    async def bot_handle_book_update(self, symbol: str):
        if symbol not in PM_SYMS:
            return
        # Check follow window on every book update — speed matters
        if not self.follow_fired:
            await self._try_follow()
        if not self.arb_busy:
            await self._check_sum_arb()

    async def bot_handle_order_fill(self, order_id: str, qty: int, price: int):
        info = self.open_orders.get(order_id)
        oid  = str(order_id)
        tag  = self.fill_source.get(oid, "?")

        if oid in self.arb_pending:
            remaining = info[1] - qty if info else 0
            if remaining <= 0:
                self.arb_pending.discard(oid)
                self.fill_source.pop(oid, None)
            if not self.arb_pending:
                self.arb_busy = False
        else:
            self.fill_source.pop(oid, None)

        if info:
            sym  = info[0].symbol
            side = "BUY" if info[0].side == 1 else "SELL"
            signed = -price * qty if side == "BUY" else price * qty
            if tag in self.pnl:
                self.pnl[tag] += signed
            self.pnl["total"] += signed
            log.info(f"[FILL] {tag}  {sym} {side} {qty}@{price}  pos={self._pos(sym)}  "
                     f"${self.pnl['total']:+d}")

    async def bot_handle_order_rejected(self, order_id: str, reason: str):
        oid = str(order_id)
        self.fill_source.pop(oid, None)
        self.arb_pending.discard(oid)
        log.warning(f"[REJECTED] {order_id}: {reason}")

    async def bot_handle_cancel_response(self, order_id: str, success: bool, error: Optional[str] = None):
        if success:
            oid = str(order_id)
            self.fill_source.pop(oid, None)
            self.arb_pending.discard(oid)

    async def bot_handle_trade_msg(self, symbol: str, price: int, qty: int):
        if symbol in PM_SYMS and not self.arb_busy:
            await self._check_sum_arb()

    async def bot_handle_swap_response(self, swap: str, qty: int, success: bool):
        pass

    async def bot_handle_news(self, news_release: dict):
        news_type = news_release.get("kind")
        news_data = news_release.get("new_data", {})
        tick      = news_release.get("tick", 0)
        self.current_tick = max(self.current_tick, tick)

        # Open a follow window on EVERY news event — even if we can't parse it,
        # the market will react and we can follow the first significant move
        self._open_follow_window()

        if news_type == "structured":
            subtype = news_data.get("structured_subtype", "")
            if subtype == "cpi_print":
                forecast = float(news_data.get("forecast", 0))
                actual   = float(news_data.get("actual",   0))
                surprise = actual - forecast
                log.info(f"[CPI] actual={actual:.5f}  forecast={forecast:.5f}  "
                         f"surprise={surprise:+.5f}")
                if abs(surprise) >= CPI_MIN_SURPRISE:
                    await self._cpi_sweep(surprise)
                    await self._check_sum_arb()

        elif news_type == "unstructured":
            body = (news_data.get("content", "")
                    if isinstance(news_data, dict) else str(news_data))
            if not body.strip():
                return
            msg_type = news_data.get("type", "") if isinstance(news_data, dict) else ""
            direction, tier = self._score_headline(body, msg_type)

            # FedSpeak is directly about the Fed — boost tier if we have a signal
            if msg_type == "FedSpeak" and direction != 0 and tier > 1:
                tier = max(1, tier - 1)  # promote T2→T1, T3→T2

            log.info(f"[NEWS] type={msg_type!r}  tier={tier}  dir={direction:+d}  {body[:120]}")
            if direction == 0 or tier == 0:
                return
            buy_qty  = NEWS_BUY_SIZE[tier]
            sell_qty = NEWS_SELL_SIZE[tier]
            if direction > 0:
                buy_sym, sell_sym = "R_HIKE", "R_CUT"
            else:
                buy_sym, sell_sym = "R_CUT", "R_HIKE"
            if buy_qty > 0:
                await self._news_sweep(buy_sym, sell_sym, buy_qty, sell_qty)
            await self._check_sum_arb()

    async def bot_handle_market_resolved(self, market_id: str, winning_symbol: str, tick: int):
        log.info(f"[RESOLVED] {market_id} → {winning_symbol}")
        log.info(f"[FINAL POS] {self._pos_str()}")
        log.info(f"[PNL] arb={self.pnl['arb']:+d}  cpi={self.pnl['cpi']:+d}  "
                 f"news={self.pnl['news']:+d}  follow={self.pnl['follow']:+d}  "
                 f"total={self.pnl['total']:+d}")

    async def bot_handle_settlement_payout(self, user: str, market_id: str, amount: int, tick: int):
        log.info(f"[PAYOUT] {market_id}: {amount}")

    # ══════════════════════════════════════════════════════════════════════════
    #  MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════════

    async def trade_loop(self):
        await asyncio.sleep(2)
        log.info(f"[BOOKS] {list(self.order_books.keys())}")

        while True:
            await asyncio.sleep(TRADE_INTERVAL)
            self.loop_count += 1
            try:
                # Cancel stale directional orders — edge is gone after 2s
                if self.directional_orders:
                    await self._cancel_stale_directional()

                # Arb housekeeping — cancel stale partial fills
                if self.arb_busy and self.loop_count % 3 == 0:
                    await self._cancel_arb()

                await self._check_sum_arb()

                # Status
                if self.loop_count % 30 == 0:
                    pm_mids = {s: self._mid(s) for s in PM_SYMS}
                    mid_str = "  ".join(
                        f"{s}={pm_mids[s]:.0f}" if pm_mids[s] else f"{s}=?" for s in PM_SYMS)
                    mtm = sum(self._pos(s) * (pm_mids[s] or 0) for s in PM_SYMS)
                    log.info(
                        f"[STATUS] tick={self.current_tick}  {self._pos_str()}  "
                        f"mids: {mid_str}  "
                        f"arb={self.pnl['arb']:+d}  cpi={self.pnl['cpi']:+d}  "
                        f"news={self.pnl['news']:+d}  follow={self.pnl['follow']:+d}  "
                        f"cash={self.pnl['total']:+d}  mtm={mtm:+.0f}"
                    )

            except Exception as e:
                log.error(f"[LOOP ERR] {e}", exc_info=True)

    def _pos_str(self) -> str:
        return (f"cut={self._pos('R_CUT')} hold={self._pos('R_HOLD')} "
                f"hike={self._pos('R_HIKE')}")


# ── Entry point ────────────────────────────────────────────────────────────────

def _lock(port: int = 65432):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        return s
    except socket.error:
        log.critical("Already running — kill the old process first.")
        exit(1)


async def main():
    _lock()
    while True:
        try:
            bot = PMBot(f"{SERVER}:3333", USERNAME, PASSWORD)
            await bot.start()
        except Exception as e:
            log.error(f"Disconnected: {e} — reconnecting in 5s")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
