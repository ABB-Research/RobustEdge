"""Normal-only anomaly detector wrappers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.mixture import GaussianMixture
from sklearn.neural_network import MLPRegressor
from sklearn.svm import OneClassSVM


class BaseDetector(ABC):
    """Abstract interface for all detectors.

    Higher scores must always mean more anomalous.
    """

    name: str

    @abstractmethod
    def fit(self, X: np.ndarray) -> "BaseDetector":
        raise NotImplementedError

    @abstractmethod
    def score(self, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError


@dataclass
class PCAReconstructionDetector(BaseDetector):
    n_components: int | float = 0.95
    name: str = "pca"

    def fit(self, X: np.ndarray) -> "PCAReconstructionDetector":
        self.model_ = PCA(n_components=self.n_components, svd_solver="full" if isinstance(self.n_components, float) else "auto")
        self.model_.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        z = self.model_.transform(X)
        xhat = self.model_.inverse_transform(z)
        return np.mean((X - xhat) ** 2, axis=1)


@dataclass
class GMMDetector(BaseDetector):
    n_components: int = 2
    covariance_type: str = "diag"
    random_state: int = 42
    name: str = "gmm"

    def fit(self, X: np.ndarray) -> "GMMDetector":
        self.model_ = GaussianMixture(n_components=self.n_components, covariance_type=self.covariance_type, random_state=self.random_state, reg_covar=1e-6)
        self.model_.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        return -self.model_.score_samples(X)


@dataclass
class OCSVMDetector(BaseDetector):
    kernel: str = "rbf"
    nu: float = 0.05
    gamma: str | float = "scale"
    name: str = "ocsvm"

    def fit(self, X: np.ndarray) -> "OCSVMDetector":
        self.model_ = OneClassSVM(kernel=self.kernel, nu=self.nu, gamma=self.gamma)
        self.model_.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        return -self.model_.decision_function(X).ravel()


@dataclass
class IsolationForestDetector(BaseDetector):
    n_estimators: int = 300
    contamination: str | float = "auto"
    random_state: int = 42
    name: str = "isolation_forest"

    def fit(self, X: np.ndarray) -> "IsolationForestDetector":
        self.model_ = IsolationForest(n_estimators=self.n_estimators, contamination=self.contamination, random_state=self.random_state, n_jobs=-1)
        self.model_.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        return -self.model_.score_samples(X)


@dataclass
class ShallowAutoencoderDetector(BaseDetector):
    hidden_layer_sizes: tuple[int, ...] = (64, 16, 64)
    max_iter: int = 300
    random_state: int = 42
    name: str = "autoencoder"

    def fit(self, X: np.ndarray) -> "ShallowAutoencoderDetector":
        self.model_ = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            activation="relu",
            solver="adam",
            alpha=1e-4,
            learning_rate_init=1e-3,
            max_iter=self.max_iter,
            early_stopping=True,
            validation_fraction=0.1,
            random_state=self.random_state,
        )
        self.model_.fit(X, X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        xhat = self.model_.predict(X)
        return np.mean((X - xhat) ** 2, axis=1)


def default_detectors(random_state: int = 42, include_autoencoder: bool = True) -> list[BaseDetector]:
    detectors: list[BaseDetector] = [
        PCAReconstructionDetector(),
        GMMDetector(random_state=random_state),
        OCSVMDetector(),
        IsolationForestDetector(random_state=random_state),
    ]
    if include_autoencoder:
        detectors.append(ShallowAutoencoderDetector(random_state=random_state))
    return detectors
