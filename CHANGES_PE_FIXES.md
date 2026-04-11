# PE Calibration Fixes - Change Log

**Date**: 2026-04-08
**Issue**: PE calibration for Stock A was fragile (single-point calibration led to 49% error: fair=570 vs market=850)
**Root cause**: Calibrated PE=672 from first earnings (mid=936, eps=1.393), but market now implies PE=1002

## Changes Made

### FIX 1: Multi-Point Calibration (Robust PE Estimation)

**fair_value.py**:
- Added `self.pe_samples = []` to collect (eps, mid) pairs
- Modified `calibrate_pe(eps, market_mid)` to:
  - Collect 3 samples instead of 1
  - Compute **median** PE to reject outliers
  - Print all samples when calibration completes

**bot.py**:
- Updated `calibrate_after_delay()` to pass `eps` to `calibrate_pe()`
- Now triggers calibration sample collection on **first 3 earnings** instead of just the first

**Effect**: PE calibration is now robust to single bad market reads. Takes ~2-4 minutes to complete.

---

### FIX 2: Adaptive PE Adjustment (Self-Correction)

**fair_value.py**:
- Modified `update_a(eps, market_mid)` to:
  - Accept optional `market_mid` parameter
  - Compute `implied_pe = market_mid / eps`
  - If `|implied_pe - stored_pe| / stored_pe > 10%`, slowly adjust: `pe = 0.95*pe + 0.05*implied_pe`
  - Print `[PE ADAPT]` when adjustment happens

**bot.py**:
- Updated earnings handler to pass `market_mid` to `update_a()` after calibration
- This enables continuous PE correction based on market feedback

**Effect**: If PE was miscalibrated, it will slowly converge to market consensus. 95/5 EMA prevents whipsawing.

---

### FIX 3: Sanity Check (Prevent Catastrophic Quoting)

**bot.py**:
- Added sanity check at start of `quote_around()`:
  - If `|market_mid - fair| > 50`, print `[SANITY]` warning and return without quoting
  - Prevents quoting at insane levels when fair value is obviously wrong

**Effect**: Stops bleeding when PE is badly miscalibrated. Better to not trade than to offer free money.

---

### BONUS: Updated Risk Limits to Match Competition

**config.py**:
- Changed `MAX_POSITION = 100` → `MAX_POSITION = 200` (actual competition limit)
- Added `SOFT_LIMIT_A = 100` and `SOFT_LIMIT_C = 50` (internal risk management)

**bot.py**:
- Updated `sweep_book()` to use `SOFT_LIMIT_A/C` instead of hardcoded `50`
- Updated `quote_around()` to:
  - Squelch threshold: `75% of soft_limit` (was hardcoded 30)
  - Hard cutoff: `90% of soft_limit` (was hardcoded 40)

**Effect**: Can now use up to 100 shares for A (was capped at 50), with proper inventory management scaling.

---

## How to Revert

If any fix causes problems, revert in reverse order:

### Revert FIX 3 (Sanity Check):
Remove the sanity check block at the start of `quote_around()` in `bot.py` (lines ~87-95).

### Revert FIX 2 (Adaptive PE):
In `fair_value.py`, remove the adaptive adjustment block in `update_a()` (lines ~88-95).
In `bot.py`, remove `market_mid=mid` parameter from `update_a()` call in earnings handler.

### Revert FIX 1 (Multi-Point):
In `fair_value.py`:
- Remove `self.pe_samples = []` from `__init__`
- Replace `calibrate_pe()` with old version:
  ```python
  def calibrate_pe(self, market_mid: float):
      if self.eps_a is not None and market_mid is not None:
          self.pe_a = market_mid / self.eps_a
          self.calibrating = False
          self.fair_a = self.eps_a * self.pe_a
          print(f"[CALIBRATE] PE_A = {self.pe_a:.1f} (from mid={market_mid}, eps={self.eps_a})")
  ```

In `bot.py`:
- Change `calibrate_pe(eps, mid)` back to `calibrate_pe(mid)` in `calibrate_after_delay()`

### Revert Risk Limits:
In `config.py`: set `MAX_POSITION = 100`, remove `SOFT_LIMIT_*` lines.
In `bot.py`: restore hardcoded `50`, `40`, `30` in `sweep_book()` and `quote_around()`.

---

## Testing Checklist

- [ ] Watch for `[CALIBRATE]` logs on first 3 earnings — should see 3 samples, then median PE
- [ ] Watch for `[PE ADAPT]` logs — should only fire if market PE diverges >10% from stored PE
- [ ] Watch for `[SANITY]` logs — should fire if fair is >50 points off market mid
- [ ] Confirm no quoting when sanity check triggers
- [ ] Verify PE converges to reasonable value after 3 earnings
- [ ] Check that soft limits allow larger positions without hitting hard caps

---

## Expected Behavior

**First 3 earnings**:
```
[EARNINGS] A: EPS=1.393, calibrating... waiting for market to settle
[CALIBRATE] Sample 1/3: eps=1.3930, mid=936.0, implied_PE=672.1
[EARNINGS] A: EPS=1.250, calibrating... waiting for market to settle
[CALIBRATE] Sample 2/3: eps=1.2500, mid=1050.0, implied_PE=840.0
[EARNINGS] A: EPS=1.100, calibrating... waiting for market to settle
[CALIBRATE] Sample 3/3: eps=1.1000, mid=950.0, implied_PE=863.6
[CALIBRATE DONE] PE_A = 840.0 (median of ['672.1', '840.0', '863.6'])
```

**Later, if market drifts**:
```
[EARNINGS] A: EPS=0.848, fair=712.3, market_mid=850
[PE ADAPT] 840.0 → 844.0 (market implied 1002.4, error=19.3%)
```

**If PE is way off**:
```
[SANITY] A fair=570 vs market=850 (Δ=280), SKIPPING quote
```
