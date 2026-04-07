from dataclasses import dataclass
from typing import Optional
import math

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






