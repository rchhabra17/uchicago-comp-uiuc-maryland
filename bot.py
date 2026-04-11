"""
Trading Bot — Merged Stock A + Stock C + Prediction Market Strategies

Stock A (from rishabh-A):
- Linear regression fair value model (Change 1, 2)
- 8-second sniping window after earnings (Change 3)
- Sentiment-based trading on A-news (Change 4)

Stock C (from c-pred):
- Cross-asset fair value from prediction market probabilities
- PM probabilities → E[Δr] → yield → C fair value via case-packet formulas
- Calibration-based scale factor to anchor model to market prices
- Cross-asset sweeping when C market diverges from PM-derived fair value

Prediction Market (from c-strat-krishi):
- Sum arbitrage (buy/sell all 3 when sum deviates from 1000)
- CPI sprint (aggressive directional sweep on CPI surprise)
- News sweep (tiered hawk/dove headline classifier)
- Market follow (follow first mover after any news event)
"""

import re
from typing import Optional, Deque
from collections import deque
import time
import asyncio
import logging
import grpc

from utcxchangelib import XChangeClient, Side
import config
from fair_value import FairValueEngine, CFairValueEngine
from risk import RiskManager
from news_sentiment import NewsSentimentClassifier

_LOGGER = logging.getLogger("xchange-client")
_PM_LOG = logging.getLogger("PM")

# ============================================================================
# PREDICTION MARKET — News regex patterns (from c-strat-krishi bot.py)
# ============================================================================

# T1: Direct, unambiguous Fed language
_HAWK_T1 = re.compile(
    r"\brate\s+hike\b|\bhike\s+(?:rates?|interest)\b|"
    r"\b(?:raises?|raised|raising)\s+(?:rates?|interest\s+rates?)\b|"
    r"\bfomc\s+(?:hike|raise|increase)\b|"
    r"\bhawkish\s+(?:pivot|turn|shift|stance)\b|"
    r"\b\d+\s*(?:basis\s*points?|bps?)\s+(?:rate\s+)?hike\b|"
    r"\bhike\b.{0,20}\brates?\b|"
    r"\btighten(?:ing)?\s+(?:monetary|policy|stance)\b|"
    r"\brestrictive\s+(?:policy|stance|posture)\b(?!.{0,20}(?:no longer|not\s+needed|unnecessary|unwarranted))|"
    r"\bpremature\s+easing\b|"
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
    r"\b(?:monetary|policy)\s+eas(?:e|ing)\b|"
    r"\baccommodat(?:ion|ive|ing)\b|"
    r"\bloos(?:en|er|ening)\s+(?:monetary|policy)\b|"
    r"\brate\s+relief\b|"
    r"\brestrictive.{0,20}no\s+longer\b|"
    r"\bno\s+longer.{0,10}restrictive\b",
    re.IGNORECASE,
)

_ANTI_DOVE = re.compile(
    r"\bwarn|premature\s+easing|reignite|too\s+(?:early|soon)|risk.{0,15}easing",
    re.IGNORECASE,
)

# T2: Macro data signals
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
    r"wage.{0,15}(?:decelerat|slow|cool|eas|fall|declin)|"
    r"disinflation",
    re.IGNORECASE,
)

# T3: Soft/contextual signals
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

_NEGATION = re.compile(
    r"\b(?:decelerat|slow(?:ing|ed|s)?|cool(?:ing|ed|s)?|eas(?:ing|ed|es)|"
    r"fall(?:ing|s)?|declin(?:ing|ed|es)?|drop(?:ping|ped|s)?|weak(?:en|ing|ed)?|"
    r"fad(?:ing|ed|es)?|moder(?:at|ating|ated))\b",
    re.IGNORECASE,
)


class MyXchangeClient(XChangeClient):

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password)

        # ── Stock A state (PRESERVED from rishabh-A) ──────────────────────────
        self.fair_value = FairValueEngine()
        self.risk = RiskManager()
        self.sentiment = NewsSentimentClassifier()

        self.recent_mids_a: Deque[tuple] = deque(maxlen=300)
        self.recent_a_news: list = []

        self.sniping_mode_active = False
        self.sniping_start_time: Optional[float] = None

        self.post_news_mode_until: Optional[float] = None
        self.news_entry_mid: Optional[float] = None
        self.news_direction: Optional[int] = None

        self.last_a_news_sentiment: Optional[str] = None
        self.current_eps_a: Optional[float] = None
        self.pnl = 0

        # ── Stock C state (NEW from c-pred) ─────────────────────────────────────
        self.c_fair_value = CFairValueEngine(
            beta_y=config.C_BETA_Y, gamma=config.C_GAMMA
        )
        self.current_eps_c: Optional[float] = None

        # ── Prediction Market state (NEW from c-strat-krishi) ─────────────────
        self.pm_current_tick = 0
        self.pm_loop_count = 0
        self.pm_arb_cooldown = 0
        self.pm_arb_pending: set[str] = set()
        self.pm_arb_busy = False

        # Market-follow state
        self.pm_follow_snapshot: dict[str, float] = {}
        self.pm_follow_deadline: float = 0.0
        self.pm_follow_fired: bool = True

        # Stale order tracking for directional PM orders
        self.pm_directional_orders: list[tuple[str, float]] = []
        self.pm_news_traded_syms: set[str] = set()

        # PM P&L tracking
        self.pm_pnl = {"arb": 0, "cpi": 0, "news": 0, "follow": 0, "total": 0}
        self.pm_fill_source: dict[str, str] = {}

    # ========================================================================
    # STOCK A — ORDER MANAGEMENT (PRESERVED)
    # ========================================================================

    async def cancel_all_orders(self, symbol: str):
        """Cancel all open orders for a symbol."""
        to_cancel = [
            oid for oid, info in self.open_orders.items()
            if info[0].symbol == symbol
        ]
        for oid in to_cancel:
            await self.cancel_order(oid)

    async def sweep_book(self, symbol: str, fair: float, edge: float, max_qty: int):
        """
        Sweep the book aggressively within position limits.

        Args:
            symbol: Symbol to sweep
            fair: Fair value
            edge: Minimum edge required to take a quote
            max_qty: Maximum quantity per sweep action
        """
        book = self.order_books.get(symbol)
        if not book:
            return

        pos = self.positions.get(symbol, 0)

        # Sweep buy side (asks below fair - edge)
        if pos < config.MAX_POSITION:
            for price in sorted(book.asks.keys()):
                qty = book.asks.get(price, 0)
                if qty > 0 and price < fair - edge:
                    buy_qty = min(qty, max_qty, config.MAX_POSITION - pos)
                    if buy_qty > 0:
                        await self.place_order(symbol, buy_qty, Side.BUY, price)
                        pos += buy_qty
                else:
                    break

        # Sweep sell side (bids above fair + edge)
        if pos > -config.MAX_POSITION:
            for price in sorted(book.bids.keys(), reverse=True):
                qty = book.bids.get(price, 0)
                if qty > 0 and price > fair + edge:
                    sell_qty = min(qty, max_qty, config.MAX_POSITION + pos)
                    if sell_qty > 0:
                        await self.place_order(symbol, sell_qty, Side.SELL, price)
                        pos -= sell_qty
                else:
                    break

    async def quote_around(self, symbol: str, fair: float):
        """
        Passive quoting with inventory management.

        Only called when NOT in sniping mode and calibration is complete.
        """
        spread = config.PARAMS[symbol]["spread"]
        size = config.PARAMS[symbol]["order_size"]
        pos = self.positions.get(symbol, 0)

        # Inventory management: skew quotes against position
        skew_mult = config.PARAMS[symbol].get("skew_mult", 0.15)
        skew = -pos * skew_mult

        # Widen spread based on inventory
        inventory_spread_expansion = int(abs(pos) * 0.1)
        effective_spread = spread + inventory_spread_expansion

        bid_price = int(fair + skew - effective_spread)
        ask_price = int(fair + skew + effective_spread)

        # Squelch if inventory is critical
        if pos >= 30:
            bid_price = int(fair - 1000)  # un-quote bid
            ask_price = int(fair - effective_spread / 2)
        elif pos <= -30:
            ask_price = int(fair + 1000)  # un-quote ask
            bid_price = int(fair + effective_spread / 2)

        # Place orders with hard cutoffs
        if pos < 40:
            await self.place_order(symbol, size, Side.BUY, bid_price)
        if pos > -40:
            await self.place_order(symbol, size, Side.SELL, ask_price)

    # ========================================================================
    # STOCK A — MID PRICE TRACKING (PRESERVED)
    # ========================================================================

    def _get_current_mid_a(self) -> Optional[float]:
        """Get current mid price for A from order book."""
        book = self.order_books.get("A")
        if not book:
            return None

        bids = [px for px, qty in book.bids.items() if qty > 0]
        asks = [px for px, qty in book.asks.items() if qty > 0]

        if bids and asks:
            return (max(bids) + min(asks)) / 2
        return None

    def _get_average_mid_a(self, start_time: float, end_time: float) -> Optional[float]:
        """
        Average mid price over a time window.

        Args:
            start_time: Start of window (unix timestamp)
            end_time: End of window (unix timestamp)

        Returns:
            Average mid over window, or None if insufficient data
        """
        samples_in_window = [
            mid for ts, mid in self.recent_mids_a
            if start_time <= ts <= end_time and mid is not None
        ]

        if not samples_in_window:
            return None

        return sum(samples_in_window) / len(samples_in_window)

    # ========================================================================
    # STOCK A — CALIBRATION (Changes 1, 2) (PRESERVED)
    # ========================================================================

    async def handle_a_earnings(self, eps: float, tick: int):
        """
        Handle A earnings event.

        CRITICAL: Flatten position first, then snipe fresh.
        - Cancel all orders
        - FLATTEN position (sell if long, buy if short) to avoid earnings shock
        - Update EPS and compute new fair value using linear model
        - Schedule calibration sample recording at +12s (runs in background)
        - Enter sniping mode if calibrated

        Args:
            eps: Earnings per share value
            tick: Tick number of earnings event
        """
        print(f"\n[EARNINGS] A: EPS={eps:.4f}, tick={tick}")

        # Track this news event for contamination checking
        self.recent_a_news.append((tick, "earnings"))

        # Cancel all open orders (defensive)
        await self.cancel_all_orders("A")

        # SENTIMENT-BASED POSITION DECISION (SPEED OPTIMIZED)
        # Use last A-news sentiment to predict earnings direction
        # Skip flattening small positions to avoid 1-2 tick delay
        pos = self.positions.get("A", 0)
        abs_pos = abs(pos)

        # Skip flattening if position is too small to matter
        if abs_pos < config.MIN_POSITION_TO_FLATTEN:
            if pos != 0:
                print(f"[EARNINGS] Position too small ({pos}), skipping flatten for speed")
        else:
            # Large position - use sentiment to decide
            should_flatten = True

            if self.last_a_news_sentiment is not None:
                # Check if position is aligned with recent sentiment
                if self.last_a_news_sentiment == "bullish" and pos > 0:
                    print(f"[EARNINGS] Last news BULLISH, keeping LONG position ({pos} shares)")
                    should_flatten = False
                elif self.last_a_news_sentiment == "bearish" and pos < 0:
                    print(f"[EARNINGS] Last news BEARISH, keeping SHORT position ({pos} shares)")
                    should_flatten = False
                else:
                    print(f"[EARNINGS] Position misaligned with sentiment (sentiment={self.last_a_news_sentiment}, pos={pos}), flattening")

            # Flatten if misaligned or no recent sentiment signal
            if should_flatten:
                print(f"[EARNINGS] Flattening position: {pos} → 0")
                if pos > 0:
                    await self.place_order("A", pos, Side.SELL, None)
                else:
                    await self.place_order("A", -pos, Side.BUY, None)
                await asyncio.sleep(0.2)

        # Update current EPS - linear model is built to predict fair value for NEW EPS
        self.current_eps_a = eps

        # Schedule calibration sample recording at +12s (runs in background during sniping)
        asyncio.create_task(self.record_calibration_sample(eps, tick))

        # Enter sniping mode if already calibrated
        # Use NEW EPS with EXISTING model - this is the whole point of linear regression
        if self.fair_value.is_calibrated_a():
            fair = self.fair_value.fair_value_a(eps)
            if fair is not None:
                print(f"[EARNINGS] Fair value: {fair:.2f} (model predicts for new EPS={eps:.4f})")
                await self.enter_sniping_mode(config.SNIPING_DURATION_S)
        else:
            # Not yet calibrated - just wait for calibration
            n_samples = len(self.fair_value.samples_a)
            n_distinct = self.fair_value.n_distinct_eps_a()
            print(f"[EARNINGS] Not yet calibrated (samples={n_samples}, distinct_eps={n_distinct}, need {config.MIN_DISTINCT_EPS_FOR_TRADING})")

    async def record_calibration_sample(self, eps: float, earnings_tick: int):
        """
        Record calibration sample at +12s after earnings.

        Per Change 2:
        - Wait until +12s after earnings
        - Average mid over [+10s, +15s] window
        - Check for contamination
        - Add sample to fair value engine

        Args:
            eps: EPS value from earnings
            earnings_tick: Tick when earnings arrived
        """
        # Wait 12 seconds
        await asyncio.sleep(12)

        current_time = time.time()
        window_start = current_time - 2  # 12s - 2s = +10s
        window_end = current_time + 3    # 12s + 3s = +15s

        # Get average mid over [+10s, +15s] window
        settled_mid = self._get_average_mid_a(window_start, window_end)

        if settled_mid is None:
            # Fallback: use current mid if window averaging fails
            settled_mid = self._get_current_mid_a()

        if settled_mid is None:
            print(f"[CALIBRATE] ✗ Cannot record sample: no mid price available")
            return

        # Check for contamination: was there another A-news event within +15s?
        contamination_cutoff_tick = earnings_tick + (config.CONTAMINATION_WINDOW_S * 5)  # 5 ticks/sec
        contaminated = any(
            tick != earnings_tick and earnings_tick < tick <= contamination_cutoff_tick
            for tick, _ in self.recent_a_news
        )

        if contaminated:
            print(f"[CALIBRATE] ✗ Sample contaminated (another A-news event within {config.CONTAMINATION_WINDOW_S}s), skipping")
            return

        # Record sample
        self.fair_value.add_sample_a(eps, settled_mid)

    # ========================================================================
    # STOCK A — SNIPING MODE (Change 3) (PRESERVED)
    # ========================================================================

    async def enter_sniping_mode(self, duration_s: float):
        """
        Enter sniping mode for a fixed duration.

        During sniping mode:
        - Scan book on every update
        - Take mispriced quotes with time-decaying edge thresholds
        - Exit automatically after duration

        Args:
            duration_s: Duration of sniping mode in seconds
        """
        self.sniping_mode_active = True
        self.sniping_start_time = time.time()

        print(f"[SNIPING] Entered sniping mode for {duration_s:.1f}s")

        # Schedule exit
        await asyncio.sleep(duration_s)
        await self.exit_sniping_mode()

    async def exit_sniping_mode(self):
        """Exit sniping mode and return to normal operation."""
        self.sniping_mode_active = False
        self.sniping_start_time = None
        print(f"[SNIPING] Exited sniping mode")

    def _get_sniping_edge(self) -> float:
        """
        Get current sniping edge based on time since sniping started.

        Returns step function:
        - [0-2s]: edge = 2
        - [2-5s]: edge = 4
        - [5-8s]: edge = 8
        """
        if not self.sniping_mode_active or self.sniping_start_time is None:
            return config.PARAMS["A"]["sweep_edge"]  # default

        elapsed = time.time() - self.sniping_start_time

        if elapsed < 2:
            return config.SNIPING_EDGE_EARLY
        elif elapsed < 5:
            return config.SNIPING_EDGE_MID
        else:
            return config.SNIPING_EDGE_LATE

    async def snipe_on_book_update(self):
        """
        Sniping logic triggered on book updates.

        Called from bot_handle_book_update when sniping mode is active.
        """
        if self.current_eps_a is None:
            return

        fair = self.fair_value.fair_value_a(self.current_eps_a)
        if fair is None:
            return

        edge = self._get_sniping_edge()

        # Sweep with time-decaying edge
        await self.sweep_book("A", fair, edge, config.SNIPING_MAX_PER_SCAN)

    # ========================================================================
    # STOCK A — NEWS SENTIMENT TRADING (Change 4) (PRESERVED)
    # ========================================================================

    async def handle_a_news_trade(self, sentiment: str, confidence: float, content: str):
        """
        Execute directional trade based on A-news sentiment.

        Per Change 4:
        - Compute expected fair shift (±150 pts)
        - Aggressively trade in direction of sentiment
        - Enter post-news mode for 10s

        Args:
            sentiment: "bullish" or "bearish"
            confidence: Classifier confidence [0, 1]
            content: News content (for logging)
        """
        print(f"\n[NEWS TRADE] Sentiment: {sentiment}, confidence: {confidence:.2f}")
        print(f"[NEWS TRADE] Content: {content[:80]}...")

        # Get current mid before trading
        pre_news_mid = self._get_current_mid_a()
        if pre_news_mid is None:
            print(f"[NEWS TRADE] ✗ Cannot trade: no mid price")
            return

        self.news_entry_mid = pre_news_mid

        # Determine direction
        if sentiment == "bullish":
            self.news_direction = +1
            expected_fair = pre_news_mid + config.NEWS_EXPECTED_MOVE
        elif sentiment == "bearish":
            self.news_direction = -1
            expected_fair = pre_news_mid - config.NEWS_EXPECTED_MOVE
        else:
            print(f"[NEWS TRADE] ✗ Invalid sentiment: {sentiment}")
            return

        print(f"[NEWS TRADE] Pre-news mid: {pre_news_mid:.2f}, expected fair: {expected_fair:.2f}")

        # Cancel all existing orders
        await self.cancel_all_orders("A")

        # Compute trade size (half of max position)
        max_trade_size = int(config.MAX_POSITION * config.NEWS_TRADE_SIZE_FRACTION)
        current_pos = self.positions.get("A", 0)

        # Trade aggressively in direction of sentiment
        book = self.order_books.get("A")
        if not book:
            print(f"[NEWS TRADE] ✗ No order book")
            return

        if sentiment == "bullish":
            # Buy into asks aggressively
            target_pos = min(current_pos + max_trade_size, config.MAX_POSITION)
            qty_to_buy = target_pos - current_pos

            if qty_to_buy > 0:
                # Market buy up to qty_to_buy
                asks_sorted = sorted((px, qty) for px, qty in book.asks.items() if qty > 0)
                remaining = qty_to_buy

                for price, available_qty in asks_sorted:
                    if remaining <= 0:
                        break
                    trade_qty = min(remaining, available_qty, config.MAX_ORDER_SIZE)
                    await self.place_order("A", trade_qty, Side.BUY, price)
                    remaining -= trade_qty
                    print(f"[NEWS TRADE] BUY {trade_qty} @ {price}")

        elif sentiment == "bearish":
            # Sell into bids aggressively
            target_pos = max(current_pos - max_trade_size, -config.MAX_POSITION)
            qty_to_sell = current_pos - target_pos

            if qty_to_sell > 0:
                # Market sell up to qty_to_sell
                bids_sorted = sorted(((px, qty) for px, qty in book.bids.items() if qty > 0), reverse=True)
                remaining = qty_to_sell

                for price, available_qty in bids_sorted:
                    if remaining <= 0:
                        break
                    trade_qty = min(remaining, available_qty, config.MAX_ORDER_SIZE)
                    await self.place_order("A", trade_qty, Side.SELL, price)
                    remaining -= trade_qty
                    print(f"[NEWS TRADE] SELL {trade_qty} @ {price}")

        # Enter post-news mode
        self.post_news_mode_until = time.time() + config.POST_NEWS_MODE_S
        print(f"[NEWS TRADE] Entered post-news mode for {config.POST_NEWS_MODE_S}s")

    async def check_news_exit(self):
        """
        Check if we should exit news position based on profit target.

        Called from bot_handle_book_update when post-news mode is active.
        Exits if mid has moved > NEWS_EXIT_MOVE_THRESHOLD in our direction.
        """
        if self.post_news_mode_until is None or self.news_entry_mid is None:
            return

        current_mid = self._get_current_mid_a()
        if current_mid is None:
            return

        move = current_mid - self.news_entry_mid

        # Check if move is in our direction and exceeds threshold
        if self.news_direction == +1 and move >= config.NEWS_EXIT_MOVE_THRESHOLD:
            print(f"[NEWS EXIT] Profit target hit: mid moved +{move:.0f} (target: +{config.NEWS_EXIT_MOVE_THRESHOLD})")
            await self.exit_news_position()

        elif self.news_direction == -1 and move <= -config.NEWS_EXIT_MOVE_THRESHOLD:
            print(f"[NEWS EXIT] Profit target hit: mid moved {move:.0f} (target: -{config.NEWS_EXIT_MOVE_THRESHOLD})")
            await self.exit_news_position()

    async def exit_news_position(self):
        """Exit news position aggressively."""
        pos = self.positions.get("A", 0)

        if pos == 0:
            print(f"[NEWS EXIT] No position to exit")
            self.post_news_mode_until = None
            self.news_entry_mid = None
            self.news_direction = None
            return

        # Close position aggressively
        book = self.order_books.get("A")
        if not book:
            return

        if pos > 0:
            # Sell to close long
            bids_sorted = sorted(((px, qty) for px, qty in book.bids.items() if qty > 0), reverse=True)
            remaining = pos

            for price, available_qty in bids_sorted:
                if remaining <= 0:
                    break
                trade_qty = min(remaining, available_qty, config.MAX_ORDER_SIZE)
                await self.place_order("A", trade_qty, Side.SELL, price)
                remaining -= trade_qty
                print(f"[NEWS EXIT] SELL {trade_qty} @ {price}")

        elif pos < 0:
            # Buy to close short
            asks_sorted = sorted((px, qty) for px, qty in book.asks.items() if qty > 0)
            remaining = abs(pos)

            for price, available_qty in asks_sorted:
                if remaining <= 0:
                    break
                trade_qty = min(remaining, available_qty, config.MAX_ORDER_SIZE)
                await self.place_order("A", trade_qty, Side.BUY, price)
                remaining -= trade_qty
                print(f"[NEWS EXIT] BUY {trade_qty} @ {price}")

        # Clear news mode state
        self.post_news_mode_until = None
        self.news_entry_mid = None
        self.news_direction = None

    # ========================================================================
    # PREDICTION MARKET — BOOK HELPERS (NEW from c-strat-krishi)
    # ========================================================================

    def _pm_best_bid(self, sym: str) -> Optional[int]:
        book = self.order_books.get(sym)
        if not book:
            return None
        bids = [k for k, v in book.bids.items() if v > 0]
        return max(bids) if bids else None

    def _pm_best_ask(self, sym: str) -> Optional[int]:
        book = self.order_books.get(sym)
        if not book:
            return None
        asks = [k for k, v in book.asks.items() if v > 0]
        return min(asks) if asks else None

    def _pm_mid(self, sym: str) -> Optional[float]:
        b, a = self._pm_best_bid(sym), self._pm_best_ask(sym)
        return (b + a) / 2 if b and a else None

    def _pm_pos(self, sym: str) -> int:
        return self.positions.get(sym, 0)

    # ========================================================================
    # STOCK C — HELPERS (NEW from c-pred)
    # ========================================================================

    def _get_current_mid_c(self) -> Optional[float]:
        """Get current mid price for C from order book."""
        book = self.order_books.get("C")
        if not book:
            return None
        bids = [px for px, qty in book.bids.items() if qty > 0]
        asks = [px for px, qty in book.asks.items() if qty > 0]
        if bids and asks:
            return (max(bids) + min(asks)) / 2
        return None

    def _get_e_delta_r(self) -> Optional[float]:
        """
        Compute E[Δr] (expected rate change in bps) from PM book mids.

        E[Δr] = 25 × q_hike − 25 × q_cut
        where q_hike = R_HIKE_mid / total, q_cut = R_CUT_mid / total
        """
        cut_mid = self._pm_mid("R_CUT")
        hold_mid = self._pm_mid("R_HOLD")
        hike_mid = self._pm_mid("R_HIKE")
        if cut_mid is None or hold_mid is None or hike_mid is None:
            return None
        total = cut_mid + hold_mid + hike_mid
        if total <= 0:
            return None
        q_hike = hike_mid / total
        q_cut = cut_mid / total
        return 25.0 * q_hike - 25.0 * q_cut

    async def _c_cross_asset_trade(self, signal: float):
        """
        Execute cross-asset trade when C market diverges from PM-derived fair value.

        signal > 0: C expensive vs model → sell C
        signal < 0: C cheap vs model → buy C
        """
        if self.c_fair_value.fair_c is None:
            return
        pos = self.positions.get("C", 0)
        if abs(pos) >= config.C_CROSS_MAX_POS:
            return
        max_qty = min(config.C_CROSS_QTY, config.C_CROSS_MAX_POS - abs(pos))
        if max_qty <= 0:
            return
        print(f"[C CROSS] signal={signal:.1f} | fair={self.c_fair_value.fair_c:.0f} | pos={pos}")
        await self.sweep_book("C", self.c_fair_value.fair_c,
                              config.PARAMS["C"]["sweep_edge"], max_qty)

    # ========================================================================
    # STOCK C — EARNINGS HANDLER (NEW from c-pred)
    # ========================================================================

    async def handle_c_earnings(self, eps: float, tick: int):
        """
        Handle C earnings event. Calibrate/recalibrate C fair value model.

        On each C earnings:
        1. Record new EPS
        2. Get current C market mid and E[Δr] from PM
        3. Calibrate model with (market_mid, eps, E[Δr])
        4. Sweep if cross-asset signal exists
        """
        print(f"\n[EARNINGS] C: EPS={eps:.4f}, tick={tick}")
        self.current_eps_c = eps

        # Cancel all C orders before recalibrating
        await self.cancel_all_orders("C")

        # Get current market mid and PM-derived E[Δr]
        c_mid = self._get_current_mid_c()
        e_dr = self._get_e_delta_r()

        if c_mid is not None and e_dr is not None:
            self.c_fair_value.calibrate(c_mid, eps, e_dr)
            print(f"[EARNINGS] C fair value calibrated: {self.c_fair_value.fair_c:.1f}")
        else:
            print(f"[EARNINGS] C: Waiting for PM data to calibrate "
                  f"(c_mid={'ok' if c_mid else 'missing'}, e_dr={'ok' if e_dr else 'missing'})")

    # ========================================================================
    # PREDICTION MARKET — ORDER HELPERS (NEW from c-strat-krishi)
    # ========================================================================

    async def _pm_place(self, sym: str, qty: int, side: Side, price: int, tag: str = ""):
        """Place a PM order with position limits and tagging."""
        curr = self._pm_pos(sym)
        if side == Side.BUY and curr >= config.PM_SOFT_LIMIT:
            return None
        if side == Side.SELL and curr <= -config.PM_SOFT_LIMIT:
            return None
        qty = min(qty, config.MAX_ORDER_SIZE)
        if qty <= 0:
            return None
        try:
            oid = await self.place_order(sym, qty, side, max(1, int(price)))
            oid_str = str(oid)
            if tag:
                self.pm_fill_source[oid_str] = tag
            if tag == "arb":
                self.pm_arb_pending.add(oid_str)
            if tag in ("cpi", "news", "follow"):
                self.pm_directional_orders.append((oid_str, time.time() + 2.0))
            return oid_str
        except Exception as e:
            _PM_LOG.warning(f"[ORDER ERR] {sym} {side.name} {qty}@{price}: {e}")
            return None

    async def _pm_cancel_stale_directional(self):
        """Cancel directional PM orders older than 2s."""
        now = time.time()
        still_live = []
        for oid, expiry in self.pm_directional_orders:
            if now >= expiry and oid in self.open_orders:
                try:
                    await self.cancel_order(oid)
                except Exception:
                    pass
            elif oid in self.open_orders:
                still_live.append((oid, expiry))
        self.pm_directional_orders = still_live

    # ========================================================================
    # PM STRATEGY 1: SUM ARBITRAGE (NEW from c-strat-krishi)
    # ========================================================================

    async def _pm_cancel_arb(self):
        for oid in list(self.pm_arb_pending):
            if oid in self.open_orders:
                try:
                    await self.cancel_order(oid)
                except Exception:
                    pass
        self.pm_arb_pending.clear()
        self.pm_arb_busy = False

    async def _pm_check_sum_arb(self):
        if self.pm_arb_cooldown > 0:
            self.pm_arb_cooldown -= 1
            return
        if self.pm_arb_busy:
            return

        pos = {s: self._pm_pos(s) for s in config.PM_SYMS}
        max_pos = max(abs(pos[s]) for s in config.PM_SYMS)
        if max_pos >= config.PM_ARB_MAX_NET:
            return

        bids = {s: self._pm_best_bid(s) for s in config.PM_SYMS}
        asks = {s: self._pm_best_ask(s) for s in config.PM_SYMS}

        if all(asks[s] for s in config.PM_SYMS):
            if min(asks[s] for s in config.PM_SYMS) >= config.PM_ARB_SKIP_BELOW:
                sum_ask = sum(asks[s] for s in config.PM_SYMS)
                profit = config.PM_RESOLUTION - sum_ask
                if profit >= config.PM_ARB_MIN_PROFIT:
                    qty = min(config.PM_ARB_SIZE, min(config.PM_SOFT_LIMIT - pos[s] for s in config.PM_SYMS))
                    if qty > 0:
                        _PM_LOG.info(f"[ARB BUY] sum={sum_ask}  profit={profit}  qty={qty}")
                        self.pm_arb_busy = True
                        for sym in config.PM_SYMS:
                            await self._pm_place(sym, qty, Side.BUY, asks[sym], "arb")
                        self.pm_arb_cooldown = config.PM_ARB_COOLDOWN
                        return

        if all(bids[s] for s in config.PM_SYMS):
            if min(bids[s] for s in config.PM_SYMS) >= config.PM_ARB_SKIP_BELOW:
                sum_bid = sum(bids[s] for s in config.PM_SYMS)
                profit = sum_bid - config.PM_RESOLUTION
                if profit >= config.PM_ARB_MIN_PROFIT:
                    qty = min(config.PM_ARB_SIZE, min(config.PM_SOFT_LIMIT + pos[s] for s in config.PM_SYMS))
                    if qty > 0:
                        _PM_LOG.info(f"[ARB SELL] sum={sum_bid}  profit={profit}  qty={qty}")
                        self.pm_arb_busy = True
                        for sym in config.PM_SYMS:
                            await self._pm_place(sym, qty, Side.SELL, bids[sym], "arb")
                        self.pm_arb_cooldown = config.PM_ARB_COOLDOWN

    # ========================================================================
    # PM STRATEGY 2: CPI SWEEP (NEW from c-strat-krishi)
    # ========================================================================

    def _pm_adjust_qty_for_position(self, sym: str, base_qty: int, side: Side) -> int:
        """Reduce size when adding to existing directional risk."""
        pos = self._pm_pos(sym)
        if side == Side.BUY and pos > 5:
            return max(1, base_qty // 2)
        if side == Side.SELL and pos < -5:
            return max(1, base_qty // 2)
        return base_qty

    async def _pm_cpi_sweep(self, surprise: float):
        magnitude = min(3.0, abs(surprise) / 0.001)
        buy_qty = max(1, int(config.PM_CPI_BUY_BASE * magnitude))
        sell_qty = max(1, int(config.PM_CPI_SELL_BASE * magnitude))
        if surprise > 0:
            buy_sym, sell_sym, label = "R_HIKE", "R_CUT", "HAWKISH"
        else:
            buy_sym, sell_sym, label = "R_CUT", "R_HIKE", "DOVISH"
        _PM_LOG.info(f"[CPI {label}] surprise={surprise:+.5f}  "
                     f"buy {buy_qty}x{buy_sym}  sell {sell_qty}x{sell_sym}")

        buy_qty = self._pm_adjust_qty_for_position(buy_sym, buy_qty, Side.BUY)
        sell_qty = self._pm_adjust_qty_for_position(sell_sym, sell_qty, Side.SELL)

        ask = self._pm_best_ask(buy_sym)
        if ask:
            qty = min(buy_qty, config.PM_SOFT_LIMIT - self._pm_pos(buy_sym))
            if qty > 0:
                await self._pm_place(buy_sym, qty, Side.BUY, ask + 10, "cpi")
                self.pm_news_traded_syms.add(buy_sym)
        bid = self._pm_best_bid(sell_sym)
        if bid:
            qty = min(sell_qty, config.PM_SOFT_LIMIT + self._pm_pos(sell_sym))
            if qty > 0:
                await self._pm_place(sell_sym, qty, Side.SELL, max(1, bid - 10), "cpi")
                self.pm_news_traded_syms.add(sell_sym)

    # ========================================================================
    # PM STRATEGY 3: NEWS SWEEP (NEW from c-strat-krishi)
    # ========================================================================

    def _pm_score_headline(self, text: str, msg_type: str = "") -> tuple[int, int]:
        """Score headline for hawk/dove direction. Returns (direction, tier)."""
        if _HAWK_T1.search(text): return +1, 1
        if _DOVE_T1.search(text):
            if _ANTI_DOVE.search(text):
                return +1, 1
            return -1, 1

        h2, d2 = len(_HAWK_T2.findall(text)), len(_DOVE_T2.findall(text))
        has_negation = bool(_NEGATION.search(text))
        is_labor = bool(re.search(r"unemploy|jobless|hiring|payroll|jobs?\b|employment", text, re.IGNORECASE))

        if h2 > 0 and has_negation and d2 == 0 and not is_labor:
            d2 = h2
            h2 = 0

        if h2 > d2: return +1, 2
        if d2 > h2: return -1, 2
        if h2 > 0 and d2 > 0: return 0, 0

        h3, d3 = len(_HAWK_T3.findall(text)), len(_DOVE_T3.findall(text))
        if h3 > d3: return +1, 3
        if d3 > h3: return -1, 3

        return 0, 0

    async def _pm_news_sweep(self, buy_sym: str, sell_sym: str, buy_qty: int, sell_qty: int, tag: str = "news"):
        """Sweep PM book directionally."""
        placed = []

        buy_qty = self._pm_adjust_qty_for_position(buy_sym, buy_qty, Side.BUY)
        sell_qty = self._pm_adjust_qty_for_position(sell_sym, sell_qty, Side.SELL)

        ask = self._pm_best_ask(buy_sym)
        if ask and buy_qty > 0:
            qty = min(buy_qty, config.PM_SOFT_LIMIT - self._pm_pos(buy_sym))
            if qty > 0:
                oid = await self._pm_place(buy_sym, qty, Side.BUY, ask + 5, tag)
                if oid:
                    placed.append(f"BUY {qty}x{buy_sym}@{ask+5}")
                    self.pm_news_traded_syms.add(buy_sym)
        bid = self._pm_best_bid(sell_sym)
        if bid and sell_qty > 0:
            qty = min(sell_qty, config.PM_SOFT_LIMIT + self._pm_pos(sell_sym))
            if qty > 0:
                oid = await self._pm_place(sell_sym, qty, Side.SELL, max(1, bid - 5), tag)
                if oid:
                    placed.append(f"SELL {qty}x{sell_sym}@{max(1,bid-5)}")
                    self.pm_news_traded_syms.add(sell_sym)
        if placed:
            _PM_LOG.info(f"[{tag.upper()} SWEEP] {', '.join(placed)}")

    # ========================================================================
    # PM STRATEGY 4: MARKET FOLLOW (NEW from c-strat-krishi)
    # ========================================================================

    def _pm_open_follow_window(self):
        """Snapshot mids when news arrives for follow-the-leader."""
        self.pm_follow_snapshot = {}
        self.pm_news_traded_syms.clear()
        for sym in config.PM_SYMS:
            m = self._pm_mid(sym)
            if m is not None:
                self.pm_follow_snapshot[sym] = m
        self.pm_follow_deadline = time.time() + config.PM_FOLLOW_WINDOW
        self.pm_follow_fired = False

    async def _pm_try_follow(self):
        """Follow the first significant mover after news."""
        if self.pm_follow_fired or not self.pm_follow_snapshot:
            return
        if time.time() > self.pm_follow_deadline:
            self.pm_follow_fired = True
            return

        best_sym, best_delta = None, 0.0
        for sym in config.PM_SYMS:
            if sym in self.pm_news_traded_syms:
                continue
            m = self._pm_mid(sym)
            if m is None or sym not in self.pm_follow_snapshot:
                continue
            delta = m - self.pm_follow_snapshot[sym]
            if abs(delta) > abs(best_delta):
                best_sym, best_delta = sym, delta

        if best_sym is None or abs(best_delta) < config.PM_FOLLOW_THRESHOLD:
            return

        if best_delta > 0:
            ask = self._pm_best_ask(best_sym)
            if ask:
                qty = min(config.PM_FOLLOW_QTY, config.PM_SOFT_LIMIT - self._pm_pos(best_sym))
                if qty > 0:
                    await self._pm_place(best_sym, qty, Side.BUY, ask + 3, "follow")
                    _PM_LOG.info(f"[FOLLOW] BUY {qty}x{best_sym}@{ask+3} delta={best_delta:+.0f}")
        else:
            bid = self._pm_best_bid(best_sym)
            if bid:
                qty = min(config.PM_FOLLOW_QTY, config.PM_SOFT_LIMIT + self._pm_pos(best_sym))
                if qty > 0:
                    await self._pm_place(best_sym, qty, Side.SELL, max(1, bid - 3), "follow")
                    _PM_LOG.info(f"[FOLLOW] SELL {qty}x{best_sym}@{max(1,bid-3)} delta={best_delta:+.0f}")

        self.pm_follow_fired = True

    # ========================================================================
    # PM — STATUS HELPERS
    # ========================================================================

    def _pm_pos_str(self) -> str:
        return (f"cut={self._pm_pos('R_CUT')} hold={self._pm_pos('R_HOLD')} "
                f"hike={self._pm_pos('R_HIKE')}")

    # ========================================================================
    # MERGED EVENT HANDLERS
    # ========================================================================

    async def bot_handle_book_update(self, symbol: str):
        """
        Merged book update handler for ALL THREE strategies.

        Stock A: mid tracking, sniping, news exit check
        Stock C: (handled by c_trade_loop periodically)
        Prediction Market: follow window, sum arb check
        """
        # ── Stock A logic (PRESERVED) ─────────────────────────────────────────
        if symbol == "A":
            current_mid = self._get_current_mid_a()
            if current_mid is not None:
                self.recent_mids_a.append((time.time(), current_mid))

            if self.sniping_mode_active:
                await self.snipe_on_book_update()

            if self.post_news_mode_until is not None and time.time() < self.post_news_mode_until:
                await self.check_news_exit()

        # ── Prediction Market logic (NEW) ─────────────────────────────────────
        if symbol in config.PM_SYMS:
            if not self.pm_follow_fired:
                await self._pm_try_follow()
            if not self.pm_arb_busy:
                await self._pm_check_sum_arb()

    async def bot_handle_order_fill(self, order_id: str, qty: int, price: int):
        """Merged fill handler for both strategies."""
        info = self.open_orders.get(order_id)
        oid = str(order_id)

        if not info:
            return

        symbol = info[0].symbol
        side = "BUY" if info[0].side == 1 else "SELL"

        # ── Stock A fill logging (PRESERVED) ──────────────────────────────────
        if symbol == "A" and self.current_eps_a is not None:
            fair = self.fair_value.fair_value_a(self.current_eps_a)
            if fair:
                edge = (fair - price) if side == "BUY" else (price - fair)
                print(f"[FILL] {symbol} {side} {qty}@{price} | fair={fair:.0f} | edge={edge:.0f}")

        # ── PM fill tracking (NEW) ────────────────────────────────────────────
        if symbol in config.PM_SYMS:
            tag = self.pm_fill_source.get(oid, "?")

            if oid in self.pm_arb_pending:
                remaining = info[1] - qty if info else 0
                if remaining <= 0:
                    self.pm_arb_pending.discard(oid)
                    self.pm_fill_source.pop(oid, None)
                if not self.pm_arb_pending:
                    self.pm_arb_busy = False
            else:
                self.pm_fill_source.pop(oid, None)

            signed = -price * qty if side == "BUY" else price * qty
            if tag in self.pm_pnl:
                self.pm_pnl[tag] += signed
            self.pm_pnl["total"] += signed
            _PM_LOG.info(f"[FILL] {tag}  {symbol} {side} {qty}@{price}  pos={self._pm_pos(symbol)}  "
                         f"${self.pm_pnl['total']:+d}")

    async def bot_handle_trade_msg(self, symbol: str, price: int, qty: int):
        """Check for PM arb opportunities on public trades."""
        if symbol in config.PM_SYMS and not self.pm_arb_busy:
            await self._pm_check_sum_arb()

    async def bot_handle_order_rejected(self, order_id: str, reason: str):
        oid = str(order_id)
        self.pm_fill_source.pop(oid, None)
        self.pm_arb_pending.discard(oid)
        _PM_LOG.warning(f"[REJECTED] {order_id}: {reason}")

    async def bot_handle_cancel_response(self, order_id: str, success: bool, error: Optional[str] = None):
        if success:
            oid = str(order_id)
            self.pm_fill_source.pop(oid, None)
            self.pm_arb_pending.discard(oid)

    async def bot_handle_news(self, news_release: dict):
        """
        Merged news handler for ALL THREE strategies.

        Routing:
        - A earnings → Stock A handler (PRESERVED)
        - C earnings → Stock C handler (NEW from c-pred)
        - CPI print → PM CPI sweep (PRESERVED) + C fair value update (NEW)
        - A-symbol unstructured → Stock A sentiment classifier (PRESERVED)
        - Non-A unstructured → PM headline classifier (PRESERVED)
        - All news → PM follow window (PRESERVED)
        """
        news_type = news_release["kind"]
        news_data = news_release["new_data"]
        tick = news_release["tick"]

        # ── PM: open follow window on EVERY news event ────────────────────────
        self._pm_open_follow_window()
        self.pm_current_tick = max(self.pm_current_tick, tick)

        if news_type == "structured":
            subtype = news_data.get("structured_subtype")

            if subtype == "earnings":
                asset = news_data.get("asset")
                eps = news_data.get("value")

                # ── Stock A earnings (PRESERVED) ──────────────────────────────
                if asset == "A":
                    await self.handle_a_earnings(eps, tick)

                # ── Stock C earnings (NEW from c-pred) ───────────────────────
                elif asset == "C":
                    await self.handle_c_earnings(eps, tick)

            elif subtype == "cpi_print":
                # ── PM CPI sweep (NEW) ────────────────────────────────────────
                forecast = float(news_data.get("forecast", 0))
                actual = float(news_data.get("actual", 0))
                surprise = actual - forecast
                _PM_LOG.info(f"[CPI] actual={actual:.5f}  forecast={forecast:.5f}  "
                             f"surprise={surprise:+.5f}")
                if abs(surprise) >= config.PM_CPI_MIN_SURPRISE:
                    await self._pm_cpi_sweep(surprise)
                    await self._pm_check_sum_arb()

                # ── CPI also affects C fair value (NEW from c-pred) ──────────
                # CPI surprise shifts PM probabilities → E[Δr] changes → C fair value changes
                if self.c_fair_value.calibrated:
                    e_dr = self._get_e_delta_r()
                    if e_dr is not None:
                        fair_c = self.c_fair_value.compute(e_dr)
                        if fair_c is not None:
                            c_mid = self._get_current_mid_c()
                            if c_mid is not None:
                                signal = self.c_fair_value.cross_asset_signal(c_mid)
                                print(f"[C CPI] fair={fair_c:.0f} | mid={c_mid:.0f} | signal={signal:.1f}")
                                if abs(signal) >= config.CROSS_ASSET_THRESHOLD:
                                    await self._c_cross_asset_trade(signal)

        else:
            # Unstructured news
            symbol = news_release.get("symbol")
            content = news_data.get("content", "")

            # ── Stock A news (PRESERVED) ──────────────────────────────────────
            if symbol == "A":
                self.recent_a_news.append((tick, "news"))

                news_for_classifier = {"symbol": symbol, "content": content}
                sentiment, confidence = self.sentiment.classify(news_for_classifier)

                print(f"\n[NEWS] A-symbol: {content[:60]}...")
                print(f"[NEWS] Sentiment: {sentiment}, confidence: {confidence:.2f}")

                if sentiment in ["bullish", "bearish"]:
                    self.last_a_news_sentiment = sentiment
                    print(f"[NEWS] Updated last sentiment: {sentiment}")

                if sentiment in ["bullish", "bearish"]:
                    await self.handle_a_news_trade(sentiment, confidence, content)
                else:
                    print(f"[NEWS] Low confidence or neutral, not trading")

            # ── PM unstructured news (NEW) ────────────────────────────────────
            # Process ALL unstructured news for PM (not just non-A)
            if content.strip():
                msg_type = news_data.get("type", "") if isinstance(news_data, dict) else ""
                direction, tier = self._pm_score_headline(content, msg_type)

                if msg_type == "FedSpeak" and direction != 0 and tier > 1:
                    tier = max(1, tier - 1)

                _PM_LOG.info(f"[NEWS] type={msg_type!r}  tier={tier}  dir={direction:+d}  {content[:120]}")
                if direction != 0 and tier != 0:
                    buy_qty = config.PM_NEWS_BUY_SIZE[tier]
                    sell_qty = config.PM_NEWS_SELL_SIZE[tier]
                    if direction > 0:
                        buy_sym, sell_sym = "R_HIKE", "R_CUT"
                    else:
                        buy_sym, sell_sym = "R_CUT", "R_HIKE"
                    if buy_qty > 0:
                        await self._pm_news_sweep(buy_sym, sell_sym, buy_qty, sell_qty)
                    await self._pm_check_sum_arb()

    async def bot_handle_market_resolved(self, market_id: str, winning_symbol: str, tick: int):
        _PM_LOG.info(f"[RESOLVED] {market_id} → {winning_symbol}")
        _PM_LOG.info(f"[FINAL POS] {self._pm_pos_str()}")
        _PM_LOG.info(f"[PNL] arb={self.pm_pnl['arb']:+d}  cpi={self.pm_pnl['cpi']:+d}  "
                     f"news={self.pm_pnl['news']:+d}  follow={self.pm_pnl['follow']:+d}  "
                     f"total={self.pm_pnl['total']:+d}")

    async def bot_handle_settlement_payout(self, user: str, market_id: str, amount: int, tick: int):
        _PM_LOG.info(f"[PAYOUT] {market_id}: {amount}")

    # ========================================================================
    # MERGED TRADE LOOPS
    # ========================================================================

    async def trade_loop(self):
        """
        Stock A periodic trading loop (PRESERVED).

        Only quotes when:
        - Calibrated (>=3 samples)
        - Not in sniping mode
        - Not in post-news mode
        """
        while True:
            await asyncio.sleep(5)

            # Check if we should quote
            if not self.fair_value.is_calibrated_a():
                continue

            if self.sniping_mode_active:
                continue

            if self.post_news_mode_until is not None and time.time() < self.post_news_mode_until:
                continue

            # Get fair value
            if self.current_eps_a is None:
                continue

            fair = self.fair_value.fair_value_a(self.current_eps_a)
            if fair is None:
                continue

            # Cancel and re-quote
            await self.cancel_all_orders("A")
            await self.quote_around("A", fair)

            # PNL logging
            pos_a = self.positions.get("A", 0)
            cash = self.positions.get("cash", 0)
            mtm = cash + pos_a * fair
            print(f"[PNL] cash={cash} | pos_A={pos_a} | fair_A={fair:.0f} | mtm={mtm:.0f}")

    async def pm_trade_loop(self):
        """
        Prediction Market periodic loop (NEW from c-strat-krishi).

        Handles:
        - Stale directional order cancellation
        - Arb housekeeping
        - Periodic arb checks
        - Status logging
        """
        await asyncio.sleep(2)
        _PM_LOG.info(f"[PM] Trade loop started. Books: {list(self.order_books.keys())}")

        while True:
            await asyncio.sleep(config.PM_TRADE_INTERVAL)
            self.pm_loop_count += 1
            try:
                if self.pm_directional_orders:
                    await self._pm_cancel_stale_directional()

                if self.pm_arb_busy and self.pm_loop_count % 3 == 0:
                    await self._pm_cancel_arb()

                await self._pm_check_sum_arb()

                # Status log every 30 loops (~15s)
                if self.pm_loop_count % 30 == 0:
                    pm_mids = {s: self._pm_mid(s) for s in config.PM_SYMS}
                    mid_str = "  ".join(
                        f"{s}={pm_mids[s]:.0f}" if pm_mids[s] else f"{s}=?" for s in config.PM_SYMS)
                    mtm = sum(self._pm_pos(s) * (pm_mids[s] or 0) for s in config.PM_SYMS)
                    _PM_LOG.info(
                        f"[STATUS] tick={self.pm_current_tick}  {self._pm_pos_str()}  "
                        f"mids: {mid_str}  "
                        f"arb={self.pm_pnl['arb']:+d}  cpi={self.pm_pnl['cpi']:+d}  "
                        f"news={self.pm_pnl['news']:+d}  follow={self.pm_pnl['follow']:+d}  "
                        f"cash={self.pm_pnl['total']:+d}  mtm={mtm:+.0f}"
                    )

            except Exception as e:
                _PM_LOG.error(f"[LOOP ERR] {e}", exc_info=True)

    async def c_trade_loop(self):
        """
        Stock C periodic trading loop (NEW from c-pred).

        Computes C fair value from PM probabilities and trades when
        the cross-asset signal exceeds the threshold.
        """
        await asyncio.sleep(3)  # wait for books to populate
        print("[C] Trade loop started")

        while True:
            await asyncio.sleep(config.C_TRADE_INTERVAL)
            try:
                # Compute E[Δr] from PM book mids
                e_dr = self._get_e_delta_r()
                if e_dr is None:
                    continue

                # If we have EPS but haven't calibrated yet, try now
                if not self.c_fair_value.calibrated and self.current_eps_c is not None:
                    c_mid = self._get_current_mid_c()
                    if c_mid is not None:
                        self.c_fair_value.calibrate(c_mid, self.current_eps_c, e_dr)

                if not self.c_fair_value.calibrated:
                    continue

                # Compute fair value from current PM probabilities
                fair_c = self.c_fair_value.compute(e_dr)
                if fair_c is None:
                    continue

                # Cross-asset sweep if signal exceeds threshold
                c_mid = self._get_current_mid_c()
                if c_mid is not None:
                    signal = self.c_fair_value.cross_asset_signal(c_mid)
                    if abs(signal) >= config.CROSS_ASSET_THRESHOLD:
                        await self._c_cross_asset_trade(signal)

                # Quote around fair value
                await self.cancel_all_orders("C")
                await self.quote_around("C", fair_c)

                # PNL logging
                pos_c = self.positions.get("C", 0)
                print(f"[C] fair={fair_c:.0f} | pos={pos_c} | E[Δr]={e_dr:.2f}bps")

            except Exception as e:
                print(f"[C LOOP ERR] {e}")

    # ========================================================================
    # CONNECTION & STARTUP
    # ========================================================================

    async def process_message(self, msg) -> None:
        """
        Override parent's process_message to handle EOF gracefully.
        Raises ConnectionError instead of calling exit(0).
        """
        if msg == grpc.aio.EOF:
            _LOGGER.info("End of GRPC stream. Attempting reconnection...")
            raise ConnectionError("gRPC stream ended")

        # Call parent implementation for all other messages
        await super().process_message(msg)

    async def start(self):
        """Start bot with ALL THREE trade loops and automatic reconnection."""
        # Launch all trade loops as independent tasks
        asyncio.create_task(self.trade_loop())
        asyncio.create_task(self.pm_trade_loop())
        asyncio.create_task(self.c_trade_loop())

        # Reconnection loop with exponential backoff
        backoff_s = 1.0
        max_backoff_s = 60.0
        first_connection = True

        while True:
            try:
                # Reset model on reconnection (indicates new round)
                if not first_connection:
                    _LOGGER.info("Reconnecting - resetting models for new round")
                    self.fair_value.reset_model_a()
                    self.c_fair_value.reset()
                    self.current_eps_c = None

                await self.connect()
                first_connection = False
                # If connect() returns normally, reset backoff
                backoff_s = 1.0
            except grpc.aio.AioRpcError as e:
                # Log full error details for debugging
                _LOGGER.error(f"gRPC error details: code={e.code()}, details={e.details()}")

                # Check if this is the "round ended" error
                try:
                    if e.code() == grpc.StatusCode.UNAVAILABLE and "Connection reset by peer" in str(e.details()):
                        _LOGGER.info("Round has ended (connection reset by peer). Shutting down gracefully.")
                        break
                except:
                    pass

                # Other gRPC errors - attempt reconnection
                _LOGGER.warning(f"gRPC error: {e}. Reconnecting in {backoff_s:.1f}s...")
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, max_backoff_s)
            except ConnectionError as e:
                _LOGGER.warning(f"Connection lost: {e}. Reconnecting in {backoff_s:.1f}s...")
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, max_backoff_s)
            except Exception as e:
                _LOGGER.error(f"Unexpected error in connection: {e}. Reconnecting in {backoff_s:.1f}s...")
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, max_backoff_s)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    my_client = MyXchangeClient(
        "uchicago.exchange:3333",
        "maryland_uiuc",
        "torch-karma-beacon"
    )
    await my_client.start()


if __name__ == "__main__":
    asyncio.run(main())
