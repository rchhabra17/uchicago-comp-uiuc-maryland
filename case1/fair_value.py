# In fair_value.py

import math

# Add these constants at the top — update when released on Ed
C_GAMMA  = 0.5
C_BETA_Y = 0.0001
C_Y0     = 0.04
C_PE0    = 20.0
C_B0     = 1000.0
C_D      = 7.0
C_CCONV  = 50.0
C_LAMBDA = 0.5
C_N      = 100.0

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


# Then in FairValueEngine.__init__, add:
#   self.fed   = FedModel()
#   self.eps_c = None
#   self.fair_c = None

# Add these methods to FairValueEngine:
def update_c(self, eps: float) -> float | None:
    self.eps_c = eps
    return self._compute_c()

def recompute_c(self) -> float | None:
    return self._compute_c()

def _compute_c(self) -> float | None:
    if self.eps_c is None:
        return None
    y_t     = self.fed.implied_yield
    delta_y = y_t - C_Y0
    pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
    p_ops   = self.eps_c * pe_t
    delta_b = C_B0 * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
    self.fair_c = p_ops + C_LAMBDA * delta_b / C_N
    print(f"[C FV] fair={self.fair_c:.2f}  eps={self.eps_c:.4f}  PE={pe_t:.2f}")
    return self.fair_c

# And in get(), add:
#   if symbol == "C":
#       return self.fair_c

class FairValueEngine:
    def __init__(self):
        self.eps_a = None
        self.fair_a = None
        self.pe_a = None
        self.calibrating = True
        self.fed   = FedModel()
        self.eps_c = None
        self.fair_c = None

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

    def get(self, symbol: str) -> float | None:
        if symbol == "A":
            return self.fair_a
        if symbol == "C":
            return self.fair_c
        return None
    
    def update_c(self, eps: float) -> float | None:
        self.eps_c = eps
        return self._compute_c()

    def recompute_c(self) -> float | None:
        return self._compute_c()

    def _compute_c(self) -> float | None:
        if self.eps_c is None:
            return None
        y_t     = self.fed.implied_yield
        delta_y = y_t - C_Y0
        pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
        p_ops   = self.eps_c * pe_t
        delta_b = C_B0 * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
        self.fair_c = p_ops + C_LAMBDA * delta_b / C_N
        print(f"[C FV] fair={self.fair_c:.2f}  eps={self.eps_c:.4f}  PE={pe_t:.2f}")
        return self.fair_c

    def infer_eps_c(self, market_mid: float):
        if self.eps_c is None:
            y_t     = self.fed.implied_yield
            delta_y = y_t - C_Y0
            pe_t    = C_PE0 * math.exp(-C_GAMMA * delta_y)
            delta_b = C_B0 * (-C_D * delta_y + 0.5 * C_CCONV * delta_y ** 2)
            
            p_ops = market_mid - (C_LAMBDA * delta_b / C_N)
            self.eps_c = p_ops / pe_t
            print(f"[CALIBRATE] Implied EPS_C = {self.eps_c:.4f} from mid={market_mid}")
            self._compute_c()