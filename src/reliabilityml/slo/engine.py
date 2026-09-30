"""SLIs, error budgets and multi-window burn-rate alerts (after the Google SRE Workbook)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

WINDOW_DAYS = 30
LATENCY_THRESHOLD_MS = 300.0


@dataclass(frozen=True)
class Slo:
    name: str
    target: float

    @property
    def allowed_error_rate(self) -> float:
        return 1 - self.target

    def budget_minutes(self, days: int = WINDOW_DAYS) -> float:
        """Minutes of full outage the budget allows over the window (99.9% over 30 days = 43.2)."""
        return self.allowed_error_rate * days * 24 * 60


AVAILABILITY = Slo("availability", 0.999)
LATENCY = Slo("latency", 0.99)


@dataclass(frozen=True)
class BurnRule:
    severity: str
    long: str
    short: str
    threshold: float

    @property
    def budget_share(self) -> float:
        """Share of a 30-day budget spent if this burn rate is sustained over the long window."""
        return self.threshold * pd.Timedelta(self.long) / pd.Timedelta(days=WINDOW_DAYS)


BURN_RULES = [
    BurnRule("page", "1h", "5min", 14.4),
    BurnRule("page", "6h", "30min", 6.0),
    BurnRule("ticket", "3D", "6h", 1.0),
]


def burn_rate(bad_fraction: float, slo: Slo) -> float:
    """Observed error rate divided by the allowed error rate. 1.0 spends the budget exactly."""
    return bad_fraction / slo.allowed_error_rate


def latency_good_share(
    p50: pd.Series | np.ndarray, p95: pd.Series | np.ndarray, threshold: float = LATENCY_THRESHOLD_MS
) -> np.ndarray:
    """Share of requests faster than the threshold, from a log-normal fitted to p50 and p95."""
    p50 = np.maximum(np.asarray(p50, dtype=float), 1e-3)
    p95 = np.maximum(np.asarray(p95, dtype=float), p50 * 1.0001)
    sigma = np.log(p95 / p50) / norm.ppf(0.95)
    return norm.cdf((np.log(threshold) - np.log(p50)) / sigma)


def sli_minutes(requests_per_s: pd.Series, error_rate: pd.Series, p50: pd.Series, p95: pd.Series) -> pd.DataFrame:
    """Per-minute request counts, and bad requests for each SLI."""
    total = requests_per_s * 60
    return pd.DataFrame(
        {
            "total": total,
            "bad_availability": total * error_rate.clip(0, 1),
            "bad_latency": total * (1 - latency_good_share(p50, p95)),
        },
        index=requests_per_s.index,
    )


def rolling_burn(sli: pd.DataFrame, column: str, window: str, slo: Slo) -> pd.Series:
    bad = sli[column].rolling(window).sum()
    total = sli["total"].rolling(window).sum()
    return (bad / total.replace(0, np.nan)) / slo.allowed_error_rate


def burn_alerts(sli: pd.DataFrame, column: str, slo: Slo) -> pd.DataFrame:
    """Minutes where both windows of a rule exceed its threshold, collapsed into firing periods."""
    rows = []
    for rule in BURN_RULES:
        long = rolling_burn(sli, column, rule.long, slo)
        short = rolling_burn(sli, column, rule.short, slo)
        firing = (long > rule.threshold) & (short > rule.threshold)
        # A rule can only fire once its long window is full of data; a 3-day window holding one hour
        # of history would otherwise page on the first few errors.
        firing &= sli.index >= sli.index[0] + pd.Timedelta(rule.long) - pd.Timedelta(minutes=1)
        start = None
        for ts, on in firing.items():
            if on and start is None:
                start = ts
            elif not on and start is not None:
                rows.append(
                    {
                        "severity": rule.severity,
                        "rule": f"{rule.long}/{rule.short} > {rule.threshold:g}",
                        "start": start,
                        "end": ts,
                        "peak_long": float(long[start:ts].max()),
                    }
                )
                start = None
        if start is not None:
            rows.append(
                {
                    "severity": rule.severity,
                    "rule": f"{rule.long}/{rule.short} > {rule.threshold:g}",
                    "start": start,
                    "end": firing.index[-1],
                    "peak_long": float(long[start:].max()),
                }
            )
    return pd.DataFrame(rows, columns=["severity", "rule", "start", "end", "peak_long"])


def status(sli: pd.DataFrame, column: str, slo: Slo, at: pd.Timestamp | None = None) -> dict:
    """SLI, remaining budget and current burn rates over the 30 days ending at `at`."""
    at = at or sli.index[-1]
    window = sli.loc[at - pd.Timedelta(days=WINDOW_DAYS) + pd.Timedelta(minutes=1) : at]
    bad, total = window[column].sum(), window["total"].sum()
    bad_fraction = bad / total if total else 0.0
    consumed = bad_fraction / slo.allowed_error_rate
    burns = {}
    for label in ("5min", "30min", "1h", "6h", "3D"):
        w = sli.loc[at - pd.Timedelta(label) + pd.Timedelta(minutes=1) : at]
        frac = w[column].sum() / w["total"].sum() if w["total"].sum() else 0.0
        burns[label] = float(burn_rate(frac, slo))
    return {
        "slo": slo.name,
        "target": slo.target,
        "sli": float(1 - bad_fraction),
        "window_days": round((window.index[-1] - window.index[0]).total_seconds() / 86400 + 1 / 1440, 2),
        "budget_consumed": float(consumed),
        "budget_remaining": float(1 - consumed),
        "budget_minutes_total": slo.budget_minutes(),
        "budget_minutes_left": float(slo.budget_minutes() * (1 - consumed)),
        "burn_rates": burns,
    }
