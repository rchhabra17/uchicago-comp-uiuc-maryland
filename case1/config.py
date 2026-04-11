# Stock A Configuration
# Updated per case1_stock_a_update_v2.md empirical findings

PARAMS = {
    "A": {
        "spread": 6,
        "sweep_edge": 1,
        "order_size": 5,
        "skew_mult": 0.15
        # NOTE: "pe" removed — using linear regression model instead
    },
    # C params commented out for A-only testing
    # "C": {"spread": 1, "sweep_edge": 1, "order_size": 5, "skew_mult": 0.01}
}

# Position limits (from competition risk limits in CLAUDE.md)
MAX_POSITION = 100
MAX_ORDER_SIZE = 40

# Minimum position size to bother flattening on earnings (speed optimization)
# Small positions aren't worth the 1-2 tick delay from market order fill
MIN_POSITION_TO_FLATTEN = 15

# General
QUOTE_REFRESH_INTERVAL = 5  # seconds between re-quoting if nothing changed

# ============================================================================
# CALIBRATION PARAMETERS (Changes 1, 2)
# Based on Finding 1: Linear model requires 3+ distinct EPS samples
# Based on Finding 2: Market converges by +10s, sample at +12s
# ============================================================================

# Minimum distinct EPS values needed before we start trading
# From Finding 1: "3-4 earnings events are enough for a usable fit"
MIN_DISTINCT_EPS_FOR_TRADING = 3

# Sampling window for settled mid price after earnings
# From Finding 2: "at +10s: 5 pts deviation, at +12s: 3 pts"
SETTLED_WINDOW_START_S = 10  # start of averaging window
SETTLED_WINDOW_END_S = 15    # end of averaging window

# Contamination check: skip sample if another A-news event within this window
CONTAMINATION_WINDOW_S = 15  # seconds after earnings to check for contamination

# ============================================================================
# SNIPING MODE PARAMETERS (Change 3)
# Based on Finding 2: Post-earnings convergence takes ~10s
# ============================================================================

# Duration of sniping mode after earnings
# From Finding 2: "market converged by +10s", so snipe for 8s
SNIPING_DURATION_S = 8.0

# Edge thresholds that decay over time during sniping window
# Early sweeps catch the most stale quotes, later sweeps are riskier
SNIPING_EDGE_EARLY = 0.5   # [0-2s]: maximum aggression, take almost any edge
SNIPING_EDGE_MID = 2       # [2-5s]: medium aggression
SNIPING_EDGE_LATE = 4      # [5-8s]: conservative, approaching normal edge

# Maximum quantity per individual sweep action (not total position)
# Allows multiple sweeps within sniping mode
SNIPING_MAX_PER_SCAN = 20

# ============================================================================
# NEWS SENTIMENT TRADING PARAMETERS (Change 4)
# Based on Finding 5: A-news events produce 100-200pt moves with 5-8s delay
# ============================================================================

# Minimum classifier confidence to trigger a trade
# From update doc: 0.7 threshold ensures high-quality signals
NEWS_CONFIDENCE_THRESHOLD = 0.6

# Expected price move on A-symbol news (in points)
# Empirical: avg=55pts but high variance (range 5-123). Target big moves.
# Reverted closer to original - big wins come from 100-120pt moves
NEWS_EXPECTED_MOVE = 120

# Trade size as fraction of MAX_POSITION
# From update doc: "start with half of normal max position until validated"
NEWS_TRADE_SIZE_FRACTION = 0.5

# Duration of post-news mode (pause after news trade)
# From Finding 5: "~10s for market to converge"
POST_NEWS_MODE_S = 10

# Exit threshold: close position once mid moves this far in our direction
# Compromise between original (80) and empirical avg (55)
NEWS_EXIT_MOVE_THRESHOLD = 70
