"""Threshold calibration."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class QuantileCalibrator:
    """Empirical-quantile threshold calibrated on benign validation scores."""

    quantile: float = 0.995

    def fit(self, scores: np.ndarray) -> "QuantileCalibrator":
        if len(scores) == 0:
            raise ValueError("Cannot calibrate on empty score array.")
        self.threshold_ = float(np.quantile(scores, self.quantile))
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        return (scores > self.threshold_).astype(int)
