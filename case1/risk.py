import time

class RiskManager:
    def __init__(self):
        self.positions = {}
        self.last_signal_time = {}

    # def update_fill(self, symbol: str, side: str, qty: int):
    #     current = self.positions.get(symbol, 0)
    #     if side == "buy":
    #         self.positions[symbol] = current + qty
    #     else:
    #         self.positions[symbol] = current - qty

    # update fill for B, case mismatch have to fix for everything later - uncomment above after fixing later
    def update_fill(self, symbol: str, side: str, qty: int):
        current = self.positions.get(symbol, 0)
        if side.upper() == "BUY":
            self.positions[symbol] = current + qty
        else:
            self.positions[symbol] = current - qty

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    # def can_trade(self, symbol: str, side: str, qty: int, max_pos: int) -> bool:
    #     current = self.get_position(symbol)
    #     new_pos = current + qty if side == "buy" else current - qty
    #     return abs(new_pos) <= max_pos
    
    # commented above for upper case issue again, uncomment for other stocks - need to make it uniform later
    def can_trade(self, symbol: str, side: str, qty: int, max_pos: int) -> bool:
        current = self.get_position(symbol)
        new_pos = current + qty if side.upper() == "BUY" else current - qty
        return abs(new_pos) <= max_pos

    # B-only risk helpers

    def can_trade_b_package(self, changes: dict[str, int], max_pos_by_symbol: dict[str, int], max_gross_family: int) -> bool:
        # Per-symbol limits
        for symbol, delta in changes.items():
            cur = self.get_position(symbol)
            lim = max_pos_by_symbol.get(symbol, 999999)
            if abs(cur + delta) > lim:
                # print(f"[RISK BLOCK] {symbol} cur={cur} delta={delta} lim={lim}")
                return False

        # Gross B-family exposure limit
        gross_now = self.gross_b_family_exposure()
        gross_after = gross_now
        for symbol, delta in changes.items():
            old_abs = abs(self.get_position(symbol))
            new_abs = abs(self.get_position(symbol) + delta)
            gross_after += (new_abs - old_abs)

        if gross_after > max_gross_family:
            # print(f"[RISK BLOCK] gross={gross_after} limit={max_gross_family}")
            return False
        return True

    def gross_b_family_exposure(self) -> int:
        total = 0
        for symbol, pos in self.positions.items():
            if symbol == "B" or symbol.startswith("B_C_") or symbol.startswith("B_P_"):
                total += abs(pos)
        return total

    def on_cooldown(self, signal_key: str, cooldown_s: float) -> bool:
        now = time.time()
        last = self.last_signal_time.get(signal_key, 0.0)
        if now - last < cooldown_s:
            return True
        self.last_signal_time[signal_key] = now
        return False
    

