# fair_value.py — Strategy 4: Cross-asset C fair value from prediction market
"""
Given a FedProbabilityModel (from prediction_market.py), compute the fair value
of Stock C using the case-packet formulas with known constants.

Known parameters (from case packet):
  y₀     = 0.045   baseline yield (4.5%)
  PE₀    = 14.0    baseline P/E
  EPS₀   = 2.00    baseline EPS (overwritten by earnings news)
  B₀/N   = 40.0    bond portfolio per share
  D      = 7.5     duration
  Conv   = 55.0    convexity
  λ      = 0.65    bond-component weighting

Unknown (calibrate during practice):
  γ      — PE sensitivity to yield: PE_t = PE₀·exp(−γ·Δy)
  β_y    — yield sensitivity to rate expectations: Δy = β_y · E[Δr]

The model output is a *relative* fair value: anchored to the first observed
market price, then tracking changes driven by the prediction market.
"""

import math
import logging
from typing import Optional

log = logging.getLogger("FV")

# ── Known constants (case packet) ─────────────────────────────────────────────
Y0         = 0.045    # baseline yield
PE0        = 14.0     # baseline P/E
EPS0       = 2.00     # baseline EPS
B0_N       = 40.0     # bond portfolio per share  (B₀/N)
D          = 7.5      # duration
CONV       = 55.0     # convexity
LAMBDA     = 0.65     # bond-component weighting

# ── Unknown parameters — calibrated from market ───────────────────────────────
# β_y: E[Δr] is in bps; y is in decimal.  A full 25-bp expected hike should
#   move yields by something like 3–8 bps → β_y ≈ 0.12–0.32 bp/bp → 0.000012–0.000032
#   in decimal/bp units.  Start conservatively; tune during practice.
BETA_Y_DEFAULT = 0.0002    # yield moves 0.02% per bp of expected rate change
GAMMA_DEFAULT  = 2.0       # PE decays ~2% per 1% of yield change

# ── Cross-asset trade threshold ───────────────────────────────────────────────
# Only trade C if its market price deviates from fair value by this many points.
CROSS_ASSET_THRESHOLD = 10   # points — widen if γ / β_y calibration is rough


class FairValueEngine:
    """
    Computes C's fair value from the prediction market probabilities.

    Usage:
      1. Call calibrate(market_mid, eps) once (after first earnings + PM data).
      2. Call compute(e_delta_r, eps) on every PM or earnings update.
      3. Compare result against C's market mid to find cross-asset mispricings.

    Calibration anchors our *scale factor* so the model tracks dollar-price
    movements even though γ and β_y aren't perfectly known.
    """

    def __init__(self, beta_y: float = BETA_Y_DEFAULT, gamma: float = GAMMA_DEFAULT):
        self.beta_y   = beta_y
        self.gamma    = gamma

        # EPS state
        self.eps_c: Optional[float] = None

        # Calibration state
        self.calibrated          = False
        self.c_baseline_price    = None   # market price at calibration
        self.c_baseline_e_dr     = None   # E[Δr] at calibration
        self.c_baseline_raw      = None   # raw model output at calibration
        self.c_price_scale       = 1.0    # market_price / raw_model at calibration

        # Latest fair value
        self.fair_c: Optional[float] = None

    # ══════════════════════════════════════════════════════════════════════════
    #  CORE MODEL
    # ══════════════════════════════════════════════════════════════════════════

    def _raw(self, eps: float, e_delta_r: float) -> float:
        """
        Unscaled fair value for C.

            Δy      = β_y · E[Δr]
            PE_t    = PE₀ · exp(−γ · Δy)
            ΔB/N    = (B₀/N) · (−D·Δy + ½·Conv·Δy²)
            P_raw   = EPS · PE_t + λ · ΔB/N

        E[Δr] is in basis points; β_y converts to decimal yield change.
        """
        delta_y   = self.beta_y * e_delta_r          # decimal yield change
        pe_t      = PE0 * math.exp(-self.gamma * delta_y)
        delta_b_n = B0_N * (-D * delta_y + 0.5 * CONV * delta_y ** 2)
        return eps * pe_t + LAMBDA * delta_b_n

    # ══════════════════════════════════════════════════════════════════════════
    #  CALIBRATION
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate(self, market_mid: float, eps: float, e_delta_r: float):
        """
        Anchor the scale factor to the current market price.

        Call this:
          - At the start of each round once both an earnings print AND a
            two-sided PM book exist.
          - After each C earnings release (re-anchor to latest market mid).
        """
        self.eps_c              = eps
        self.c_baseline_e_dr   = e_delta_r
        self.c_baseline_raw    = self._raw(eps, e_delta_r)
        if self.c_baseline_raw > 0:
            self.c_price_scale = market_mid / self.c_baseline_raw
        else:
            self.c_price_scale = 1.0
        self.c_baseline_price  = market_mid
        self.calibrated        = True
        self.fair_c            = market_mid

        log.info(
            f"[C CALIBRATE] market={market_mid:.1f} | raw={self.c_baseline_raw:.3f} "
            f"| scale={self.c_price_scale:.1f} | E[Δr]={e_delta_r:.2f}bps | eps={eps:.4f}"
        )

    def recalibrate(self, market_mid: float, e_delta_r: float):
        """Re-anchor to new market mid (call on each C earnings release)."""
        if self.eps_c is not None:
            self.calibrate(market_mid, self.eps_c, e_delta_r)

    # ══════════════════════════════════════════════════════════════════════════
    #  COMPUTE
    # ══════════════════════════════════════════════════════════════════════════

    def compute(self, e_delta_r: float, eps: Optional[float] = None) -> Optional[float]:
        """
        Compute C fair value from current E[Δr].
        Returns fair_c in market price units, or None if not calibrated.
        """
        if not self.calibrated:
            return None
        if eps is not None:
            self.eps_c = eps
        if self.eps_c is None:
            return None

        raw       = self._raw(self.eps_c, e_delta_r)
        self.fair_c = raw * self.c_price_scale

        delta_raw = raw - self.c_baseline_raw
        delta_px  = self.fair_c - self.c_baseline_price

        log.debug(
            f"[C FV] fair={self.fair_c:.1f} | E[Δr]={e_delta_r:.2f}bps "
            f"| Δraw={delta_raw:.4f} | Δpx={delta_px:.1f} | eps={self.eps_c:.4f}"
        )
        return self.fair_c

    # ══════════════════════════════════════════════════════════════════════════
    #  CROSS-ASSET SIGNAL
    # ══════════════════════════════════════════════════════════════════════════

    def cross_asset_signal(self, c_market_mid: float) -> float:
        """
        Returns the mispricing: positive means C is expensive vs PM model,
        negative means C is cheap vs PM model.

        cross_asset_signal > CROSS_ASSET_THRESHOLD  →  sell C
        cross_asset_signal < -CROSS_ASSET_THRESHOLD →  buy  C
        """
        if self.fair_c is None:
            return 0.0
        return c_market_mid - self.fair_c

    def update_gamma(self, gamma: float):
        """Hot-update γ between rounds if calibration suggests a new value."""
        self.gamma = gamma
        log.info(f"[FV] γ updated to {gamma}")

    def update_beta_y(self, beta_y: float):
        """Hot-update β_y between rounds."""
        self.beta_y = beta_y
        log.info(f"[FV] β_y updated to {beta_y}")
