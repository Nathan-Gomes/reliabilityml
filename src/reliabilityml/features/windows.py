"""Minute-level rollups and leakage-safe baselines.

Everything here is causal: the value computed for minute t only uses data from minutes
before t (baselines) or up to and including t (current windows).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..generator.simulate import Telemetry, to_minutes
from ..generator.topology import SERVICE_NAMES

DETECTION_SIGNALS = [
    "error_rate",
    "latency_p95",
    "latency_p99",
    "request_rate",
    "cpu_utilization",
    "memory_utilization",
    "db_connections_in_use",
]
# Minimum standard deviation per signal: absolute floor, and floor relative to the mean.
STD_FLOOR = {
    "error_rate": (0.0015, 0.0),
    "latency_p95": (2.0, 0.06),
    "latency_p99": (3.0, 0.08),
    "request_rate": (1.0, 0.06),
    "cpu_utilization": (0.02, 0.0),
    "memory_utilization": (0.01, 0.0),
    "db_connections_in_use": (2.0, 0.0),
}


@dataclass
class MinuteData:
    """Wide minute frames: one DataFrame per signal, indexed by minute, one column per service."""

    frames: dict[str, pd.DataFrame]
    deploys: pd.DataFrame

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.frames["error_rate"].index  # type: ignore[return-value]

    def signal(self, name: str) -> pd.DataFrame:
        return self.frames[name]


def minute_data(tel: Telemetry) -> MinuteData:
    long = to_minutes(tel)
    frames = {
        col: long.pivot(index="ts", columns="service", values=col).reindex(columns=SERVICE_NAMES)
        for col in long.columns
        if col not in ("ts", "service")
    }
    deploys = tel.deploys.copy()
    deploys["minute"] = deploys["ts"].dt.floor("min")
    return MinuteData(frames, deploys)


def ewma_z(
    frame: pd.DataFrame, signal: str, alpha: float = 0.03, upward: bool = True, clip_k: float = 4.0
) -> pd.DataFrame:
    """Z-score of each minute against a robust EWMA baseline built from *previous* minutes only.

    Before a minute updates the baseline it is clipped to mean +/- clip_k standard deviations, so
    an incident cannot inflate its own baseline and hide its second minute.
    """
    x = frame.to_numpy(dtype=float)
    n, cols = x.shape
    absolute, relative = STD_FLOOR[signal]
    z = np.full((n, cols), np.nan)
    first = np.where(np.isnan(x[0]), 0.0, x[0])
    mean = first.copy()
    var = np.zeros(cols)
    warm = 30  # minutes of plain averaging before z-scores are trusted
    for t in range(1, n):
        std = np.maximum(np.sqrt(var), np.maximum(absolute, relative * np.abs(mean)))
        row = x[t]
        valid = ~np.isnan(row)
        if t >= warm:
            z[t, valid] = (row[valid] - mean[valid]) / std[valid]
        update = np.where(valid, np.clip(row, mean - clip_k * std, mean + clip_k * std), mean)
        if t < warm:
            update = np.where(valid, row, mean)
        delta = update - mean
        mean = mean + alpha * delta
        var = (1 - alpha) * (var + alpha * delta**2)
    out = pd.DataFrame(z, index=frame.index, columns=frame.columns)
    return out.clip(lower=0) if upward else out


def z_scores(md: MinuteData, alpha: float = 0.03) -> dict[str, pd.DataFrame]:
    return {s: ewma_z(md.signal(s), s, alpha) for s in DETECTION_SIGNALS}


def z_matrix(z: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Flatten to one row per minute, one column per (signal, service) that exists."""
    parts = []
    for signal, frame in z.items():
        cols = [c for c in frame.columns if frame[c].notna().any()]
        parts.append(frame[cols].add_prefix(f"{signal}|"))
    return pd.concat(parts, axis=1).fillna(0.0)
