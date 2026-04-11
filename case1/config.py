# config.py — All tunable parameters for the trading bot
# Update these between rounds based on observed market behavior.

# ── Exchange risk limits (update from Ed post on competition day) ─────────────
MAX_POSITION     = 100   # global exchange-enforced max |position|
MAX_ORDER_SIZE   = 40    # max single order size
MAX_OPEN_ORDERS  = 200   # max outstanding unfilled volume (check Ed post)

# ── ETF arb ───────────────────────────────────────────────────────────────────
SWAP_FEE         = 5     # per creation/redemption swap
ETF_MIN_PROFIT   = 1     # min profit after swap fee to execute ETF arb
ETF_MAX_POS      = 40    # max ETF position

# ── Cross-asset C trade (Strategy 4) ─────────────────────────────────────────
C_CROSS_MAX_POS  = 20    # max C position from cross-asset strategy
C_CROSS_QTY      = 3     # contracts per cross-asset trade
# Increase if model is noisy (γ/β_y poorly calibrated); tighten as you calibrate
CROSS_ASSET_THRESHOLD = 10   # min |C_market - C_fair| to trade (price units)

# ── Prediction market market-making (Strategy 3) ──────────────────────────────
PM_MAX_POS       = 50    # max |position| per PM contract
PM_MM_SIZE       = 3     # contracts per passive quote
PM_ARB_SIZE      = 5     # contracts per sum-arb leg
PM_DIR_SIZE      = 10    # contracts per CPI/news directional sweep
MM_SPREAD_BASE   = 12    # tight half-spread (calm market)
MM_SPREAD_WIDE   = 35    # wide half-spread (post-news spike)
MM_WIDE_DECAY    = 50    # ticks to decay from wide back to base
SKEW_PER_UNIT    = 0.6   # bid/ask shift per unit of inventory
ARB_MIN_PROFIT   = 6     # min locked profit to execute sum arb

# ── CPI signal (Strategy 2) ───────────────────────────────────────────────────
CPI_SHIFT_SCALE  = 50_000   # probability shift per unit CPI surprise (0.001 → 50 pts)
CPI_SHIFT_CAP    = 180      # max shift per single CPI print
CPI_MIN_SURPRISE = 0.0001   # ignore surprises below this magnitude

# ── Fair value model for C (Strategy 4) ──────────────────────────────────────
# Known constants (from case packet — do not change):
Y0    = 0.045   # baseline yield
PE0   = 14.0    # baseline P/E
EPS0  = 2.00    # baseline EPS
B0_N  = 40.0    # bond portfolio per share
D     = 7.5     # duration
CONV  = 55.0    # convexity
LAMB  = 0.65    # bond-component weighting λ

# Calibration targets — update THESE between rounds:
# β_y: decimal yield change per basis-point of expected rate change
# γ  : PE sensitivity to yield changes (PE_t = PE0 · exp(−γ · Δy))
#
# How to calibrate between rounds:
#   1. Note E[Δr] from PM book mid at two different times (e.g., 5bps and 12bps).
#   2. Note the corresponding C market prices (e.g., 1015 and 1008).
#   3. Δprice / Δ(E[Δr]) = ∂P/∂E[Δr]
#      From model: ∂P/∂E[Δr] ≈ −scale × (EPS·PE0·γ·β_y + λ·B0_N·D·β_y)
#   4. Solve for β_y·γ from the observed sensitivity.
BETA_Y = 0.0002   # start conservative; tune from practice data
GAMMA  = 2.0      # start conservative; tune from practice data

# ── Options arb ───────────────────────────────────────────────────────────────
RISK_FREE_RATE = 0.045   # y0 (matches case packet)
OPTIONS_MAX_POS = 20
OPTIONS_QTY     = 3

# ── Timing ────────────────────────────────────────────────────────────────────
TRADE_INTERVAL  = 1.5   # main loop cadence (seconds)
PM_QUOTE_EVERY  = 3     # refresh PM quotes every N loops (~4.5s)
ETF_CHECK_EVERY = 1     # check ETF arb every loop
MAX_TICKS       = 4500  # ticks per round (10 days × 90s × 5 ticks)
