"""Measurement, not assertion.

Round 2 asks teams to demonstrate the bias-toward-escalation deliberately.
Saying so in a comment is not a demonstration. This module produces:

  confusion_matrix()   assigned tier vs reference tier, all 30 patients
  triage_rates()       under-triage and over-triage as numbers
  threshold_sweep()    the actual tradeoff curve, so the tuning choice is visible
  replay()             DoorBoard order vs FIFO and vs ESI-only, timed
  latency()            how long scoring and ranking actually take

The reference labels are a team consensus over synthetic patients. They are not
clinical ground truth and are labelled as such everywhere they appear.
"""

from __future__ import annotations

import time

from .models import Patient, RoomState
from .scoring import SAFE_WAIT_MIN, TIER_CUTOFFS, score_patient, tier_for


REFERENCE_NOTE = (
    "reference_priority is a consensus target tier agreed by the team over "
    "synthetic patients, used to make the under-triage claim measurable. It is "
    "not clinical ground truth and no clinician has signed it off."
)

# how long each tier should wait before it is a safety problem
TIER_TARGET_MIN = dict(SAFE_WAIT_MIN)


def assigned_tiers(patients: list[Patient], *, shift_mode: str = "normal",
                   cutoffs=None, room: RoomState | None = None) -> dict[str, int]:
    """Measures what actually ships: the ranked board, ML lift included."""
    from .ranking import rank_patients

    if room is None:
        from .models import PROFILES
        room = PROFILES["urban"].room(surge=(shift_mode == "surge"))
    room = _with_mode(room, shift_mode)
    out = {}
    for r in rank_patients(patients, room):
        if cutoffs is None:
            out[r.patient_id] = r.tier
        else:
            out[r.patient_id] = _tier_with(r.raw_points, cutoffs)
    return out


def _with_mode(room: RoomState, shift_mode: str) -> RoomState:
    import copy as _copy
    r = _copy.deepcopy(room)
    r.shift_mode = shift_mode
    return r


def _tier_with(raw: float, cutoffs) -> int:
    a, b, c, d = cutoffs
    if raw >= a:
        return 1
    if raw >= b:
        return 2
    if raw >= c:
        return 3
    if raw >= d:
        return 4
    return 5


def reference_map(patients: list[Patient]) -> dict[str, int]:
    return {p.id: int(p.reference_priority) for p in patients if p.reference_priority}


def confusion_matrix(patients: list[Patient], *, shift_mode: str = "normal",
                     cutoffs=None, room: RoomState | None = None) -> dict:
    ref = reference_map(patients)
    got = assigned_tiers(patients, shift_mode=shift_mode, cutoffs=cutoffs, room=room)
    grid = {r: {c: 0 for c in range(1, 6)} for r in range(1, 6)}
    cells = []
    for pid, r in ref.items():
        g = got.get(pid)
        if g is None:
            continue
        grid[r][g] += 1
        cells.append({"id": pid, "reference": r, "assigned": g, "delta": g - r})
    return {"grid": grid, "cells": cells, "n": len(cells), "note": REFERENCE_NOTE}


def triage_rates(patients: list[Patient], *, shift_mode: str = "normal",
                 cutoffs=None, room: RoomState | None = None) -> dict:
    cm = confusion_matrix(patients, shift_mode=shift_mode, cutoffs=cutoffs, room=room)
    n = cm["n"] or 1
    under = [c for c in cm["cells"] if c["delta"] > 0]     # assigned less urgent
    over = [c for c in cm["cells"] if c["delta"] < 0]      # assigned more urgent
    exact = [c for c in cm["cells"] if c["delta"] == 0]
    crit_ref = [c for c in cm["cells"] if c["reference"] <= 2]
    crit_missed = [c for c in crit_ref if c["assigned"] > 2]
    return {
        "n": cm["n"],
        "exact": len(exact),
        "exact_pct": round(100.0 * len(exact) / n, 1),
        "under_triage": len(under),
        "under_triage_pct": round(100.0 * len(under) / n, 1),
        "over_triage": len(over),
        "over_triage_pct": round(100.0 * len(over) / n, 1),
        "under_ids": [c["id"] for c in under],
        "over_ids": [c["id"] for c in over],
        "critical_n": len(crit_ref),
        "critical_missed": len(crit_missed),
        "critical_missed_ids": [c["id"] for c in crit_missed],
        "critical_sensitivity": round(
            100.0 * (len(crit_ref) - len(crit_missed)) / max(1, len(crit_ref)), 1),
        "note": REFERENCE_NOTE,
    }


def threshold_sweep(patients: list[Patient], *, shift_mode: str = "normal") -> list[dict]:
    """Move the tier-2 cutoff and watch under-triage trade against over-triage.

    This is the asymmetric-cost design choice made visible. The shipped cutoff
    is the leftmost point where under-triage hits zero.
    """
    a, _, c, d = TIER_CUTOFFS
    out = []
    for b in range(40, 141, 2):
        rates = triage_rates(patients, shift_mode=shift_mode, cutoffs=(a, float(b), c, d))
        out.append({
            "tier2_cutoff": b,
            "under_pct": rates["under_triage_pct"],
            "over_pct": rates["over_triage_pct"],
            "exact_pct": rates["exact_pct"],
            "critical_missed": rates["critical_missed"],
            "shipped": abs(b - TIER_CUTOFFS[1]) < 1.0,
        })
    return out


def _order_doorboard(patients: list[Patient], room: RoomState) -> list[str]:
    from .ranking import rank_patients
    return [r.patient_id for r in rank_patients(patients, room)]


def _order_fifo(patients: list[Patient]) -> list[str]:
    return [p.id for p in sorted(patients, key=lambda x: -x.wait_min)]


def _order_esi(patients: list[Patient]) -> list[str]:
    return [p.id for p in sorted(patients, key=lambda x: (x.esi, -x.wait_min))]


# service model for the replay: how long one patient occupies the nurse
SERVICE_MIN = 3.0


def replay(patients: list[Patient], room: RoomState) -> dict:
    """Time-to-first-assessment under three orderings.

    Model: one intake stream, SERVICE_MIN per patient, position in the queue
    determines when they are seen. Crude on purpose and stated as such. It
    isolates the only thing the ordering controls, which is position.
    """
    ref = reference_map(patients)
    orders = {
        "doorboard": _order_doorboard(patients, room),
        "fifo": _order_fifo(patients),
        "esi_only": _order_esi(patients),
    }
    wait_by = {}
    for name, order in orders.items():
        wait_by[name] = {pid: (i * SERVICE_MIN) for i, pid in enumerate(order)}

    def mean_for(name, want) -> float:
        vals = [w for pid, w in wait_by[name].items() if ref.get(pid) in want]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    unstable = {1, 2}
    mild = {4, 5}
    out = {
        "service_min": SERVICE_MIN,
        "n": len(patients),
        "unstable_n": sum(1 for v in ref.values() if v in unstable),
        "orders": orders,
        "assumption": (
            f"single intake stream, {SERVICE_MIN:.0f} min per patient, position "
            "in queue is the only variable. Not a discrete-event ED simulation."
        ),
    }
    for name in orders:
        out[name] = {
            "unstable_mean_min": round(mean_for(name, unstable), 1),
            "mild_mean_min": round(mean_for(name, mild), 1),
            "worst_unstable_min": round(
                max([w for pid, w in wait_by[name].items() if ref.get(pid) in unstable] or [0]), 1),
        }
    out["gain_vs_fifo_min"] = round(
        out["fifo"]["unstable_mean_min"] - out["doorboard"]["unstable_mean_min"], 1)
    out["gain_vs_esi_min"] = round(
        out["esi_only"]["unstable_mean_min"] - out["doorboard"]["unstable_mean_min"], 1)
    out["mild_cost_vs_fifo_min"] = round(
        out["doorboard"]["mild_mean_min"] - out["fifo"]["mild_mean_min"], 1)
    return out


def latency(patients: list[Patient], room: RoomState, repeats: int = 5) -> dict:
    from .ranking import rank_patients

    t0 = time.perf_counter()
    for p in patients:
        score_patient(p)
    per_patient_ms = (time.perf_counter() - t0) * 1000.0 / max(1, len(patients))

    times = []
    for _ in range(repeats):
        t = time.perf_counter()
        rank_patients(patients, room)
        times.append((time.perf_counter() - t) * 1000.0)
    times.sort()
    return {
        "n": len(patients),
        "score_ms_per_patient": round(per_patient_ms, 2),
        "rank_ms_median": round(times[len(times) // 2], 1),
        "rank_ms_worst": round(times[-1], 1),
        "repeats": repeats,
        "note": "wall clock, local CPU, no network. Excludes UI render.",
    }


def summary(patients: list[Patient], room: RoomState) -> dict:
    return {
        "rates_normal": triage_rates(patients, shift_mode="normal"),
        "rates_surge": triage_rates(patients, shift_mode="surge"),
        "replay": replay(patients, room),
        "latency": latency(patients, room),
        "cutoffs": TIER_CUTOFFS,
    }
