from dataclasses import dataclass
import math


# DRV5055A1 @ VCC = 3.3 V (TI SBAS639 datasheet typicals)
DRV5055_A1_VCC = 3.3
DRV5055_A1_VQ = 1.65          # V, quiescent (B = 0)
DRV5055_A1_SENSITIVITY = 0.060  # V/mT


@dataclass
class DRV5055Data:
    delta: float        # V, firmware per-sample delta (volts - previous_volts)
    volts: float = 0.0  # Hall OUT voltage from ADS1115
    raw: int = 0        # ADS1115 conversion counts
    gap_mm: float = float('nan')      # estimated magnet–sensor gap
    gap_delta_mm: float = float('nan')  # change in gap since previous sample


class DRV5055GapModel:
    """Map Hall voltage → approximate gap (mm) via dipole B ∝ 1/d³.

    One-shot rest calibration: measure volts at known rest gap → magnet constant K.
    """

    def __init__(self, rest_gap_mm: float = 6.28, rest_volts: float | None = None):
        self.rest_gap_mm = rest_gap_mm
        self.rest_volts = rest_volts
        self._k = None
        self._last_gap_mm = None
        if rest_volts is not None:
            self._recompute_k()

    def volts_to_b_mt(self, volts: float) -> float:
        return (volts - DRV5055_A1_VQ) / DRV5055_A1_SENSITIVITY

    def _recompute_k(self):
        if self.rest_volts is None or self.rest_gap_mm <= 0:
            self._k = None
            return
        b0 = abs(self.volts_to_b_mt(self.rest_volts))
        if b0 < 1e-6:
            self._k = None
            return
        self._k = b0 * (self.rest_gap_mm ** 3)

    def set_rest(self, volts: float, rest_gap_mm: float | None = None):
        if rest_gap_mm is not None:
            self.rest_gap_mm = rest_gap_mm
        self.rest_volts = volts
        self._recompute_k()

    @property
    def is_calibrated(self) -> bool:
        return self._k is not None and self._k > 0

    def volts_to_gap_mm(self, volts: float) -> float:
        if not self.is_calibrated:
            return float('nan')
        b = abs(self.volts_to_b_mt(volts))
        if b < 1e-6:
            return 1e3  # effectively "far"
        return (self._k / b) ** (1.0 / 3.0)

    def update(self, data: DRV5055Data) -> DRV5055Data:
        gap = self.volts_to_gap_mm(data.volts)
        if self._last_gap_mm is not None and not math.isnan(gap) and not math.isnan(self._last_gap_mm):
            gap_delta = gap - self._last_gap_mm
        else:
            gap_delta = float('nan')
        if not math.isnan(gap):
            self._last_gap_mm = gap
        data.gap_mm = gap
        data.gap_delta_mm = gap_delta
        return data
