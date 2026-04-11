"""
Fair Value Engine for Stock A and C

Stock A: Linear regression model (settled_mid = a + b × EPS)
Stock C: Cross-asset model (PM probabilities → E[Δr] → yield → C fair value)

Stock A based on empirical findings from case1_stock_a_update_v2.md.
Stock C based on case packet formulas with calibration (from c-pred branch).
"""

import math
import numpy as np
from typing import Optional, List, Tuple


# ============================================================================
# STOCK C — KNOWN CONSTANTS (from case packet)
# ============================================================================

C_Y0        = 0.045   # baseline yield
C_PE0       = 14.0    # baseline P/E
C_EPS0      = 2.00    # baseline EPS
C_B0_OVER_N = 40.0    # bond portfolio value per share (B0 / N)
C_D         = 7.5     # duration
C_CCONV     = 55.0    # convexity
C_LAMBDA    = 0.65    # bond component weighting

# C parameters — calibrate from practice data
C_BETA_Y_DEFAULT = 0.0002  # yield sensitivity to E[Δr] (decimal per bps)
C_GAMMA_DEFAULT  = 2.0     # PE sensitivity to yield changes


class FairValueEngine:
    """
    Fair value computation for Stock A using linear regression.

    Change 1 (update v2): Replace PE calibration with linear fit.
    Model: settled_mid = a + b × EPS

    Requires minimum 3 distinct EPS samples before trading.
    Refits on every new sample for continuous refinement.
    """

    def __init__(self):
        # A: Linear regression model
        self.samples_a: List[Tuple[float, float]] = []  # (eps, settled_mid) pairs
        self.fit_a: Optional[float] = None  # intercept
        self.fit_b: Optional[float] = None  # slope

    def reset_model_a(self) -> None:
        """
        Reset the A model calibration (for new round).
        Clears all samples and fitted parameters.
        """
        self.samples_a = []
        self.fit_a = None
        self.fit_b = None
        print("[CALIBRATE] ✓ Model A reset for new round")

    # ========================================================================
    # STOCK A — LINEAR REGRESSION MODEL
    # ========================================================================

    def add_sample_a(self, eps: float, settled_mid: float) -> None:
        """
        Add a calibration sample and refit the linear model.

        Args:
            eps: Earnings per share value
            settled_mid: Market mid price at +12s after earnings (settled value)

        Side effects:
            - Appends (eps, settled_mid) to samples
            - Refits linear regression if >= 2 samples
            - Logs fit quality
        """
        self.samples_a.append((eps, settled_mid))
        n = len(self.samples_a)

        print(f"[CALIBRATE] Added sample #{n}: EPS={eps:.4f}, settled_mid={settled_mid:.2f}")

        # Refit if we have enough samples
        if n >= 2:
            self._fit_linear_a()
        else:
            print(f"[CALIBRATE] Need {2 - n} more sample(s) to fit")

    def _fit_linear_a(self) -> None:
        """
        Fit linear regression: settled_mid = a + b × EPS

        Uses numpy.polyfit (degree 1) for simplicity.
        Logs fit quality (R², residuals) for diagnostics.
        """
        if len(self.samples_a) < 2:
            return

        eps_values = np.array([s[0] for s in self.samples_a])
        settled_values = np.array([s[1] for s in self.samples_a])

        # Fit: coeffs[0] = slope (b), coeffs[1] = intercept (a)
        coeffs = np.polyfit(eps_values, settled_values, deg=1)
        self.fit_b = coeffs[0]  # slope
        self.fit_a = coeffs[1]  # intercept

        # Compute R² for diagnostics
        predictions = self.fit_a + self.fit_b * eps_values
        residuals = settled_values - predictions
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((settled_values - np.mean(settled_values)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        print(f"[CALIBRATE] Linear fit: settled = {self.fit_a:.2f} + {self.fit_b:.2f} × EPS")
        print(f"[CALIBRATE] R² = {r_squared:.4f}, n_samples = {len(self.samples_a)}, residual_std = {np.std(residuals):.2f}")

        # Log calibrated status
        if self.is_calibrated_a():
            print(f"[CALIBRATE] ✓ Model is READY for trading (n_distinct_eps = {self.n_distinct_eps_a()})")
        else:
            print(f"[CALIBRATE] ⚠ Need {3 - self.n_distinct_eps_a()} more distinct EPS values to start trading")

    def fair_value_a(self, eps: float) -> Optional[float]:
        """
        Compute fair value for A given EPS using linear model.

        Args:
            eps: Earnings per share

        Returns:
            Fair value (a + b × eps) if model is fitted, else None
        """
        if self.fit_a is None or self.fit_b is None:
            return None

        return self.fit_a + self.fit_b * eps

    def implied_eps_a(self, price: float) -> Optional[float]:
        """
        Back out implied EPS from a given price.

        Args:
            price: Market price

        Returns:
            Implied EPS = (price - a) / b, or None if not fitted

        Use case: Read what EPS the market is pricing in from current mid.
        """
        if self.fit_a is None or self.fit_b is None or self.fit_b == 0:
            return None

        return (price - self.fit_a) / self.fit_b

    def is_calibrated_a(self) -> bool:
        """
        Check if the model is calibrated and ready for trading.

        Returns:
            True if fit exists AND we have ≥3 distinct EPS values

        Per update doc Change 1: "Minimum samples to trade: require at least
        3 distinct EPS samples before the fit is considered usable."
        """
        return (
            self.fit_a is not None and
            self.fit_b is not None and
            self.n_distinct_eps_a() >= 3
        )

    def n_distinct_eps_a(self) -> int:
        """
        Count number of distinct EPS values in calibration samples.

        Returns:
            Number of unique EPS values (to check calibration readiness)
        """
        if not self.samples_a:
            return 0

        unique_eps = set(s[0] for s in self.samples_a)
        return len(unique_eps)


# ============================================================================
# STOCK C — CROSS-ASSET FAIR VALUE ENGINE (from c-pred)
# ============================================================================

class CFairValueEngine:
    """
    Fair value computation for Stock C using cross-asset PM model.

    Uses prediction market probabilities → E[Δr] → yield → C fair value
    via the case-packet formulas. Calibrates with a scale factor to anchor
    model output to observed market prices.

    Usage:
        1. Call calibrate(market_mid, eps, e_delta_r) after first C earnings
        2. Call compute(e_delta_r) whenever PM probabilities change
        3. Use cross_asset_signal(c_market_mid) to find mispricings
    """

    def __init__(self, beta_y: float = C_BETA_Y_DEFAULT, gamma: float = C_GAMMA_DEFAULT):
        self.beta_y = beta_y
        self.gamma = gamma
        self.eps_c: Optional[float] = None
        self.calibrated = False
        self.c_baseline_price: Optional[float] = None
        self.c_baseline_e_dr: Optional[float] = None
        self.c_baseline_raw: Optional[float] = None
        self.c_price_scale: float = 1.0
        self.fair_c: Optional[float] = None

    def _raw(self, eps: float, e_delta_r: float) -> float:
        """
        Unscaled fair value for C from case-packet formulas.

        Δy    = β_y · E[Δr]
        PE_t  = PE₀ · exp(−γ · Δy)
        ΔB/N  = (B₀/N) · (−D·Δy + ½·Conv·Δy²)
        P_raw = EPS · PE_t + λ · ΔB/N
        """
        delta_y = self.beta_y * e_delta_r
        pe_t = C_PE0 * math.exp(-self.gamma * delta_y)
        delta_b_n = C_B0_OVER_N * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
        return eps * pe_t + C_LAMBDA * delta_b_n

    def calibrate(self, market_mid: float, eps: float, e_delta_r: float):
        """
        Anchor the scale factor to the current market price.

        Call after each C earnings release with the current market mid
        and E[Δr] from the prediction market.
        """
        self.eps_c = eps
        self.c_baseline_e_dr = e_delta_r
        self.c_baseline_raw = self._raw(eps, e_delta_r)
        if self.c_baseline_raw > 0:
            self.c_price_scale = market_mid / self.c_baseline_raw
        else:
            self.c_price_scale = 1.0
        self.c_baseline_price = market_mid
        self.calibrated = True
        self.fair_c = market_mid
        print(f"[C CALIBRATE] market={market_mid:.1f} | scale={self.c_price_scale:.1f} "
              f"| E[Δr]={e_delta_r:.2f}bps | eps={eps:.4f}")

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
        raw = self._raw(self.eps_c, e_delta_r)
        self.fair_c = raw * self.c_price_scale
        return self.fair_c

    def cross_asset_signal(self, c_market_mid: float) -> float:
        """
        Returns mispricing: positive = C expensive vs model, negative = C cheap.

        signal > threshold  → sell C
        signal < -threshold → buy C
        """
        if self.fair_c is None:
            return 0.0
        return c_market_mid - self.fair_c

    def reset(self):
        """Reset C model for new round."""
        self.eps_c = None
        self.calibrated = False
        self.c_baseline_price = None
        self.c_baseline_e_dr = None
        self.c_baseline_raw = None
        self.c_price_scale = 1.0
        self.fair_c = None
        print("[CALIBRATE] ✓ Model C reset for new round")
