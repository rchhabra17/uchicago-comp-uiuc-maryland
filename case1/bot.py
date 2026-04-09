from typing import Optional
from utcxchangelib import XChangeClient, Side
import asyncio
import config
from fair_value import FairValueEngine
from risk import RiskManager


class MyXchangeClient(XChangeClient):

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password)
        self.fair_value = FairValueEngine()
        self.risk = RiskManager()
        self.pnl = 0
        self.drift = {"A": 0.0, "C": 0.0}


    # DUMMY BOILERPLATE CODE
    # async def trade(self):
    #     """This is a simple example bot that places orders and prints updates."""
    #     await asyncio.sleep(5)

    #     # Place an order
    #     await self.place_order("A", 10, Side.BUY, 100)

    #     # You can also look at order books like this
    #     for security, book in self.order_books.items():
    #         if book.bids or book.asks:
    #             sorted_bids = sorted((k,v) for k,v in book.bids.items() if v != 0)
    #             sorted_asks = sorted((k,v) for k,v in book.asks.items() if v != 0)
    #             print(f"Bids for {security}:\n{sorted_bids}")
    #             print(f"Asks for {security}:\n{sorted_asks}")



    # ---- Order Management ----
    async def cancel_all_orders(self, symbol: str):
        to_cancel = [
            oid for oid, info in self.open_orders.items()
            if info[0].symbol == symbol
        ]
        for oid in to_cancel:
            await self.cancel_order(oid)

    async def sweep_book(self, symbol: str, fair: float):
        book = self.order_books[symbol]
        edge = config.PARAMS[symbol]["sweep_edge"]
        pos = self.positions.get(symbol, 0)

        # Only sweep buy side if not already too long
        if pos < 50:
            for price in sorted(book.asks.keys()):
                qty = book.asks[price]
                if qty > 0 and price < fair - edge:
                    buy_qty = min(qty, 50 - pos)  # don't exceed limit
                    if buy_qty > 0:
                        await self.place_order(symbol, buy_qty, Side.BUY, price)
                        pos += buy_qty
                else:
                    break

        # Only sweep sell side if not already too short
        if pos > -50:
            for price in sorted(book.bids.keys(), reverse=True):
                qty = book.bids[price]
                if qty > 0 and price > fair + edge:
                    sell_qty = min(qty, 50 + pos)
                    if sell_qty > 0:
                        await self.place_order(symbol, sell_qty, Side.SELL, price)
                        pos -= sell_qty
                else:
                    break
    
    async def quote_around(self, symbol: str, fair: float):
        spread = config.PARAMS[symbol]["spread"]
        size = config.PARAMS[symbol]["order_size"]
        pos = self.positions.get(symbol, 0)

        # 1. SMART MONEY ADJUSTMENT (Drift)
        book = self.order_books.get(symbol)
        adjusted_fair = fair
        if book and book.bids and book.asks:
            bids = [k for k, v in book.bids.items() if v > 0]
            asks = [k for k, v in book.asks.items() if v > 0]
            if bids and asks:
                market_mid = (max(bids) + min(asks)) / 2
                disparity = market_mid - fair
                
                # If disparity is large, pull fair slowly towards market (rolling 95/5 EMA)
                self.drift[symbol] = 0.95 * self.drift.get(symbol, 0.0) + 0.05 * disparity
                adjusted_fair = fair + self.drift[symbol]

        # 2. VOLATILITY / SPREAD EXPANSION
        inventory_spread_expansion = int(abs(pos) * 0.1)  # Widen 1 tick per 10 units
        effective_spread = spread + inventory_spread_expansion

        # 3. SKEW AND ASYMMETRIC QUOTING / SQUELCH
        skew_mult = config.PARAMS[symbol].get("skew_mult", 0.15)
        skew = -pos * skew_mult

        bid_price = int(adjusted_fair + skew - effective_spread)
        ask_price = int(adjusted_fair + skew + effective_spread)

        # Force squelch if inventory is highly critical (> 30 is 75% of max 40)
        if pos >= 30:
            # Too long: un-quote the bid entirely, sell very aggressively exactly at mid
            bid_price = int(adjusted_fair - 1000)
            ask_price = int(adjusted_fair - effective_spread / 2)
        elif pos <= -30:
            # Too short: un-quote the ask entirely, buy very aggressively exactly at mid
            ask_price = int(adjusted_fair + 1000)
            bid_price = int(adjusted_fair + effective_spread / 2)

        # Hard cutoffs — don't add to a big position
        if pos < 40 and self.risk.can_trade(symbol, "buy", size, config.MAX_POSITION):
            await self.place_order(symbol, size, Side.BUY, bid_price)

        if pos > -40 and self.risk.can_trade(symbol, "sell", size, config.MAX_POSITION):
            await self.place_order(symbol, size, Side.SELL, ask_price)
    
    async def calibrate_after_delay(self, symbol, eps):
        """Wait for market to settle after first earnings, then learn PE."""
        await asyncio.sleep(10)  # 10 seconds for price to settle
        book = self.order_books.get(symbol)
        if book and book.bids and book.asks:
            bids = [k for k, v in book.bids.items() if v > 0]
            asks = [k for k, v in book.asks.items() if v > 0]
            if bids and asks:
                mid = (max(bids) + min(asks)) / 2
                self.fair_value.calibrate_pe(mid)



    # ---- Event Handlers ----

    async def bot_handle_order_fill(self, order_id: str, qty: int, price: int):
        info = self.open_orders.get(order_id)
        if info:
            symbol = info[0].symbol
            side = "BUY" if info[0].side == 1 else "SELL"
            fair = self.fair_value.get(symbol) or 0
            edge = (fair - price) if side == "BUY" else (price - fair)
            print(f"[FILL] {symbol} {side} {qty}@{price} | fair={fair:.0f} | edge={edge:.0f}")

    async def bot_handle_cancel_response(self, order_id: str, success: bool, error: Optional[str] = None):
        if success:
            # print(f"[CANCEL] {order_id} cancelled")
            pass

    async def bot_handle_order_rejected(self, order_id: str, reason: str) -> None:
        # print(f"[REJECTED] {order_id} - {reason}")
        pass

    async def bot_handle_trade_msg(self, symbol: str, price: int, qty: int):
        if symbol == "A":
            print(f"TRADE A: price={price}")

    async def bot_handle_swap_response(self, swap: str, qty: int, success: bool):
        pass

    async def bot_handle_news(self, news_release: dict):
        news_type = news_release["kind"]
        news_data = news_release["new_data"]
        tick = news_release["tick"]

        if news_type == "structured":
            subtype = news_data["structured_subtype"]

            if subtype == "earnings":
                asset = news_data["asset"]
                value = news_data["value"]

                # if asset == "A":
                #     book = self.order_books.get("A")
                #     mid = None
                #     if book and book.bids and book.asks:
                #         mid = (max(book.bids.keys()) + min(book.asks.keys())) / 2
                #     print(f"A EARNINGS: EPS={value}, current_mid={mid}, ratio={mid/value if mid and value else 'N/A'}")

                if asset == "A":
                    book = self.order_books.get("A")
                    mid = None
                    if book and book.bids and book.asks:
                        bids = [k for k, v in book.bids.items() if v > 0]
                        asks = [k for k, v in book.asks.items() if v > 0]
                        if bids and asks:
                            mid = (max(bids) + min(asks)) / 2

                    if self.fair_value.calibrating:
                        # First earnings of the round — learn PE after market settles
                        print(f"[EARNINGS] A: EPS={value}, calibrating... waiting for market to settle")
                        self.fair_value.eps_a = value
                        asyncio.create_task(self.calibrate_after_delay("A", value))
                    else:
                        # Subsequent earnings — trade on it
                        new_fair = self.fair_value.update_a(value)
                        print(f"[EARNINGS] A: EPS={value}, fair={new_fair}, market_mid={mid}")
                        if new_fair is not None:
                            await self.cancel_all_orders("A")
                            await self.sweep_book("A", new_fair)
                            tick_in_day = tick % 450
                            if tick_in_day / 5 < 85:
                                await self.quote_around("A", new_fair)

                elif asset == "C":
                    new_fair = self.fair_value.update_c(value)
                    print(f"[EARNINGS] C: EPS={value}, fair={new_fair}")
                    if new_fair is not None:
                        await self.cancel_all_orders("C")
                        await self.sweep_book("C", new_fair)
                        tick_in_day = tick % 450
                        if tick_in_day / 5 < 85:
                            await self.quote_around("C", new_fair)

            elif subtype == "cpi_print":
                forecast = news_data.get("forecast")
                actual = news_data.get("actual")
                if forecast is not None and actual is not None:
                    self.fair_value.fed.update_from_cpi(forecast, actual)
                    new_fair_c = self.fair_value.recompute_c()
                    print(f"[CPI PRINT] forecast={forecast}, actual={actual}, new_fair_C={new_fair_c}")
                    if new_fair_c is not None:
                        await self.cancel_all_orders("C")
                        await self.sweep_book("C", new_fair_c)
                        await self.quote_around("C", new_fair_c)
        else:
            pass  # unstructured news — Phase 3

    async def bot_handle_market_resolved(self, market_id: str, winning_symbol: str, tick: int):
        print(f"Market {market_id} resolved: winner is {winning_symbol}")

    async def bot_handle_settlement_payout(self, user: str, market_id: str, amount: int, tick: int):
        print(f"Settlement payout: {amount} from {market_id}")

    async def trade_loop(self):
        while True:
            await asyncio.sleep(5)

            def get_mid_fuzzy(keyword):
                for sym, book in self.order_books.items():
                    if keyword.lower() in sym.lower():
                        if book and book.bids and book.asks:
                            bids = [k for k, v in book.bids.items() if v > 0]
                            asks = [k for k, v in book.asks.items() if v > 0]
                            if bids and asks:
                                return (max(bids) + min(asks)) / 2
                return None

            hike_mid = get_mid_fuzzy("hike")
            hold_mid = get_mid_fuzzy("hold")
            cut_mid = get_mid_fuzzy("cut")
            
            if hike_mid and hold_mid and cut_mid:
                self.fair_value.fed.update_from_book_mids(hike_mid, hold_mid, cut_mid)
                self.fair_value.recompute_c()
            print(f"[DEBUG] Available symbols: {list(self.order_books.keys())}")  # Debug just in case

            for symbol in ["A"]:
                fair = self.fair_value.get(symbol)
                using_market_mid = False

                if fair is None:
                    book = self.order_books.get(symbol)
                    if book and book.bids and book.asks:
                        bids = [k for k, v in book.bids.items() if v > 0]
                        asks = [k for k, v in book.asks.items() if v > 0]
                        if bids and asks:
                            fair = (max(bids) + min(asks)) / 2
                            using_market_mid = True

                if fair is None:
                    continue

                print(f"[QUOTE] {symbol}: fair={fair:.0f}")
                await self.cancel_all_orders(symbol)

                if using_market_mid:
                    # Pre-calibration: quote wider, smaller size
                    pos = self.positions.get(symbol, 0)
                    skew_mult = config.PARAMS[symbol].get("skew_mult", 0.15)
                    skew = -pos * skew_mult
                    spread = config.PARAMS[symbol].get("spread", 6) + 4
                    bid_price = int(fair + skew - spread)
                    ask_price = int(fair + skew + spread)
                    if pos < 20:
                        await self.place_order(symbol, 5, Side.BUY, bid_price)
                    if pos > -20:
                        await self.place_order(symbol, 5, Side.SELL, ask_price)
                else:
                    await self.quote_around(symbol, fair)
                

                    # ---- C: same structure as A ----
            for symbol in ["C"]:
                fair = self.fair_value.get(symbol)
                using_market_mid = False

                if fair is None:
                    book = self.order_books.get(symbol)
                    if book and book.bids and book.asks:
                        bids = [k for k, v in book.bids.items() if v > 0]
                        asks = [k for k, v in book.asks.items() if v > 0]
                        if bids and asks:
                            fair_mid = (max(bids) + min(asks)) / 2
                            # Instead of just using market mid indefinitely, we can infer C's initial EPS
                            self.fair_value.infer_eps_c(fair_mid)
                            fair = self.fair_value.get(symbol)

                if fair is None:
                    continue

                print(f"[QUOTE] {symbol}: fair={fair:.0f}")
                await self.cancel_all_orders(symbol)

                if using_market_mid:
                    # Pre-earnings: quote wider, smaller size, tighter position limit
                    pos = self.positions.get(symbol, 0)
                    skew_mult = config.PARAMS[symbol].get("skew_mult", 0.15)
                    skew = -pos * skew_mult
                    spread = config.PARAMS[symbol].get("spread", 2) + 2
                    bid_price = int(fair + skew - spread)
                    ask_price = int(fair + skew + spread)
                    if pos < 10:
                        await self.place_order(symbol, 5, Side.BUY, bid_price)
                    if pos > -10:
                        await self.place_order(symbol, 5, Side.SELL, ask_price)
                else:
                    await self.quote_around(symbol, fair)

            # PNL logging (unchanged)
            cash = self.positions.get("cash", 0)
            pos_a = self.positions.get("A", 0)
            fair_a = self.fair_value.get("A") or 0
            mark_to_market = cash + (pos_a * fair_a)
            print(f"[PNL] cash={cash} | pos_A={pos_a} | mtm={mark_to_market:.0f}")
            pos_c  = self.positions.get("C", 0)
            fair_c = self.fair_value.get("C") or 0
            # update the existing print to include C:
            print(f"[PNL] cash={cash} | pos_A={pos_a} | pos_C={pos_c} | mtm={cash + pos_a*fair_a + pos_c*fair_c:.0f}")

    async def start(self):
        asyncio.create_task(self.trade_loop())
        await self.connect()


async def main():
    SERVER = 'https://practice.uchicago.exchange'
    my_client = MyXchangeClient("practice.uchicago.exchange:3333", "maryland_uiuc", "torch-karma-beacon")
    await my_client.start()


if __name__ == "__main__":
    asyncio.run(main())# Debug
