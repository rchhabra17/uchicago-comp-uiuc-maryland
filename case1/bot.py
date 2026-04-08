from typing import Optional
from utcxchangelib import XChangeClient, Side
import asyncio
import config
from fair_value import FairValueEngine
from risk import RiskManager

# B imports
from options import (
    midpoint,
    best_bid_ask,
    detect_box_signal,
    call_symbol,
    put_symbol,
    compute_fair_b,
    find_stale_orders,
    detect_butterfly_orders,
    detect_vertical_orders,
)

class MyXchangeClient(XChangeClient):

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password)
        self.fair_value = FairValueEngine()
        self.risk = RiskManager()
        self.pnl = 0
        self.b_cash = 0.0


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

    # helper to never place order with price <= 0 for B rn
    def _valid_price(self, price) -> bool:
        return price is not None and price > 0
    
    # helper to track order IDs when you place them - B

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

        # Skew: if long, push quotes down (cheaper ask to offload)
        skew = -pos * 0.15  # tune this — at pos=30, skew = -4.5

        bid_price = int(fair + skew - spread)
        ask_price = int(fair + skew + spread)

        # Hard cutoffs — don't add to a big position
        if pos < 40 and self.risk.can_trade(symbol, "buy", size, config.MAX_POSITION):
            await self.place_order(symbol, size, Side.BUY, bid_price)

        if pos > -40 and self.risk.can_trade(symbol, "sell", size, config.MAX_POSITION):
            await self.place_order(symbol, size, Side.SELL, ask_price)

    
    
    
    

        
    async def bot_handle_book_update(self, symbol:str) -> None:
        b_relevant = {f"B_C_{k}" for k in config.B_STRIKES} | {f"B_P_{k}" for k in config.B_STRIKES}
        if symbol not in b_relevant:
            return

        orders_to_fire = []

        # Strategy 3: Vertical bounds — fastest, model-free, run first
        if not self.risk.on_cooldown("vertical", 0.2):
            orders_to_fire.extend(
                detect_vertical_orders(self.order_books, config.B_STRIKES, config.B_VERTICAL_SIZE)
            )

        # Strategy 2: Butterfly arb — model-free
        if not self.risk.on_cooldown("butterfly", 0.2):
            orders_to_fire.extend(
                detect_butterfly_orders(self.order_books, config.B_BUTTERFLY_SIZE)
            )

        # Strategy 1: Stale quote sniping — main edge
        fair_b = compute_fair_b(self.order_books, config.B_STRIKES)
        if fair_b is not None and not self.risk.on_cooldown("snipe", 0.2):
            print(f"[FAIR_B] {fair_b:.1f}") # The [FAIR_B] print tells you on competition day exactly when Strategy 1 is activating and by how much fair_b moved. 
            orders_to_fire.extend(
                find_stale_orders(self.order_books, fair_b, config.B_STRIKES, config.B_SWEEP_EDGE, config.B_SNIPE_ORDER_SIZE)
            )
        # Moved check: not sure if we need - Strategy 1 only activates when fair_b shifts by more than 3 points since last time, basically ignores noise 
        # and only fires when required saving computaion but lwk its fast enough that we are not wasting time - TBD

        # if fair_b is not None:
        #     moved = self.last_fair_b is None or abs(fair_b - self.last_fair_b) > 3.0
        #     self.last_fair_b = fair_b
        #     if moved and not self.risk.on_cooldown("snipe", 0.2):
        #         print(f"[FAIR_B] {fair_b:.1f} moved from {self.last_fair_b:.1f}")
        #         orders_to_fire.extend(
        #             find_stale_orders(self.order_books, fair_b, config.B_STRIKES, config.B_SWEEP_EDGE, config.B_SNIPE_ORDER_SIZE)
        #         )

        # Box spreads — existing logic, now inline
        for k1, k2 in config.B_BOX_PAIRS:
            if self.risk.on_cooldown(f"box_{k1}_{k2}", config.B_SIGNAL_COOLDOWN):
                continue
            c1_book = self.order_books.get(call_symbol(k1))
            c2_book = self.order_books.get(call_symbol(k2))
            p1_book = self.order_books.get(put_symbol(k1))
            p2_book = self.order_books.get(put_symbol(k2))
            if not all([c1_book, c2_book, p1_book, p2_book]):
                continue
            signal = detect_box_signal(
                call_k1=midpoint(c1_book), call_k2=midpoint(c2_book),
                put_k1=midpoint(p1_book),  put_k2=midpoint(p2_book),
                k1=k1, k2=k2,
                threshold=config.B_BOX_THRESHOLD, qty=config.B_BOX_ORDER_SIZE,
            )
            if signal is None:
                continue
            c1_bid, c1_ask = best_bid_ask(c1_book)
            c2_bid, c2_ask = best_bid_ask(c2_book)
            p1_bid, p1_ask = best_bid_ask(p1_book)
            p2_bid, p2_ask = best_bid_ask(p2_book)
            if signal.action == "BUY_BOX" and all(p is not None for p in [c1_ask, c2_bid, p2_ask, p1_bid]):
                orders_to_fire += [
                    (call_symbol(k1), signal.qty, Side.BUY,  c1_ask),
                    (call_symbol(k2), signal.qty, Side.SELL, c2_bid),
                    (put_symbol(k2),  signal.qty, Side.BUY,  p2_ask),
                    (put_symbol(k1),  signal.qty, Side.SELL, p1_bid),
                ]
            elif signal.action == "SELL_BOX" and all(p is not None for p in [c1_bid, c2_ask, p2_bid, p1_ask]):
                orders_to_fire += [
                    (call_symbol(k1), signal.qty, Side.SELL, c1_bid),
                    (call_symbol(k2), signal.qty, Side.BUY,  c2_ask),
                    (put_symbol(k2),  signal.qty, Side.SELL, p2_bid),
                    (put_symbol(k1),  signal.qty, Side.BUY,  p1_ask),
                ]

        # Deduplicate by symbol — first order for each symbol wins
        valid_orders = []
        for sym, qty, side, price in orders_to_fire:
            delta = qty if side == Side.BUY else -qty
            if self.risk.can_trade_b_package(
                {sym: delta}, config.B_MAX_POSITION, config.B_MAX_GROSS_FAMILY
            ):
                valid_orders.append((sym, qty, side, price))

        # Fire everything concurrently — single tick
        if valid_orders:
            for sym, qty, side, price in valid_orders:
                print(f"[FIRE] {sym} {side} {qty}@{price}")
            await asyncio.gather(*[
                self.place_order(sym, qty, side, price)
                for sym, qty, side, price in valid_orders
            ])


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


    def print_b_pnl(self):
        b_pos = self.risk.get_position("B")
        c950 = self.risk.get_position("B_C_950")
        c1000 = self.risk.get_position("B_C_1000")
        c1050 = self.risk.get_position("B_C_1050")
        p950 = self.risk.get_position("B_P_950")
        p1000 = self.risk.get_position("B_P_1000")
        p1050 = self.risk.get_position("B_P_1050")

        mtm = self.compute_b_mtm()

        print(
            f"[B PNL] cash={self.b_cash:.0f} | "
            f"pos_B={b_pos} | "
            f"C950={c950} C1000={c1000} C1050={c1050} | "
            f"P950={p950} P1000={p1000} P1050={p1050} | "
            f"mtm={mtm:.0f}"
        )
    # ---- Event Handlers ----

    async def bot_handle_order_fill(self, order_id: str, qty: int, price: int):
        info = self.open_orders.get(order_id)
        if info:
            symbol = info[0].symbol
            side = "BUY" if info[0].side == 1 else "SELL"
            # fair = self.fair_value.get(symbol) or 0
            # edge = (fair - price) if side == "BUY" else (price - fair)
            # print(f"[FILL] {symbol} {side} {qty}@{price} | fair={fair:.0f} | edge={edge:.0f}")

            # Updates B cash only when B instrument fills - for isolated test
            self.risk.update_fill(symbol, side, qty)
            b_family = {"B", "B_C_950", "B_C_1000", "B_C_1050", "B_P_950", "B_P_1000", "B_P_1050"}

            if symbol in b_family:
                if side == "BUY":
                    self.b_cash -= qty * price
                else:
                    self.b_cash += qty * price

            print(f"[FILL] {symbol} {side} {qty}@{price}")

    # computes B only mark to market from current books - for isolated B test
    def compute_b_mtm(self):
        b_symbols = ["B", "B_C_950", "B_C_1000", "B_C_1050", "B_P_950", "B_P_1000", "B_P_1050"]
        mtm = self.b_cash

        for sym in b_symbols:
            pos = self.risk.get_position(sym)
            book = self.order_books.get(sym)
            if not book:
                continue

            bids = [p for p, q in book.bids.items() if q > 0]
            asks = [p for p, q in book.asks.items() if q > 0]
            if bids and asks:
                mid = (max(bids) + min(asks)) / 2
                mtm += pos * mid

        return mtm

    async def bot_handle_cancel_response(self, order_id: str, success: bool, error: Optional[str] = None):
        if success:
            # print(f"[CANCEL] {order_id} cancelled")
            pass
   

    async def bot_handle_order_rejected(self, order_id: str, reason: str) -> None:
        print(f"[REJECTED] {order_id} - {reason}")

    async def bot_handle_trade_msg(self, symbol: str, price: int, qty: int):
        # if symbol == "A":
        #     print(f"TRADE A: price={price}")
        pass

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
                        # if new_fair is not None:
                        #     await self.cancel_all_orders("A")
                        #     await self.sweep_book("A", new_fair)
                        #     tick_in_day = tick % 450
                        #     if tick_in_day / 5 < 85:
                        #         await self.quote_around("A", new_fair)

            elif subtype == "cpi_print":
                pass  # Phase 3

        else:
            pass  # unstructured news — Phase 3

    async def bot_handle_market_resolved(self, market_id: str, winning_symbol: str, tick: int):
        print(f"Market {market_id} resolved: winner is {winning_symbol}")

    async def bot_handle_settlement_payout(self, user: str, market_id: str, amount: int, tick: int):
        print(f"Settlement payout: {amount} from {market_id}")

    async def trade_loop(self):
        while True:
            await asyncio.sleep(5)

            # for symbol in ["A"]:
            #     fair = self.fair_value.get(symbol)
            #     using_market_mid = False

            #     if fair is None:
            #         book = self.order_books.get(symbol)
            #         if book and book.bids and book.asks:
            #             bids = [k for k, v in book.bids.items() if v > 0]
            #             asks = [k for k, v in book.asks.items() if v > 0]
            #             if bids and asks:
            #                 fair = (max(bids) + min(asks)) / 2
            #                 using_market_mid = True

            #     if fair is None:
            #         continue

            #     print(f"[QUOTE] {symbol}: fair={fair:.0f}")
            #     await self.cancel_all_orders(symbol)

            #     if using_market_mid:
            #         # Pre-calibration: quote wider, smaller size
            #         pos = self.positions.get(symbol, 0)
            #         skew = -pos * 0.15
            #         bid_price = int(fair + skew - 10)
            #         ask_price = int(fair + skew + 10)
            #         if pos < 20:
            #             await self.place_order(symbol, 5, Side.BUY, bid_price)
            #         if pos > -20:
            #             await self.place_order(symbol, 5, Side.SELL, ask_price)
            #     else:
            #         await self.quote_around(symbol, fair)

            # PNL logging (unchanged)
            # cash = self.positions.get("cash", 0)
            # pos_a = self.positions.get("A", 0)
            # fair_a = self.fair_value.get("A") or 0
            # mark_to_market = cash + (pos_a * fair_a)
            # print(f"[PNL] cash={cash} | pos_A={pos_a} | mtm={mark_to_market:.0f}")

            # print B MTM
            # print(f"[B MTM] {self.compute_b_mtm():.2f}")
            self.print_b_pnl()

    async def start(self):
        asyncio.create_task(self.trade_loop())
        await self.connect()


async def main():
    SERVER = '34.197.188.76'
    my_client = MyXchangeClient(f"{SERVER}:3333", "maryland_uiuc", "torch-karma-beacon")
    await my_client.start()


if __name__ == "__main__":
    asyncio.run(main())