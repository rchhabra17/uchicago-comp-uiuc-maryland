# Stock A

PARAMS = {
    "A": {"spread": 6, "sweep_edge": 1, "order_size": 5, "pe": 1150, "skew_mult": 0.15},
    "C": {"spread": 1, "sweep_edge": 1, "order_size": 5, "skew_mult": 0.01}
}

# Position limits (based on competition risk limits)
MAX_POSITION = 100  # placeholder, update when they release limits
MAX_ORDER_SIZE = 40

# General
QUOTE_REFRESH_INTERVAL = 5  # seconds between re-quoting if nothing changed