# fair_value.py

import math

# C parameters — revealed by organizers
C_Y0        = 0.045   # baseline yield
C_PE0       = 14.0    # baseline P/E
C_EPS0      = 2.00    # baseline EPS (for sanity checks / fallback)
C_D         = 7.5     # duration
C_CCONV     = 55.0    # convexity
C_B0_OVER_N = 40.0    # bond portfolio value per share (B0 / N)
C_LAMBDA    = 0.65    # bond component weighting

# C parameters — NOT revealed, must assume & tune
C_GAMMA     = 15.0    # PE sensitivity to yield changes (tune live; 0.5 was way too low)
C_BETA_Y    = 0.0001  # yield sensitivity to E[Δr] in bps (25bp E[Δr] → 25bp yield move)


class FedModel:
    def __init__(self):
        self.q_hike: float = 1/3
        self.q_hold: float = 1/3
        self.q_cut:  float = 1/3

    def update_from_book_mids(self, hike_mid: float, hold_mid: float, cut_mid: float):
        total = hike_mid + hold_mid + cut_mid
        if total <= 0:
            return
        self.q_hike = hike_mid / total
        self.q_hold = hold_mid / total
        self.q_cut  = cut_mid  / total
        print(f"[FED] hike={self.q_hike:.2%}  hold={self.q_hold:.2%}  cut={self.q_cut:.2%}")

    def update_from_cpi(self, forecast: float, actual: float):
        surprise = actual - forecast
        shift = 0.02 * surprise
        self.q_hike = max(0.0, self.q_hike + shift)
        self.q_cut  = max(0.0, self.q_cut  - shift)
        self._normalise()
        print(f"[FED CPI] surprise={surprise:+.3f}  hike={self.q_hike:.2%}  hold={self.q_hold:.2%}  cut={self.q_cut:.2%}")

    def _normalise(self):
        total = self.q_hike + self.q_hold + self.q_cut
        if total > 0:
            self.q_hike /= total
            self.q_hold /= total
            self.q_cut  /= total

    @property
    def expected_delta_r(self) -> float:
        return 25 * self.q_hike + 0 * self.q_hold + (-25) * self.q_cut

    @property
    def implied_yield(self) -> float:
        return C_Y0 + C_BETA_Y * self.expected_delta_r


class FairValueEngine:
    def __init__(self):
        # A
        self.eps_a = None
        self.fair_a = None
        self.pe_a = None
        self.calibrating = True
        # C
        self.fed = FedModel()
        self.eps_c = None
        self.fair_c = None

    # ---------- A ----------
    def update_a(self, eps: float, market_mid: float = None) -> float | None:
        self.eps_a = eps

        if self.calibrating and market_mid is not None:
            # First earnings: don't trust our PE, learn from market
            # We can't use mid RIGHT NOW (market hasn't reacted yet)
            # So we flag that we need to calibrate after the market settles
            return None

        if self.pe_a is not None:
            self.fair_a = eps * self.pe_a
            return self.fair_a
        return None

    def calibrate_pe(self, market_mid: float):
        """Call this ~10s after first earnings, once market has settled."""
        if self.eps_a is not None and market_mid is not None:
            self.pe_a = market_mid / self.eps_a
            self.calibrating = False
            self.fair_a = self.eps_a * self.pe_a
            print(f"[CALIBRATE] PE_A = {self.pe_a:.1f} (from mid={market_mid}, eps={self.eps_a})")

    # ---------- C ----------
    def update_c(self, eps: float) -> float | None:
        self.eps_c = eps
        return self._compute_c()

    def recompute_c(self) -> float | None:
        """Call when Fed probabilities change but EPS hasn't."""
        return self._compute_c()

    def _compute_c(self) -> float | None:
        if self.eps_c is None:
            return None
        y_t     = self.fed.implied_yield
        delta_y = y_t - C_Y0
        pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
        p_ops   = self.eps_c * pe_t
        # bond P&L per share (B0_OVER_N already folds in the 1/N)
        delta_b_per_share = C_B0_OVER_N * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
        self.fair_c = p_ops + C_LAMBDA * delta_b_per_share
        print(f"[C FV] fair={self.fair_c:.2f}  eps={self.eps_c:.4f}  PE={pe_t:.2f}  Δy={delta_y:+.5f}")
        return self.fair_c

    def infer_eps_c(self, market_mid: float):
        """Back out implied EPS from market mid before the first C earnings release."""
        if self.eps_c is None:
            y_t     = self.fed.implied_yield
            delta_y = y_t - C_Y0
            pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
            delta_b_per_share = C_B0_OVER_N * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)

            p_ops = market_mid - (C_LAMBDA * delta_b_per_share)
            self.eps_c = p_ops / pe_t
            print(f"[CALIBRATE] Implied EPS_C = {self.eps_c:.4f} from mid={market_mid}")
            self._compute_c()

    # ---------- accessor ----------
    def get(self, symbol: str) -> float | None:
        if symbol == "A":
            return self.fair_a
        if symbol == "C":
            return self.fair_c
        return None