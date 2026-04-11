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
    r: float = 0.0,       # risk-free rate — set to 0 (no discounting; matches detect_parity_signal_tradeable / detect_box_signal)
    T: float = 0.0,       # time to expiry — set to 0 (no discounting)
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


def _one_sided_mid(bid: Optional[int], ask: Optional[int]) -> Optional[float]:
    """Best available mid: two-sided preferred, one-sided fallback."""
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if bid is not None:
        return float(bid)
    if ask is not None:
        return float(ask)
    return None


def compute_fair_b(quotes: dict, strikes: list) -> tuple[Optional[float], bool]:
    """
    Fair B = median(call_k - put_k + K) across strikes.
    Returns (fair_b, is_fresh) where is_fresh=False means we fell back to one-sided quotes.
    Accepts partial books: uses best available mid per leg.
    """
    estimates = []
    all_two_sided = True
    for k in strikes:
        c_bid, c_ask = quotes.get(f"B_C_{k}", (None, None))
        p_bid, p_ask = quotes.get(f"B_P_{k}", (None, None))

        c_mid = _one_sided_mid(c_bid, c_ask)
        p_mid = _one_sided_mid(p_bid, p_ask)

        if c_mid is None or p_mid is None:
            continue

        if None in (c_bid, c_ask, p_bid, p_ask):
            all_two_sided = False

        estimates.append(c_mid - p_mid + k)

    if not estimates:
        return None, False
    return statistics.median(estimates), all_two_sided

def find_stale_orders(quotes: dict, fair_b: float, strikes: list, edge: float, qty: int,
                      T: Optional[float] = None, sigma: Optional[float] = None) -> list[tuple]:
    """
    Single-leg stale sniping.
    If T and sigma are provided (BS mode): compare market price to BS fair value.
    Otherwise (PCP fallback): use the other leg's ask as a conservative anchor.
    Delta exposure from fills is managed separately by the delta layer in bot.py.
    """
    orders = []
    use_bs = (T is not None and sigma is not None and T > 1e-6 and sigma > 1e-6)

    for k in strikes:
        c_sym = f"B_C_{k}"
        p_sym = f"B_P_{k}"

        c_bid, c_ask = quotes.get(c_sym, (None, None))
        p_bid, p_ask = quotes.get(p_sym, (None, None))

        if use_bs:
            fair_call = bs_call_price(fair_b, k, T, sigma)
            fair_put  = bs_put_price(fair_b, k, T, sigma)

            if c_bid is not None and c_bid > fair_call + edge:
                orders.append((c_sym, qty, Side.SELL, c_bid))
            if c_ask is not None and c_ask < fair_call - edge:
                orders.append((c_sym, qty, Side.BUY, c_ask))
            if p_bid is not None and p_bid > fair_put + edge:
                orders.append((p_sym, qty, Side.SELL, p_bid))
            if p_ask is not None and p_ask < fair_put - edge:
                orders.append((p_sym, qty, Side.BUY, p_ask))
        else:
            # PCP fallback: conservative anchor uses other leg's ask/bid
            if c_bid is not None and p_ask is not None:
                if c_bid > fair_b - k + p_ask + edge:
                    orders.append((c_sym, qty, Side.SELL, c_bid))
            if c_ask is not None and p_bid is not None:
                if c_ask < fair_b - k + p_bid - edge:
                    orders.append((c_sym, qty, Side.BUY, c_ask))
            if p_bid is not None and c_ask is not None:
                if p_bid > k - fair_b + c_ask + edge:
                    orders.append((p_sym, qty, Side.SELL, p_bid))
            if p_ask is not None and c_bid is not None:
                if p_ask < k - fair_b + c_bid - edge:
                    orders.append((p_sym, qty, Side.BUY, p_ask))

    return orders


# ── Black-Scholes engine ──────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erfc — no scipy needed."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def bs_call_price(S: float, K: float, T: float, sigma: float) -> float:
    """BS call price with r=0. Falls back to intrinsic at expiry or zero vol."""
    if T <= 1e-9 or sigma <= 1e-9:
        return max(S - K, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


def bs_put_price(S: float, K: float, T: float, sigma: float) -> float:
    """BS put price with r=0. Falls back to intrinsic at expiry or zero vol."""
    if T <= 1e-9 or sigma <= 1e-9:
        return max(K - S, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return K * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call_delta(S: float, K: float, T: float, sigma: float) -> float:
    """BS call delta = N(d1). Returns 1 or 0 at expiry."""
    if T <= 1e-9 or sigma <= 1e-9:
        return 1.0 if S > K else (0.5 if S == K else 0.0)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


def bs_put_delta(S: float, K: float, T: float, sigma: float) -> float:
    """BS put delta = N(d1) - 1."""
    return bs_call_delta(S, K, T, sigma) - 1.0


def _bs_vega(S: float, K: float, T: float, sigma: float) -> float:
    """BS vega: dPrice/dSigma. Same for calls and puts."""
    if T <= 1e-9 or sigma <= 1e-9:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    return S * sqrt_T * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)


def implied_vol(option_price: float, S: float, K: float, T: float, is_call: bool,
                tol: float = 0.5, max_iter: int = 8) -> Optional[float]:
    """Newton-Raphson IV solver — converges in ~5 iterations for integer-precision prices."""
    if T <= 1e-9 or S <= 0 or K <= 0:
        return None

    intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
    if option_price <= intrinsic + 1e-6:
        return None

    pricer = bs_call_price if is_call else bs_put_price
    sigma = 0.3  # initial guess

    for _ in range(max_iter):
        price = pricer(S, K, T, sigma)
        vega = _bs_vega(S, K, T, sigma)
        if vega < 1e-10:
            break
        sigma -= (price - option_price) / vega
        if sigma <= 0:
            sigma = 0.01
        if abs(price - option_price) < tol:
            break

    if sigma <= 0 or sigma > 20:
        return None
    return sigma


def compute_implied_vols(quotes: dict, fair_b: float, T: float,
                         strikes: list) -> tuple[dict, Optional[float]]:
    """
    Back-solve implied vol from each option's mid. Returns (per_symbol_dict, median_iv).
    Uses mids so the estimate is centred and not skewed by one-sided stale quotes.
    """
    ivs: dict[str, float] = {}
    for k in strikes:
        c_sym = f"B_C_{k}"
        p_sym = f"B_P_{k}"
        c_bid, c_ask = quotes.get(c_sym, (None, None))
        p_bid, p_ask = quotes.get(p_sym, (None, None))

        if c_bid is not None and c_ask is not None and c_bid < c_ask:
            iv = implied_vol((c_bid + c_ask) / 2.0, fair_b, k, T, is_call=True)
            if iv is not None:
                ivs[c_sym] = iv

        if p_bid is not None and p_ask is not None and p_bid < p_ask:
            iv = implied_vol((p_bid + p_ask) / 2.0, fair_b, k, T, is_call=False)
            if iv is not None:
                ivs[p_sym] = iv

    if not ivs:
        return ivs, None
    return ivs, statistics.median(ivs.values())


def option_delta(symbol: str, fair_b: float) -> float:
    """
    Fallback delta: linear moneyness approximation normalised to 50-pt strike spacing.
    Used when T or sigma are unavailable. Call delta in [0.05, 0.95].
    """
    if "B_C_" in symbol:
        k = int(symbol.split("_")[-1])
        return max(0.05, min(0.95, 0.5 + 0.35 * (fair_b - k) / 50.0))
    elif "B_P_" in symbol:
        k = int(symbol.split("_")[-1])
        call_d = max(0.05, min(0.95, 0.5 + 0.35 * (fair_b - k) / 50.0))
        return call_d - 1.0
    return 0.0


def option_delta_bs(symbol: str, fair_b: float, T: float, sigma: float) -> float:
    """Proper BS delta. Falls back to linear approximation when T or sigma invalid."""
    if T <= 1e-9 or sigma <= 1e-9:
        return option_delta(symbol, fair_b)
    if "B_C_" in symbol:
        k = int(symbol.split("_")[-1])
        return bs_call_delta(fair_b, k, T, sigma)
    elif "B_P_" in symbol:
        k = int(symbol.split("_")[-1])
        return bs_put_delta(fair_b, k, T, sigma)
    return 0.0


def find_near_expiry_orders(quotes: dict, fair_b: float, T: float,
                             strikes: list, qty: int, buffer: float) -> list[tuple]:
    """
    Near-expiry theta harvesting. Only active when T < threshold (caller's responsibility).
    Sells OTM options (intrinsic = 0) that still carry time premium above buffer.
    ITM options are skipped — delta risk too high without per-tick hedging.
    Delta from any fills is handled by the delta manager in bot.py.
    """
    orders = []
    for k in strikes:
        c_sym = f"B_C_{k}"
        p_sym = f"B_P_{k}"
        c_bid, _ = quotes.get(c_sym, (None, None))
        p_bid, _ = quotes.get(p_sym, (None, None))

        c_intrinsic = max(fair_b - k, 0.0)
        p_intrinsic = max(k - fair_b, 0.0)

        # Only sell strictly OTM options (intrinsic < 1 avoids float comparison issues)
        if c_bid is not None and c_intrinsic < 1.0 and c_bid > buffer:
            orders.append((c_sym, qty, Side.SELL, c_bid))

        if p_bid is not None and p_intrinsic < 1.0 and p_bid > buffer:
            orders.append((p_sym, qty, Side.SELL, p_bid))

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