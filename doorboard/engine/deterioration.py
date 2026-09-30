"""Deterioration while waiting.

Before, `quiet_drifter` was a tag someone typed into the JSON. That is not a
prediction, it is a label. This computes a trend instead: score every recorded
vitals timepoint on the patient's own age-appropriate scale and look at the
slope. Two timepoints is enough to have a direction.

A rising early-warning score in the waiting room is the single most useful
signal you can get for free, because the vitals are already being retaken.
"""

from __future__ import annotations

from .bands import vital_scale
from .models import Patient, Vitals


# NEWS2/PEWS points gained per hour that we treat as a real trend
RISE_PER_HOUR_FLAG = 1.5

TREND_POINTS = 6.0   # per point of early-warning score gained
MAX_TREND_POINTS = 20.0


def _series(p: Patient) -> list[tuple[int, Vitals]]:
    """[(minutes_before_now, vitals)] oldest first, current last."""
    out = []
    for entry in (p.vitals_series or []):
        v = entry.get("vitals") or {}
        out.append((
            int(entry.get("t_minus_min", 0)),
            Vitals(
                hr=v.get("hr"), spo2=v.get("spo2"), sbp=v.get("sbp"),
                temp_c=v.get("temp_c"), rr=v.get("rr"),
            ),
        ))
    out.sort(key=lambda x: -x[0])
    out.append((0, p.vitals))
    return out


def deterioration_signal(p: Patient) -> dict:
    """Trend on the patient's own scale. Falls back to tags if no series."""
    band = p.age_band or "adult"
    series = _series(p)

    if len(series) < 2:
        legacy = "quiet_drifter" in p.tags or "vitals_worse" in p.tags
        return {
            "method": "tag_fallback" if legacy else "none",
            "flag": legacy,
            "points": 14.0 if legacy else 0.0,
            "series_len": 1,
            "delta": 0.0,
            "per_hour": 0.0,
            "detail": "single timepoint, no trend available",
        }

    scored = []
    for t, v in series:
        s = vital_scale(v, band)
        scored.append((t, s["total"]))

    first_t, first_s = scored[0]
    last_t, last_s = scored[-1]
    delta = last_s - first_s
    minutes = max(1, first_t - last_t)
    per_hour = delta * 60.0 / minutes

    flag = per_hour >= RISE_PER_HOUR_FLAG
    pts = 0.0
    if delta > 0:
        pts = min(MAX_TREND_POINTS, delta * TREND_POINTS)

    if "vitals_worse" in p.tags and not flag:
        flag = True
        pts = max(pts, 14.0)

    track = " -> ".join(f"{s}" for _, s in scored)
    return {
        "method": "trend",
        "flag": flag,
        "points": pts,
        "series_len": len(scored),
        "delta": delta,
        "per_hour": round(per_hour, 2),
        "detail": f"early-warning score {track} over {minutes} min",
    }


def waiting_room_watch(patients: list[Patient]) -> list[dict]:
    """Everyone whose trend is rising, worst first. Feeds the queue watcher."""
    out = []
    for p in patients:
        d = deterioration_signal(p)
        if d["flag"] or d["delta"] > 0:
            out.append({
                "id": p.id,
                "name": p.name,
                "esi": p.esi,
                "delta": d["delta"],
                "per_hour": d["per_hour"],
                "detail": d["detail"],
                "flag": d["flag"],
            })
    out.sort(key=lambda x: (-x["per_hour"], -x["delta"]))
    return out
