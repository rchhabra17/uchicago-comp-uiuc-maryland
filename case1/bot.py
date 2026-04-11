"""
Trading Bot for Stock A

Implements empirical findings from case1_stock_a_update_v2.md:
- Linear regression fair value model (Change 1, 2)
- 8-second sniping window after earnings (Change 3)
- Sentiment-based trading on A-news (Change 4)
- Stock C code COMMENTED OUT for A-only testing
"""

from typing import Optional, Deque
from collections import deque
import time
import asyncio
import logging
import grpc

from utcxchangelib import XChangeClient, Side
import config
from fair_value import FairValueEngine
from risk import RiskManager
from news_sentiment import NewsSentimentClassifier

_LOGGER = logging.getLogger("xchange-client")


class MyXchangeClient(XChangeClient):

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password)

        # Fair value and risk
        self.fair_value = FairValueEngine()
        self.risk = RiskManager()
        self.sentiment = NewsSentimentClassifier()

        # Mid price tracking for settled window averaging
        # Stores (timestamp, mid) tuples, kept for last 30 seconds
        self.recent_mids_a: Deque[tuple] = deque(maxlen=300)  # ~10 updates/sec × 30s

        # Contamination tracking: recent A-related news events
        # Stores (tick, event_type) tuples
        self.recent_a_news: list = []

        # Sniping mode state
        self.sniping_mode_active = False
        self.sniping_start_time: Optional[float] = None

        # News trading state
        self.post_news_mode_until: Optional[float] = None
        self.news_entry_mid: Optional[float] = None
        self.news_direction: Optional[int] = None  # +1 for bullish, -1 for bearish

        # Sentiment tracking for earnings prediction
        # Tracks last A-news sentiment to inform pre-earnings position decisions
        self.last_a_news_sentiment: Optional[str] = None  # "bullish", "bearish", or None

        # Current EPS (updated on each earnings)
        self.current_eps_a: Optional[float] = None

        # PnL tracking
        self.pnl = 0

    # ========================================================================
    # ORDER MANAGEMENT
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
    # MID PRICE TRACKING
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
    # CALIBRATION (Changes 1, 2)
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
    # SNIPING MODE (Change 3)
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
    # NEWS SENTIMENT TRADING (Change 4)
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
            bids_sorted = bids_sorted = sorted(((px, qty) for px, qty in book.bids.items() if qty > 0), reverse=True)
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
    # EVENT HANDLERS
    # ========================================================================

    async def bot_handle_book_update(self, symbol: str):
        """
        Hook for book updates.

        Triggers:
        - Mid price tracking (for calibration averaging)
        - Sniping logic (if sniping mode active)
        - News exit check (if post-news mode active)
        """
        if symbol != "A":
            return

        # Track mid price for calibration averaging
        current_mid = self._get_current_mid_a()
        if current_mid is not None:
            self.recent_mids_a.append((time.time(), current_mid))

        # Sniping mode hook
        if self.sniping_mode_active:
            await self.snipe_on_book_update()

        # News exit check
        if self.post_news_mode_until is not None and time.time() < self.post_news_mode_until:
            await self.check_news_exit()

    async def bot_handle_order_fill(self, order_id: str, qty: int, price: int):
        """Log fills with edge calculation."""
        info = self.open_orders.get(order_id)
        if info:
            symbol = info[0].symbol
            side = "BUY" if info[0].side == 1 else "SELL"

            if symbol == "A" and self.current_eps_a is not None:
                fair = self.fair_value.fair_value_a(self.current_eps_a)
                if fair:
                    edge = (fair - price) if side == "BUY" else (price - fair)
                    print(f"[FILL] {symbol} {side} {qty}@{price} | fair={fair:.0f} | edge={edge:.0f}")

    async def bot_handle_news(self, news_release: dict):
        """
        Handle all news events.

        Routes to appropriate handlers:
        - A earnings → handle_a_earnings
        - A-symbol unstructured → sentiment classifier → handle_a_news_trade
        - C news → COMMENTED OUT
        - Macro news → ignored for A
        """
        news_type = news_release["kind"]
        news_data = news_release["new_data"]
        tick = news_release["tick"]

        if news_type == "structured":
            subtype = news_data.get("structured_subtype")

            if subtype == "earnings":
                asset = news_data.get("asset")
                eps = news_data.get("value")

                if asset == "A":
                    await self.handle_a_earnings(eps, tick)

                # C earnings COMMENTED OUT
                # elif asset == "C":
                #     ...

            # CPI COMMENTED OUT
            # elif subtype == "cpi_print":
            #     ...

        else:
            # Unstructured news
            symbol = news_release.get("symbol")
            content = news_data.get("content", "")

            # Only process A-symbol news
            if symbol == "A":
                # Track for contamination
                self.recent_a_news.append((tick, "news"))

                # Classify sentiment - pass properly structured dict
                news_for_classifier = {"symbol": symbol, "content": content}
                sentiment, confidence = self.sentiment.classify(news_for_classifier)

                print(f"\n[NEWS] A-symbol: {content[:60]}...")
                print(f"[NEWS] Sentiment: {sentiment}, confidence: {confidence:.2f}")

                # Track last sentiment for earnings prediction
                if sentiment in ["bullish", "bearish"]:
                    self.last_a_news_sentiment = sentiment
                    print(f"[NEWS] Updated last sentiment: {sentiment}")

                # Trade on any directional signal (bullish or bearish)
                if sentiment in ["bullish", "bearish"]:
                    await self.handle_a_news_trade(sentiment, confidence, content)
                else:
                    print(f"[NEWS] Low confidence or neutral, not trading")

            # Macro news (no symbol) → ignore for A
            elif symbol is None:
                print(f"[NEWS] Macro news (no symbol), ignoring for A")

    async def trade_loop(self):
        """
        Periodic trading loop for passive quoting.

        Only quotes when:
        - Calibrated (≥3 samples)
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
        """Start bot with trade loop and automatic reconnection."""
        asyncio.create_task(self.trade_loop())

        # Reconnection loop with exponential backoff
        backoff_s = 1.0
        max_backoff_s = 60.0
        first_connection = True

        while True:
            try:
                # Reset model on reconnection (indicates new round)
                if not first_connection:
                    _LOGGER.info("Reconnecting - resetting earnings model for new round")
                    self.fair_value.reset_model_a()

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
    my_client = MyXchangeClient(
        "practice.uchicago.exchange:3333",
        "maryland_uiuc",
        "torch-karma-beacon"
    )
    await my_client.start()


if __name__ == "__main__":
    asyncio.run(main())
