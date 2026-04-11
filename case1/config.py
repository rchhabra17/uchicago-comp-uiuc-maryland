# Stock A

PARAMS = {
    "A": {"spread": 6, "sweep_edge": 1, "order_size": 5, "pe": 1150}
}

# Position limits (based on competition risk limits)
MAX_POSITION = 100  # placeholder, update when they release limits
MAX_ORDER_SIZE = 40

# General
QUOTE_REFRESH_INTERVAL = 5  # seconds between re-quoting if nothing changed


# Stock B / options

B_STRIKES = [950, 1000, 1050]
B_BOX_PAIRS = [(950, 1000), (1000, 1050), (950, 1050)]

B_PARITY_THRESHOLD = 3.0
B_BOX_THRESHOLD = 1.0

B_PARITY_ORDER_SIZE = 5
B_BOX_ORDER_SIZE = 5

B_SIGNAL_COOLDOWN = 1.0  # seconds between repeated same signal
B_MAX_GROSS_FAMILY = 80

B_MAX_POSITION = {
    "B": 40,
    "B_C_950": 20,
    "B_C_1000": 20,
    "B_C_1050": 20,
    "B_P_950": 20,
    "B_P_1000": 20,
    "B_P_1050": 20,
}

B_SWEEP_EDGE = 2.0          # min edge to snipe a stale quote
B_SNIPE_ORDER_SIZE = 5      # qty per stale snipe leg
B_BUTTERFLY_SIZE = 5        # qty per butterfly leg
B_VERTICAL_SIZE = 5         # qty per vertical arb leg

B_DELTA_THRESHOLD = 5.0     # hedge only when significantly imbalanced
B_DELTA_HEDGE_SIZE = 3      # keep hedge small to avoid crossing spread for nothing

# B options market-making
B_MM_HALF_SPREAD = 4        # ticks inside fair value on each side (8 total spread)
B_MM_ORDER_SIZE = 3         # smaller size = less adverse selection damage
B_MM_SKEW_PER_UNIT = 0.8   # aggressive skew to flatten inventory via MM, not delta hedging
B_MM_MAX_SKEW = 8           # max skew in either direction (ticks)
B_MM_REFRESH_INTERVAL = 2.0 # seconds between full requote cycle
B_MM_MIN_PRICE = 2          # don't quote below this (avoid quoting options near zero)

TOTAL_ROUND_SECONDS = 900   # 10 days × 90s per day
B_NEAR_EXPIRY_FRACTION = 0.05   # last 5% of round (~45s) activates near-expiry mode
B_NEAR_EXPIRY_BUFFER = 5.0      # sell option only if bid > intrinsic + this buffer
B_NEAR_EXPIRY_SIZE = 5          # qty per near-expiry sell