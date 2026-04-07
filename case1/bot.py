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
    detect_parity_signal,
    detect_box_signal,
    call_symbol,
    put_symbol,
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

    
    async def execute_b_parity_trade(self, signal):
        strike = signal.strike
        qty = signal.qty

        c_sym = call_symbol(strike)
        p_sym = put_symbol(strike)
        b_sym = "B"

        await self.cancel_all_orders(c_sym)
        await self.cancel_all_orders(p_sym)
        await self.cancel_all_orders(b_sym)

        call_book = self.order_books.get(c_sym)
        put_book = self.order_books.get(p_sym)
        stock_book = self.order_books.get(b_sym)

        if not call_book or not put_book or not stock_book:
            return

        c_bid, c_ask = best_bid_ask(call_book)
        p_bid, p_ask = best_bid_ask(put_book)
        b_bid, b_ask = best_bid_ask(stock_book)

        if signal.action == "SELL_CALL_BUY_PUT_BUY_STOCK":
            if None in (c_bid, p_ask, b_ask):
                return
            if not all(self._valid_price(p) for p in [c_bid, p_ask, b_ask]):
                return
            
            changes = {
                c_sym: -qty,
                p_sym: qty,
                b_sym: qty,
            }

            if not self.risk.can_trade_b_package(
                changes,
                config.B_MAX_POSITION,
                config.B_MAX_GROSS_FAMILY,
            ):
                print(f"[B PARITY BLOCKED] strike={strike} risk")
                return

            print(f"[B PARITY] {signal.action} strike={strike} gap={signal.gap:.2f}")
            await self.place_order(c_sym, qty, Side.SELL, c_bid)
            await self.place_order(p_sym, qty, Side.BUY, p_ask)
            await self.place_order(b_sym, qty, Side.BUY, b_ask)


        elif signal.action == "BUY_CALL_SELL_PUT_SELL_STOCK":
            if None in (c_ask, p_bid, b_bid):
                return
            
            if not all(self._valid_price(p) for p in [c_ask, p_bid, b_bid]):
                return
            
            changes = {
                c_sym: qty,
                p_sym: -qty,
                b_sym: -qty,
            }

            if not self.risk.can_trade_b_package(
                changes,
                config.B_MAX_POSITION,
                config.B_MAX_GROSS_FAMILY,
            ):
                # print(f"[B PARITY BLOCKED] strike={strike} risk")
                return

            print(f"[B PARITY] {signal.action} strike={strike} gap={signal.gap:.2f}")
            await self.place_order(c_sym, qty, Side.BUY, c_ask)
            await self.place_order(p_sym, qty, Side.SELL, p_bid)
            await self.place_order(b_sym, qty, Side.SELL, b_bid)

    
    async def execute_b_box_trade(self, signal):
        k1, k2, qty = signal.k1, signal.k2, signal.qty

        c1 = call_symbol(k1)
        c2 = call_symbol(k2)
        p1 = put_symbol(k1)
        p2 = put_symbol(k2)

        for sym in [c1, c2, p1, p2]:
            await self.cancel_all_orders(sym)

        books = [self.order_books.get(sym) for sym in [c1, c2, p1, p2]]
        if any(book is None for book in books):
            return

        c1_bid, c1_ask = best_bid_ask(self.order_books[c1])
        c2_bid, c2_ask = best_bid_ask(self.order_books[c2])
        p1_bid, p1_ask = best_bid_ask(self.order_books[p1])
        p2_bid, p2_ask = best_bid_ask(self.order_books[p2])

        if signal.action == "BUY_BOX":
            if None in (c1_ask, c2_bid, p2_ask, p1_bid):
                return

            if not all(self._valid_price(p) for p in [c1_ask, c2_bid, p2_ask, p1_bid]):
                return

            changes = {
                c1: qty,
                c2: -qty,
                p2: qty,
                p1: -qty,
            }

            if not self.risk.can_trade_b_package(
                changes,
                config.B_MAX_POSITION,
                config.B_MAX_GROSS_FAMILY,
            ):
                print(f"[B BOX BLOCKED] {k1}-{k2} risk")
                return

            print(f"[B BOX] BUY_BOX {k1}-{k2} price={signal.price:.2f} fair={signal.fair:.2f}")
            await self.place_order(c1, qty, Side.BUY, c1_ask)
            await self.place_order(c2, qty, Side.SELL, c2_bid)
            await self.place_order(p2, qty, Side.BUY, p2_ask)
            await self.place_order(p1, qty, Side.SELL, p1_bid)

            

        elif signal.action == "SELL_BOX":
            if None in (c1_bid, c2_ask, p2_bid, p1_ask):
                return
            if not all(self._valid_price(p) for p in [c1_bid, c2_ask, p2_bid, p1_ask]):
                return
            changes = {
                c1: -qty,
                c2: qty,
                p2: -qty,
                p1: qty,
            }

            if not self.risk.can_trade_b_package(
                changes,
                config.B_MAX_POSITION,
                config.B_MAX_GROSS_FAMILY,
            ):
                print(f"[B BOX BLOCKED] {k1}-{k2} risk")
                return

            print(f"[B BOX] SELL_BOX {k1}-{k2} price={signal.price:.2f} fair={signal.fair:.2f}")
            await self.place_order(c1, qty, Side.SELL, c1_bid)
            await self.place_order(c2, qty, Side.BUY, c2_ask)
            await self.place_order(p2, qty, Side.SELL, p2_bid)
            await self.place_order(p1, qty, Side.BUY, p1_ask)

        
    async def bot_handle_book_update(self, symbol:str) -> None:
        b_relevant = {"B"} | {f"B_C_{k}" for k in config.B_STRIKES} | {f"B_P_{k}" for k in config.B_STRIKES}
        if symbol not in b_relevant:
            return

        # Need underlying B midpoint first
        b_book = self.order_books.get("B")
        if not b_book:
            return

        b_mid = midpoint(b_book)
        if b_mid is None:
            return

        # ----- Parity checks -----
        for strike in config.B_STRIKES:
            c_sym = call_symbol(strike)
            p_sym = put_symbol(strike)

            c_book = self.order_books.get(c_sym)
            p_book = self.order_books.get(p_sym)
            if not c_book or not p_book:
                continue

            c_mid = midpoint(c_book)
            p_mid = midpoint(p_book)

            c_bid, c_ask = best_bid_ask(c_book)
            p_bid, p_ask = best_bid_ask(p_book)
            b_bid, b_ask = best_bid_ask(b_book)
            c_spread = (c_ask - c_bid) if (c_bid and c_ask) else None
            p_spread = (p_ask - p_bid) if (p_bid and p_ask) else None
            b_spread = (b_ask - b_bid) if (b_bid and b_ask) else None

            signal = detect_parity_signal(
                call_mid=c_mid,
                put_mid=p_mid,
                stock_mid=b_mid,
                strike=strike,
                threshold=config.B_PARITY_THRESHOLD,
                qty=config.B_PARITY_ORDER_SIZE,
                call_spread=c_spread,   
                put_spread=p_spread,    
                stock_spread=b_spread, 
            )

            if signal is not None:
                signal_key = f"parity_{strike}"
                if not self.risk.on_cooldown(signal_key, config.B_SIGNAL_COOLDOWN):
                    await self.execute_b_parity_trade(signal)

        # ----- Box checks -----
        for k1, k2 in config.B_BOX_PAIRS:
            c1_book = self.order_books.get(call_symbol(k1))
            c2_book = self.order_books.get(call_symbol(k2))
            p1_book = self.order_books.get(put_symbol(k1))
            p2_book = self.order_books.get(put_symbol(k2))

            if not all([c1_book, c2_book, p1_book, p2_book]):
                continue

            signal = detect_box_signal(
                call_k1=midpoint(c1_book),
                call_k2=midpoint(c2_book),
                put_k1=midpoint(p1_book),
                put_k2=midpoint(p2_book),
                k1=k1,
                k2=k2,
                threshold=config.B_BOX_THRESHOLD,
                qty=config.B_BOX_ORDER_SIZE,
            )

            if signal is not None:
                signal_key = f"box_{k1}_{k2}"
                if not self.risk.on_cooldown(signal_key, config.B_SIGNAL_COOLDOWN):
                    await self.execute_b_box_trade(signal)


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