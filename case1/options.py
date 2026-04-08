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

def detect_parity_signal(
    call_mid, put_mid, stock_mid,
    strike, threshold, qty,
    call_spread=None, put_spread=None, stock_spread=None,
    r=0.05, T=1/52,
) -> Optional[ParitySignal]:
    if call_mid is None or put_mid is None or stock_mid is None:
        return None

    gap = parity_gap(call_mid, put_mid, stock_mid, strike, r, T)

    # Total cost to cross 3 spreads (half-spread per leg)
    total_cost = 0
    if call_spread:  total_cost += call_spread / 2
    if put_spread:   total_cost += put_spread / 2
    if stock_spread: total_cost += stock_spread / 2

    effective_threshold = max(threshold, total_cost)

    if gap > effective_threshold:
        return ParitySignal(strike=strike, gap=gap, action="SELL_CALL_BUY_PUT_BUY_STOCK", qty=qty)
    elif gap < -effective_threshold:
        return ParitySignal(strike=strike, gap=gap, action="BUY_CALL_SELL_PUT_SELL_STOCK", qty=qty)
    return None


def box_price(call_k1: float, call_k2: float, put_k1: float, put_k2: float) -> float:
    """
    Box = (C_k1 - C_k2) + (P_k2 - P_k1)
    Fair value ~= K2 - K1
    """
    return (call_k1 - call_k2) + (put_k2 - put_k1)


def detect_box_signal(call_k1, call_k2, put_k1, put_k2, k1, k2, threshold, qty, r: float = 0.05, T: float = 1/52,) -> Optional[BoxSignal]:
    if None in (call_k1, call_k2, put_k1, put_k2):
        return None
    price = box_price(call_k1, call_k2, put_k1, put_k2)
    fair = (k2 - k1) * math.exp(-r * T)   # ← discounted
    if price < fair - threshold:
        return BoxSignal(k1=k1, k2=k2, price=price, fair=fair, action="BUY_BOX", qty=qty)
    if price > fair + threshold:
        return BoxSignal(k1=k1, k2=k2, price=price, fair=fair, action="SELL_BOX", qty=qty)
    return None


def compute_fair_b(order_books: dict, strikes: list) -> Optional[float]:
    """Fair B = median(call_k - put_k + K) across all 3 strikes."""
    estimates = []
    for k in strikes:
        c_book = order_books.get(f"B_C_{k}")
        p_book = order_books.get(f"B_P_{k}")
        if not c_book or not p_book:
            continue
        c_mid = midpoint(c_book)
        p_mid = midpoint(p_book)
        if c_mid is None or p_mid is None:
            continue
        estimates.append(c_mid - p_mid + k)
    if len(estimates) < 2:
        return None
    return statistics.median(estimates)


def find_stale_orders(order_books: dict, fair_b: float, strikes: list, edge: float, qty: int) -> list:
    """
    Strategy 1: For each of the 6 instruments, compute theoretical fair
    using PCP + fair_b. Return (sym, qty, side, price) for anything stale.
    """
    orders = []
    for k in strikes:
        c_sym = f"B_C_{k}"
        p_sym = f"B_P_{k}"
        c_book = order_books.get(c_sym)
        p_book = order_books.get(p_sym)
        if not c_book or not p_book:
            continue

        c_bid, c_ask = best_bid_ask(c_book)
        p_bid, p_ask = best_bid_ask(p_book)


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

    return orders


def detect_vertical_orders(order_books: dict, strikes: list, qty: int) -> list:
    """
    Strategy 3: Vertical spread bounds. Model-free, one subtraction per pair.
    Lower-strike call must be >= higher-strike call. Vice versa for puts.
    """
    orders = []
    pairs = [(strikes[i], strikes[j]) for i in range(len(strikes)) for j in range(i+1, len(strikes))]

    for k_low, k_high in pairs:
        # Calls: C_low >= C_high → if C_low_ask < C_high_bid, buy low sell high
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

        # Puts: P_high >= P_low → if P_high_ask < P_low_bid, buy high sell low
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



