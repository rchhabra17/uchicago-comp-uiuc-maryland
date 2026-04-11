"""
Fair Value Engine for Stock A and C

Stock A: Linear regression model (settled_mid = a + b × EPS)
Stock C: Fed-linked model (COMMENTED OUT for A-only testing)

Based on empirical findings from case1_stock_a_update_v2.md.
"""

import math
import numpy as np
from typing import Optional, List, Tuple


# ============================================================================
# STOCK C CODE - COMMENTED OUT FOR A-ONLY TESTING
# ============================================================================

# # C parameters — revealed by organizers
# C_Y0        = 0.045   # baseline yield
# C_PE0       = 14.0    # baseline P/E
# C_EPS0      = 2.00    # baseline EPS (for sanity checks / fallback)
# C_D         = 7.5     # duration
# C_CCONV     = 55.0    # convexity
# C_B0_OVER_N = 40.0    # bond portfolio value per share (B0 / N)
# C_LAMBDA    = 0.65    # bond component weighting
#
# # C parameters — NOT revealed, must assume & tune
# C_GAMMA     = 15.0    # PE sensitivity to yield changes (tune live; 0.5 was way too low)
# C_BETA_Y    = 0.0001  # yield sensitivity to E[Δr] in bps (25bp E[Δr] → 25bp yield move)
#
#
# class FedModel:
#     def __init__(self):
#         self.q_hike: float = 1/3
#         self.q_hold: float = 1/3
#         self.q_cut:  float = 1/3
#
#     def update_from_book_mids(self, hike_mid: float, hold_mid: float, cut_mid: float):
#         total = hike_mid + hold_mid + cut_mid
#         if total <= 0:
#             return
#         self.q_hike = hike_mid / total
#         self.q_hold = hold_mid / total
#         self.q_cut  = cut_mid  / total
#         print(f"[FED] hike={self.q_hike:.2%}  hold={self.q_hold:.2%}  cut={self.q_cut:.2%}")
#
#     def update_from_cpi(self, forecast: float, actual: float):
#         surprise = actual - forecast
#         shift = 0.02 * surprise
#         self.q_hike = max(0.0, self.q_hike + shift)
#         self.q_cut  = max(0.0, self.q_cut  - shift)
#         self._normalise()
#         print(f"[FED CPI] surprise={surprise:+.3f}  hike={self.q_hike:.2%}  hold={self.q_hold:.2%}  cut={self.q_cut:.2%}")
#
#     def _normalise(self):
#         total = self.q_hike + self.q_hold + self.q_cut
#         if total > 0:
#             self.q_hike /= total
#             self.q_hold /= total
#             self.q_cut  /= total
#
#     @property
#     def expected_delta_r(self) -> float:
#         return 25 * self.q_hike + 0 * self.q_hold + (-25) * self.q_cut
#
#     @property
#     def implied_yield(self) -> float:
#         return C_Y0 + C_BETA_Y * self.expected_delta_r


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

        # # C: Fed-linked model (COMMENTED OUT)
        # self.fed = FedModel()
        # self.eps_c = None
        # self.fair_c = None

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

    # ========================================================================
    # STOCK C — COMMENTED OUT
    # ========================================================================

    # def update_c(self, eps: float) -> Optional[float]:
    #     self.eps_c = eps
    #     return self._compute_c()
    #
    # def recompute_c(self) -> Optional[float]:
    #     """Call when Fed probabilities change but EPS hasn't."""
    #     return self._compute_c()
    #
    # def _compute_c(self) -> Optional[float]:
    #     if self.eps_c is None:
    #         return None
    #     y_t     = self.fed.implied_yield
    #     delta_y = y_t - C_Y0
    #     pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
    #     p_ops   = self.eps_c * pe_t
    #     # bond P&L per share (B0_OVER_N already folds in the 1/N)
    #     delta_b_per_share = C_B0_OVER_N * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
    #     self.fair_c = p_ops + C_LAMBDA * delta_b_per_share
    #     print(f"[C FV] fair={self.fair_c:.2f}  eps={self.eps_c:.4f}  PE={pe_t:.2f}  Δy={delta_y:+.5f}")
    #     return self.fair_c
    #
    # def infer_eps_c(self, market_mid: float):
    #     """Back out implied EPS from market mid before the first C earnings release."""
    #     if self.eps_c is None:
    #         y_t     = self.fed.implied_yield
    #         delta_y = y_t - C_Y0
    #         pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
    #         delta_b_per_share = C_B0_OVER_N * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
    #
    #         p_ops = market_mid - (C_LAMBDA * delta_b_per_share)
    #         self.eps_c = p_ops / pe_t
    #         print(f"[CALIBRATE] Implied EPS_C = {self.eps_c:.4f} from mid={market_mid}")
    #         self._compute_c()

    # ========================================================================
    # ACCESSOR
    # ========================================================================

    def get(self, symbol: str) -> Optional[float]:
        """
        Get fair value for a symbol.

        Args:
            symbol: "A" or "C"

        Returns:
            Fair value if available, else None
        """
        if symbol == "A":
            # Return current fair value if we have the most recent EPS
            # For now, return None (caller must use fair_value_a(eps) directly)
            return None  # A's fair value is EPS-dependent, no static value

        # if symbol == "C":
        #     return self.fair_c

        return None
