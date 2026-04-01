# In fair_value.py
class FairValueEngine:
    def __init__(self):
        self.eps_a = None
        self.fair_a = None
        self.pe_a = None
        self.calibrating = True

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
        return None