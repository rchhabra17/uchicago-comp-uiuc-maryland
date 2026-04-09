from dataclasses import dataclass
from typing import Optional
import math
import statistics
from utcxchangelib import Side

B_STRIKES = [950, 1000, 1050]
BOX_PAIRS = [(950, 1000), (1000, 1050), (950, 1050)]


@dataclass
class ParitySignal:
    strike: int
    gap: float
    action: str
    qty: int


@dataclass
class BoxSignal:
    k1: int
    k2: int
    price: float
    fair: float
    action: str
    qty: int


def call_symbol(strike: int) -> str:
    return f"B_C_{strike}"


def put_symbol(strike: int) -> str:
    return f"B_P_{strike}"


def best_bid_ask(book) -> tuple[Optional[int], Optional[int]]:
    if book is None:
        return None, None

    best_bid = max((p for p, q in book.bids.items() if q > 0), default=None)
    best_ask = min((p for p, q in book.asks.items() if q > 0), default=None)
    return best_bid, best_ask


def midpoint(book) -> Optional[float]:
    bid, ask = best_bid_ask(book)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def parity_gap(
    call_mid: float,
    put_mid: float,
    stock_mid: float,
    strike: int,
    r: float = 0.05,      # risk-free rate — tune this
    T: float = 1/52,      # ~1 week to expiry as fraction of year — tune this
) -> float:
    """
    C - P = S - K*e^(-rT)
    gap > 0 => call rich
    gap < 0 => put rich
    """
    discounted_strike = strike * math.exp(-r * T)
    return (call_mid - put_mid) - (stock_mid - discounted_strike)

def detect_parity_signal_tradeable(
    stock_bid: Optional[int], stock_ask: Optional[int],
    call_bid: Optional[int], call_ask: Optional[int],
    put_bid: Optional[int], put_ask: Optional[int],
    strike: int, threshold: float, qty: int,
    r: float = 0.0, T: float = 0.0
) -> Optional[ParitySignal]:
    # C - P = S - K * exp(-rT)
    discounted_k = strike * math.exp(-r * T)
    
    # Strategy 1: Sell Call at bid, Buy Put at ask, Buy Stock at ask
    # We lock in call_bid - put_ask - stock_ask and receive K at expiry
    if call_bid is not None and put_ask is not None and stock_ask is not None:
        profit1 = call_bid - put_ask - stock_ask + discounted_k
        if profit1 > threshold:
            return ParitySignal(strike=strike, gap=profit1, action="SELL_CALL_BUY_PUT_BUY_STOCK", qty=qty)

    # Strategy 2: Buy Call at ask, Sell Put at bid, Sell Stock at bid
    # We lock in put_bid + stock_bid - call_ask and pay K at expiry
    if put_bid is not None and stock_bid is not None and call_ask is not None:
        profit2 = put_bid + stock_bid - call_ask - discounted_k
        if profit2 > threshold:
            return ParitySignal(strike=strike, gap=profit2, action="BUY_CALL_SELL_PUT_SELL_STOCK", qty=qty)
        
    return None


def box_price(call_k1: float, call_k2: float, put_k1: float, put_k2: float) -> float:
    """
    Box = (C_k1 - C_k2) + (P_k2 - P_k1)
    Fair value ~= K2 - K1
    """
    return (call_k1 - call_k2) + (put_k2 - put_k1)

def detect_box_signal(k1, k2, threshold, qty,
                      call_k1_bid=None, call_k1_ask=None,
                      call_k2_bid=None, call_k2_ask=None,
                      put_k1_bid=None, put_k1_ask=None,
                      put_k2_bid=None, put_k2_ask=None,
                      r=0.0, T=0.0) -> Optional[BoxSignal]:
    fair = (k2 - k1) * math.exp(-r * T)

    # BUY BOX: pay ask on c1, p2; receive bid on c2, p1
    if all(x is not None for x in [call_k1_ask, call_k2_bid, put_k2_ask, put_k1_bid]):
        buy_cost = call_k1_ask - call_k2_bid + put_k2_ask - put_k1_bid
        if buy_cost < fair - threshold:
            return BoxSignal(k1=k1, k2=k2, price=buy_cost, fair=fair, action="BUY_BOX", qty=qty)

    # SELL BOX: receive bid on c1, p2; pay ask on c2, p1
    if all(x is not None for x in [call_k1_bid, call_k2_ask, put_k2_bid, put_k1_ask]):
        sell_revenue = call_k1_bid - call_k2_ask + put_k2_bid - put_k1_ask
        if sell_revenue > fair + threshold:
            return BoxSignal(k1=k1, k2=k2, price=sell_revenue, fair=fair, action="SELL_BOX", qty=qty)

    return None


def compute_fair_b(quotes: dict, strikes: list) -> Optional[float]:
    """Fair B = median(call_k - put_k + K) across all 3 strikes."""
    estimates = []
    for k in strikes:
        c_bid, c_ask = quotes.get(f"B_C_{k}", (None, None))
        p_bid, p_ask = quotes.get(f"B_P_{k}", (None, None))
        
        if None in (c_bid, c_ask, p_bid, p_ask):
            continue
            
        c_mid = (c_bid + c_ask) / 2
        p_mid = (p_bid + p_ask) / 2
        estimates.append(c_mid - p_mid + k)
        
    if len(estimates) < 1:
        return None
    return statistics.median(estimates)

def find_stale_orders(quotes: dict, fair_b: float, strikes: list, edge: float, qty: int) -> list:
    """
    Strategy 1: For each of the instruments, compute theoretical fair
    using PCP + fair_b. Return (sym, qty, side, price) for anything stale.
    """
    orders = []
    for k in strikes:
        c_sym = f"B_C_{k}"
        p_sym = f"B_P_{k}"
        
        c_bid, c_ask = quotes.get(c_sym, (None, None))
        p_bid, p_ask = quotes.get(p_sym, (None, None))

        # SELL call hedge requires BUY put at ask — use p_ask as cost
        if c_bid is not None and p_ask is not None:
            fair_call_sell = fair_b - k + p_ask
            if c_bid > fair_call_sell + edge:
                orders.append((c_sym, qty, Side.SELL, c_bid))

        # BUY call hedge requires SELL put at bid — use p_bid as revenue
        if c_ask is not None and p_bid is not None:
            fair_call_buy = fair_b - k + p_bid
            if c_ask < fair_call_buy - edge:
                orders.append((c_sym, qty, Side.BUY, c_ask))

        # SELL put hedge requires BUY call at ask — use c_ask as cost
        if p_bid is not None and c_ask is not None:
            fair_put_sell = k - fair_b + c_ask
            if p_bid > fair_put_sell + edge:
                orders.append((p_sym, qty, Side.SELL, p_bid))

        # BUY put hedge requires SELL call at bid — use c_bid as revenue
        if p_ask is not None and c_bid is not None:
            fair_put_buy = k - fair_b + c_bid
            if p_ask < fair_put_buy - edge:
                orders.append((p_sym, qty, Side.BUY, p_ask))

    # Also snipe the base stock B if it's lagging!
    s_bid, s_ask = quotes.get("B", (None, None))
    if s_bid is not None and s_bid > fair_b + edge:
        orders.append(("B", qty, Side.SELL, s_bid))
    if s_ask is not None and s_ask < fair_b - edge:
        orders.append(("B", qty, Side.BUY, s_ask))

    return orders


def detect_butterfly_orders(order_books: dict, qty: int) -> list:
    """
    Strategy 2: Butterfly arb using tradeable prices.
    If C_950_ask + C_1050_ask - 2*C_1000_bid < 0: buy wings, sell middle.
    Same for puts.
    """
    orders = []

    # Calls
    c950_book  = order_books.get("B_C_950")
    c1000_book = order_books.get("B_C_1000")
    c1050_book = order_books.get("B_C_1050")
    if all([c950_book, c1000_book, c1050_book]):
        _, c950_ask  = best_bid_ask(c950_book)
        c1000_bid, _ = best_bid_ask(c1000_book)
        _, c1050_ask = best_bid_ask(c1050_book)
        if all(p is not None for p in [c950_ask, c1000_bid, c1050_ask]):
            if c950_ask + c1050_ask - 2 * c1000_bid < 0:
                orders += [
                    ("B_C_950",  qty,     Side.BUY,  c950_ask),
                    ("B_C_1000", 2 * qty, Side.SELL, c1000_bid),
                    ("B_C_1050", qty,     Side.BUY,  c1050_ask),
                ]

        c950_bid, _  = best_bid_ask(c950_book)
        _, c1000_ask = best_bid_ask(c1000_book)
        c1050_bid, _ = best_bid_ask(c1050_book)
        if all(p is not None for p in [c950_bid, c1000_ask, c1050_bid]):
            if c950_bid + c1050_bid - 2 * c1000_ask > 0:
                orders += [
                    ("B_C_950",  qty,     Side.SELL, c950_bid),
                    ("B_C_1000", 2 * qty, Side.BUY,  c1000_ask),
                    ("B_C_1050", qty,     Side.SELL, c1050_bid),
                ]

    # Puts
    p950_book  = order_books.get("B_P_950")
    p1000_book = order_books.get("B_P_1000")
    p1050_book = order_books.get("B_P_1050")
    if all([p950_book, p1000_book, p1050_book]):
        _, p950_ask  = best_bid_ask(p950_book)
        p1000_bid, _ = best_bid_ask(p1000_book)
        _, p1050_ask = best_bid_ask(p1050_book)
        if all(p is not None for p in [p950_ask, p1000_bid, p1050_ask]):
            if p950_ask + p1050_ask - 2 * p1000_bid < 0:
                orders += [
                    ("B_P_950",  qty,     Side.BUY,  p950_ask),
                    ("B_P_1000", 2 * qty, Side.SELL, p1000_bid),
                    ("B_P_1050", qty,     Side.BUY,  p1050_ask),
                ]

        p950_bid, _  = best_bid_ask(p950_book)
        _, p1000_ask = best_bid_ask(p1000_book)
        p1050_bid, _ = best_bid_ask(p1050_book)
        if all(p is not None for p in [p950_bid, p1000_ask, p1050_bid]):
            if p950_bid + p1050_bid - 2 * p1000_ask > 0:
                orders += [
                    ("B_P_950",  qty,     Side.SELL, p950_bid),
                    ("B_P_1000", 2 * qty, Side.BUY,  p1000_ask),
                    ("B_P_1050", qty,     Side.SELL, p1050_bid),
                ]

    return orders


def detect_vertical_orders(order_books: dict, strikes: list, qty: int) -> list:
    """
    Strategy 3: Vertical spread bounds. Model-free, one subtraction per pair.
    Lower-strike call must be >= higher-strike call. Vice versa for puts.
    """
    orders = []
    pairs = [(strikes[i], strikes[j]) for i in range(len(strikes)) for j in range(i+1, len(strikes))]

    for k_low, k_high in pairs:
        c_low_book  = order_books.get(f"B_C_{k_low}")
        c_high_book = order_books.get(f"B_C_{k_high}")
        if c_low_book and c_high_book:
            _, c_low_ask   = best_bid_ask(c_low_book)
            c_high_bid, _  = best_bid_ask(c_high_book)
            if c_low_ask is not None and c_high_bid is not None and c_low_ask < c_high_bid:
                orders += [
                    (f"B_C_{k_low}",  qty, Side.BUY,  c_low_ask),
                    (f"B_C_{k_high}", qty, Side.SELL, c_high_bid),
                ]

        p_low_book  = order_books.get(f"B_P_{k_low}")
        p_high_book = order_books.get(f"B_P_{k_high}")
        if p_low_book and p_high_book:
            p_low_bid, _   = best_bid_ask(p_low_book)
            _, p_high_ask  = best_bid_ask(p_high_book)
            if p_high_ask is not None and p_low_bid is not None and p_high_ask < p_low_bid:
                orders += [
                    (f"B_P_{k_high}", qty, Side.BUY,  p_high_ask),
                    (f"B_P_{k_low}",  qty, Side.SELL, p_low_bid),
                ]

    return orders