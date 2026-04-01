class RiskManager:
    def __init__(self):
        self.positions = {}

    def update_fill(self, symbol: str, side: str, qty: int):
        current = self.positions.get(symbol, 0)
        if side == "buy":
            self.positions[symbol] = current + qty
        else:
            self.positions[symbol] = current - qty

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def can_trade(self, symbol: str, side: str, qty: int, max_pos: int) -> bool:
        current = self.get_position(symbol)
        new_pos = current + qty if side == "buy" else current - qty
        return abs(new_pos) <= max_pos