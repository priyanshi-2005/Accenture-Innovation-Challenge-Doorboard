"""Pipeline: load → score → rank → override → surge → profiles."""

from __future__ import annotations

import copy
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from .models import (
    PROFILES,
    AuditEvent,
    HospitalProfile,
    Patient,
    RankedRow,
    RoomState,
    Vitals,
)
from .bands import DEVIATIONS, PEDS_NORMAL, age_band_for, vital_scale
from .feedback import REASON_CODES, SAFETY_CODES
from .ranking import rank_patients


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "patients.json"

# DPDP + clinical governance: what an override must record
OVERRIDE_LEGAL_NOTE = (
    "DPDP demo: record clinician ID and role, timestamp, patient ID, prior "
    "recommendation, new decision, reason code, free-text reason, whether a "
    "safety hold was cleared, and purpose limitation (triage assist only)."
)

LAWFUL_BASIS = (
    "DPDP Act 2023 s.7 certain legitimate uses, medical emergency limb; "
    "consent under s.6 for any non-emergency retention beyond the episode; "
    "s.9 verifiable parental consent path for patients under 18."
)


def _vitals_from_dict(d: dict | None) -> Vitals:
    d = d or {}
    return Vitals(
        hr=d.get("hr"),
        spo2=d.get("spo2"),
        sbp=d.get("sbp"),
        temp_c=d.get("temp_c"),
        rr=d.get("rr"),
    )


def patient_from_dict(d: dict) -> Patient:
    age = int(d["age"])
    tags = list(d.get("tags", []))
    return Patient(
        id=d["id"],
        name=d["name"],
        age=age,
        age_band=age_band_for(age),
        complaint=d["complaint"],
        look=d["look"],
        arrival=d.get("arrival", "Walk-in"),
        esi=int(d["esi"]),
        vitals=_vitals_from_dict(d.get("vitals")),
        wait_min=int(d.get("wait_min", 0)),
        chair=d.get("chair", ""),
        has_prior_record=bool(d.get("has_prior_record", False)),
        history_note=d.get("history_note", ""),
        tags=tags,
        consent_doorboard=bool(d.get("consent_doorboard", True)),
        language_barrier=bool(d.get("language_barrier", False) or "language_barrier" in tags),
        bounceback_72h=bool(d.get("bounceback_72h", False) or "bounceback_72h" in tags),
        isolation_needed=bool(d.get("isolation_needed", False) or "isolation" in tags),
        pregnant=bool(d.get("pregnant", False) or "pregnant" in tags),
        intoxicated=bool(d.get("intoxicated", False) or "intoxicated" in tags),
        pathway=str(d.get("pathway", "") or ""),
        pathway_min=int(d.get("pathway_min", 0) or 0),
        vitals_series=list(d.get("vitals_series") or []),
        reference_priority=int(d.get("reference_priority", 0) or 0),
    )


def load_patients(path: Path | None = None) -> list[Patient]:
    p = path or DATA_PATH
    raw = json.loads(p.read_text())
    return [patient_from_dict(x) for x in raw["patients"]]


def load_meta(path: Path | None = None) -> dict:
    p = path or DATA_PATH
    return json.loads(p.read_text()).get("meta", {})


def next_patient_id(patients: list[Patient]) -> str:
    """Next synthetic ID: P31 after max numeric suffix among P## ids."""
    best = 0
    for p in patients:
        pid = p.id or ""
        if pid.startswith("P") and pid[1:].isdigit():
            best = max(best, int(pid[1:]))
    return f"P{best + 1}"


def _opt_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def patient_from_intake(fields: dict[str, Any], *, patient_id: str | None = None) -> Patient:
    """Build a Patient from chart / add-arrival form fields. Optional vitals may be blank."""
    age = int(fields["age"])
    name = str(fields["name"]).strip()
    complaint = str(fields["complaint"]).strip()
    if not name or not complaint:
        raise ValueError("Name and complaint are required.")
    esi = int(fields["esi"])
    if esi < 1 or esi > 5:
        raise ValueError("ESI must be 1–5.")
    vitals = Vitals(
        hr=_opt_float(fields.get("hr")),
        spo2=_opt_float(fields.get("spo2")),
        sbp=_opt_float(fields.get("sbp")),
        temp_c=_opt_float(fields.get("temp_c")),
        rr=_opt_float(fields.get("rr")),
    )
    pid = patient_id or str(fields.get("id") or "").strip() or "PNEW"
    look = str(fields.get("look") or "").strip() or "Appearance not recorded"
    arrival = str(fields.get("arrival") or "Walk-in").strip() or "Walk-in"
    wait_min = int(fields.get("wait_min") or 0)
    chair = str(fields.get("chair") or "").strip() or "Intake"
    pathway = str(fields.get("pathway") or "").strip()
    if pathway == "(none)":
        pathway = ""
    return Patient(
        id=pid,
        name=name,
        age=age,
        age_band=age_band_for(age),
        complaint=complaint,
        look=look,
        arrival=arrival,
        esi=esi,
        vitals=vitals,
        wait_min=max(0, wait_min),
        chair=chair,
        has_prior_record=bool(fields.get("has_prior_record", False)),
        history_note=str(fields.get("history_note") or ""),
        tags=list(fields.get("tags") or []),
        consent_doorboard=bool(fields.get("consent_doorboard", True)),
        language_barrier=bool(fields.get("language_barrier", False)),
        bounceback_72h=bool(fields.get("bounceback_72h", False)),
        isolation_needed=bool(fields.get("isolation_needed", False)),
        pregnant=bool(fields.get("pregnant", False)),
        intoxicated=bool(fields.get("intoxicated", False)),
        pathway=pathway,
        pathway_min=int(fields.get("pathway_min") or 0),
        vitals_series=list(fields.get("vitals_series") or []),
        reference_priority=int(fields.get("reference_priority") or 0),
    )


def apply_chart_update(patients: list[Patient], patient_id: str, fields: dict[str, Any]) -> list[Patient]:
    """Replace one patient from chart form fields; preserves id."""
    updated = patient_from_intake(fields, patient_id=patient_id)
    out = []
    found = False
    for p in patients:
        if p.id == patient_id:
            out.append(updated)
            found = True
        else:
            out.append(p)
    if not found:
        out.append(updated)
    return out


def get_profile(profile_id: str = "urban") -> HospitalProfile:
    return PROFILES.get(profile_id, PROFILES["urban"])


def room_for(
    profile_id: str = "urban",
    *,
    surge: bool = False,
    shift_mode: str | None = None,
) -> RoomState:
    profile = get_profile(profile_id)
    room = profile.room(surge=surge)
    if shift_mode:
        room.shift_mode = shift_mode
    elif surge:
        room.shift_mode = "surge"
    else:
        room.shift_mode = "normal"
    return room


def default_room(surge: bool = False) -> RoomState:
    return room_for("urban", surge=surge)


def rank_room(
    patients: list[Patient],
    room: RoomState | None = None,
) -> list[RankedRow]:
    room = room or default_room(False)
    return rank_patients(patients, room)


def override_options() -> list[dict]:
    """Reason codes a charge nurse picks from. Free text is captured too."""
    out = []
    for code, (label, direction, is_safety) in REASON_CODES.items():
        out.append({
            "code": code,
            "label": label,
            "direction": direction,
            "safety_critical": code in SAFETY_CODES or is_safety,
        })
    return out


class SafetyConfirmRequired(Exception):
    """Raised when a safety hold would be cleared without explicit confirmation."""


def apply_override(
    rows: list[RankedRow],
    patient_id: str,
    reason: str,
    actor: str = "charge_nurse",
    clock: str = "19:41",
    *,
    patients: list[Patient] | None = None,
    reason_code: str = "clinical_concern",
    direction: str = "",
    confirmed_safety: bool = False,
    actor_role: str = "charge_nurse",
) -> tuple[list[RankedRow], AuditEvent] | tuple[list[Patient], list[RankedRow], AuditEvent]:
    """Single code path for every override.

    Before, this rewrote a string and something else did the reordering, so a
    safety hold could be cleared with nothing recorded about it. Now:

      * the reorder tag and the audit event are written together
      * clearing a "will not mark can-wait" hold needs confirmed_safety=True
      * safety-critical overrides are logged at their own severity so they can
        be counted at the end of a shift

    Returns (rows, event) for the old two-value callers, or
    (patients, rows, event) when a patient list is passed in.
    """
    meta = REASON_CODES.get(reason_code)
    if meta is None:
        reason_code = "clinical_concern"
        meta = REASON_CODES[reason_code]
    label, code_dir, code_safety = meta
    direction = direction or code_dir

    out = copy.deepcopy(rows)
    before = ""
    after = ""
    cleared_hold = False
    target = None
    for r in out:
        if r.patient_id != patient_id:
            continue
        target = r
        before = f"#{r.order} {r.space} | {r.why}"
        if r.refuse_can_wait and direction == "down":
            cleared_hold = True
        break

    severity = "safety_critical" if (cleared_hold or reason_code in SAFETY_CODES) else "routine"
    if severity == "safety_critical" and not confirmed_safety:
        raise SafetyConfirmRequired(
            "This override lowers a patient the engine is holding on a safety "
            "rule. Confirm explicitly before it is recorded."
        )

    if target is not None:
        target.why = f"Changed by charge ({label}): {reason.strip()}"
        target.flag = "" if direction != "up" else "hot"
        if cleared_hold:
            target.refuse_can_wait = False
        after = f"{target.space} | {target.why}"

    new_patients = None
    if patients is not None:
        new_patients = []
        for q in patients:
            q = copy.deepcopy(q)
            if q.id == patient_id:
                if direction == "up" and "charge_priority" not in q.tags:
                    q.tags.append("charge_priority")
                if direction == "down" and "charge_priority" in q.tags:
                    q.tags.remove("charge_priority")
                q.history_note = f"OVERRIDE: {label} - {reason.strip()}"
            new_patients.append(q)

    event = AuditEvent(
        ts=clock,
        actor=actor,
        action="override",
        patient_id=patient_id,
        detail=reason.strip(),
        before=before,
        after=after,
        legal_note=OVERRIDE_LEGAL_NOTE,
        reason_code=reason_code,
        severity=severity,
        actor_role=actor_role,
        safety_confirmed=bool(confirmed_safety) if severity == "safety_critical" else False,
        lawful_basis=LAWFUL_BASIS,
    )
    if patients is not None:
        return new_patients, out, event
    return out, event


def tick_waits(patients: list[Patient], minutes: int = 5) -> list[Patient]:
    out = []
    for p in patients:
        q = copy.deepcopy(p)
        q.wait_min += minutes
        out.append(q)
    return out


def mark_vitals_worse(
    patients: list[Patient], patient_id: str, new_vitals: dict
) -> list[Patient]:
    out = []
    for p in patients:
        q = copy.deepcopy(p)
        if q.id == patient_id:
            for k, val in new_vitals.items():
                setattr(q.vitals, k, val)
            if "vitals_worse" not in q.tags:
                q.tags.append("vitals_worse")
            if "quiet_drifter" not in q.tags:
                q.tags.append("quiet_drifter")
            if "quieter" not in q.look.lower():
                q.look = q.look + " · quieter"
        out.append(q)
    return out


SURGE_MIX_NORMAL = {1: 0.01, 2: 0.14, 3: 0.45, 4: 0.28, 5: 0.12}
# a real surge is not more of the same people. Mass-casualty and respiratory
# spikes shift the mix toward the top and bring more EMS arrivals.
SURGE_MIX_SURGE = {1: 0.03, 2: 0.24, 3: 0.46, 4: 0.20, 5: 0.07}

_SURGE_BANK = {
    1: [("Unresponsive, found down", "Unresponsive, pale", (38, 74)),
        ("Major trauma - road traffic", "Grey, cold peripheries", (19, 61))],
    2: [("Shortness of breath, worsening", "Working to breathe", (44, 82)),
        ("Chest pain radiating to arm", "Sweaty, anxious", (48, 79)),
        ("Weakness one side since 40 min", "Confused", (58, 86)),
        ("Fever and drowsy", "Lethargic, pale", (1, 12))],
    3: [("Abdominal pain since morning", "Talking, uncomfortable", (21, 66)),
        ("Vomiting, cannot keep fluids", "Dry lips, tired", (2, 9)),
        ("Fall from standing", "Quiet, talking", (66, 88)),
        ("Headache, worst so far", "Talking", (24, 58)),
        ("Fever and cough three days", "Comfortable, talking", (27, 71))],
    4: [("Sprained wrist", "Walking, talking", (17, 49)),
        ("Small laceration to forearm", "Walking, talking", (20, 55)),
        ("Ear pain", "Alert, talking", (3, 11))],
    5: [("Prescription refill", "Comfortable, talking", (25, 63)),
        ("Sore throat two days", "Comfortable, talking", (18, 44))],
}

_SURGE_NAMES = [
    "Bhat, K", "Naidu, R", "Pillai, S", "Verma, A", "Dsouza, M", "Ahmed, Z",
    "Kulkarni, P", "Ghosh, T", "Rathore, V", "Sethi, N", "Mishra, J", "Bora, D",
    "Salunke, H", "Tirkey, A", "Zachariah, L", "Mandal, R", "Oommen, P",
    "Waghmare, S", "Barman, K", "Deshpande, U",
]


def _surge_vitals(esi: int, age: int, rng) -> Vitals:
    """Physiology consistent with the drawn severity, not a copied row."""
    from .bands import PEDS_NORMAL, age_band_for as _band

    band = _band(age)
    if band in PEDS_NORMAL:
        lo_hr, hi_hr, lo_rr, hi_rr = PEDS_NORMAL[band]
        hr_mid = (lo_hr + hi_hr) / 2
        rr_mid = (lo_rr + hi_rr) / 2
        sbp_mid = 95
    else:
        hr_mid, rr_mid, sbp_mid = 78, 16, 126
    sick = {1: 3.0, 2: 1.8, 3: 0.8, 4: 0.2, 5: 0.0}[esi]
    hr = hr_mid + sick * rng.uniform(9, 18) + rng.gauss(0, 5)
    rr = rr_mid + sick * rng.uniform(2, 5) + rng.gauss(0, 1.5)
    spo2 = 98 - sick * rng.uniform(1.2, 3.0) + rng.gauss(0, 1.0)
    sbp = sbp_mid - sick * rng.uniform(4, 12) + rng.gauss(0, 7)
    temp = 36.8 + rng.gauss(0, 0.4)
    if rng.random() < 0.35:
        temp += rng.uniform(0.8, 2.2)
    v = Vitals(
        hr=round(max(30.0, hr)), spo2=round(min(100.0, max(70.0, spo2))),
        sbp=round(max(55.0, sbp)), temp_c=round(temp, 1), rr=round(max(6.0, rr)),
    )
    # intake under pressure misses readings more often
    miss_p = 0.30 if esi >= 3 else 0.12
    for attr in ("spo2", "sbp", "temp_c", "rr", "hr"):
        if rng.random() < miss_p / 4:
            setattr(v, attr, None)
    return v


def simulate_surge(
    patients: list[Patient],
    factor: float = 3.0,
    profile_id: str = "urban",
    seed: int = 20260831,
) -> tuple[list[Patient], RoomState]:
    """A surge, not a photocopier.

    New arrivals are sampled from a shifted acuity mix with their own
    physiology, ages, arrival modes and staggered wait times. The old version
    cloned mild walk-ins, which triples the row count without testing anything
    the engine has to get right.
    """
    rng = random.Random(seed)
    room = room_for(profile_id, surge=True, shift_mode="surge")

    target = int(round(len(patients) * factor))
    n_new = max(0, target - len(patients))
    levels = sorted(SURGE_MIX_SURGE)
    weights = [SURGE_MIX_SURGE[k] for k in levels]

    extras: list[Patient] = []
    for i in range(n_new):
        esi = rng.choices(levels, weights=weights, k=1)[0]
        complaint, look, age_range = rng.choice(_SURGE_BANK[esi])
        age = rng.randint(age_range[0], age_range[1])
        if esi <= 2:
            arrival = "EMS" if rng.random() < 0.62 else "Walk-in"
        else:
            arrival = "Walk-in" if rng.random() < 0.85 else "Transfer"
        prior = rng.random() < 0.5
        pid = f"S{i+1:02d}"
        extras.append(Patient(
            id=pid,
            name=f"{_SURGE_NAMES[i % len(_SURGE_NAMES)]} ({pid})",
            age=age,
            age_band=age_band_for(age),
            complaint=complaint,
            look=look,
            arrival=arrival,
            esi=esi,
            vitals=_surge_vitals(esi, age, rng),
            wait_min=rng.randint(0, 26),
            chair=f"S{i+1}",
            has_prior_record=prior,
            history_note="Prior attendance on file" if prior else "",
            tags=["surge_arrival"],
            reference_priority=esi,
        ))
    return list(patients) + extras, room


def surge_mix_report(patients: list[Patient], surged: list[Patient]) -> dict:
    """Shows the mix actually shifted, so the demo beat is provable."""
    def mix(rows):
        c = Counter(p.esi for p in rows)
        n = len(rows) or 1
        return {k: round(100.0 * c.get(k, 0) / n, 1) for k in range(1, 6)}

    new = [p for p in surged if "surge_arrival" in p.tags]
    return {
        "baseline_n": len(patients),
        "surge_n": len(surged),
        "factor": round(len(surged) / max(1, len(patients)), 2),
        "baseline_mix_pct": mix(patients),
        "new_arrivals_mix_pct": mix(new),
        "combined_mix_pct": mix(surged),
        "ems_share_new_pct": round(
            100.0 * sum(1 for p in new if "ems" in p.arrival.lower()) / max(1, len(new)), 1),
        "note": "new arrivals drawn from a shifted acuity mix, not cloned rows",
    }


def mock_integrations(
    profile: HospitalProfile, *, force_degraded: bool = False
) -> dict[str, Any]:
    """Read-only adapters — maturity contrast without claiming live EHR write."""
    room = profile.room(surge=False)
    maturity = getattr(profile, "maturity", "high")
    ehr_up = bool(profile.has_ehr_feed) and not force_degraded
    beds_up = bool(profile.has_bed_board) and not force_degraded
    return {
        "maturity": maturity,
        "maturity_label": {
            "high": "High — FHIR beds + roster + EHR read",
            "mid": "Mid — HL7 ADT + CSV beds · partial EHR",
            "low": "Low — paper roster · CSV beds · first-minute only",
        }.get(maturity, maturity),
        "degraded_mode": not ehr_up,
        "force_degraded": force_degraded,
        "degraded_note": (
            "EHR read unavailable — DoorBoard uses first-minute facts only "
            "(complaint, look, vitals taken at the door). Prior-history weight = 0."
            if not ehr_up
            else "EHR read mock online — prior records available for ~half the room."
        ),
        "bed_board": {
            "source": (
                "FHIR Bed mock"
                if beds_up
                else ("CSV bed sheet mock" if maturity != "low" else "Whiteboard / CSV mock")
            ),
            "status": "ok" if beds_up or maturity != "low" else "degraded",
            "last_sync": "19:39" if beds_up else "manual 19:20",
            "resus_open": profile.resus_open,
            "bay_open": profile.bay_open,
            "chair_open": profile.chair_open,
            "isolation_open": room.isolation_open,
            "boarders_blocking": room.boarders_blocking,
        },
        "roster": {
            "source": "Staff roster API mock" if ehr_up else "Paper roster mock",
            "status": "ok" if ehr_up else "manual",
            "last_sync": "19:38" if ehr_up else "shift start",
            "nurses_on_floor": profile.nurses_on_floor,
            "charge_nurse": "CN-Demo",
        },
        "ehr": {
            "source": "HL7/FHIR read mock" if ehr_up else "No EHR — first-minute only",
            "status": "ok" if ehr_up else "offline",
            "write_enabled": False,
            "prior_record_lookup": ehr_up,
            "pathway_timers": ehr_up and profile.has_stroke_bay,
        },
        "specialty": {
            "trauma": profile.has_trauma,
            "peds": profile.has_peds,
            "stroke_bay": profile.has_stroke_bay,
            "stroke_policy": profile.stroke_policy,
            "note": profile.specialty_note,
        },
        "isolation_policy": {
            "source": "Infection control mock",
            "airborne_rooms": room.isolation_open,
        },
    }


def explain_bits(row: RankedRow, limit: int = 3) -> list[str]:
    """Short reasons a nurse can read in ~5 seconds."""
    parts = [p.strip() for p in (row.why or "").split("·") if p.strip()]
    conf_words = {"high": "Sure", "medium": "Somewhat", "low": "Unsure"}
    conf = conf_words.get(row.confidence_label, row.confidence_label)
    extras: list[str] = []
    if row.escalate_bias:
        extras.append("Being careful — unsure, so moved up")
    if not row.has_prior_record:
        extras.append("First visit — no prior chart weight")
    if getattr(row, "refuse_can_wait", False):
        extras.append("Cannot wait — flagged for eye")
    merged = parts + [e for e in extras if e not in parts]
    if not merged:
        merged = [
            f"Urgency {row.acuity:.0f}",
            f"{conf} {row.confidence:.0%}",
            row.space,
        ]
    # Always end with destination if room remains
    out = merged[: max(1, limit - 1)]
    dest = row.space.replace("Bay open", "Bay free").replace("Isolation open", "Isolation free")
    if dest and dest not in out and len(out) < limit:
        out.append(dest)
    return out[:limit]


def patients_over_safe_wait(patients: list[Patient]) -> list[str]:
    """IDs whose wait exceeds ESI safe ceiling (from scoring SAFE_WAIT_MIN)."""
    from .scoring import SAFE_WAIT_MIN

    out = []
    for p in patients:
        ceil = SAFE_WAIT_MIN.get(int(p.esi), 30)
        if p.wait_min > ceil:
            out.append(p.id)
    return out


def apply_staffing_shock(room: RoomState, nurses_down: int = 3) -> RoomState:
    """Mid-shift call-outs: fewer nurses, tighter space, surge-like escalate."""
    out = copy.deepcopy(room)
    out.nurses_on_floor = max(2, out.nurses_on_floor - nurses_down)
    out.bay_open = max(1, out.bay_open - 1)
    out.chair_open = max(2, out.chair_open - 2)
    out.staffing_shock = True
    out.surge = True
    out.shift_mode = "surge"
    return out


def age_aware_vs_adult_only(patients: list[Patient], room: RoomState) -> list[dict]:
    """Safety proof: where adult-only thresholds under-call ped/geri risk."""
    rows = rank_room(patients, room)
    diffs = []
    for r in rows:
        if r.age_band == "adult":
            continue
        delta = round(r.acuity - r.adult_only_acuity, 1)
        if abs(delta) >= 1:
            diffs.append(
                {
                    "id": r.patient_id,
                    "name": r.name,
                    "age_band": r.age_band,
                    "age": r.age,
                    "age_aware": r.acuity,
                    "adult_only": r.adult_only_acuity,
                    "delta": delta,
                    "risk": "adult-only under-calls" if delta > 0 else "adult-only over-calls",
                }
            )
    diffs.sort(key=lambda x: -abs(x["delta"]))
    return diffs


def fever_twin_proof(patients: list[Patient], room: RoomState | None = None) -> list[dict]:
    """Same 38.5C in a 3yo, a 36yo and a 75yo, scored on published scales."""
    from .scoring import score_patient

    room = room or default_room(False)
    mode = room.shift_mode or "normal"
    twins = []
    for p in patients:
        if p.vitals.temp_c != 38.5:
            continue
        if not any(t.startswith("fever_twin") for t in p.tags) and p.id not in ("P07", "P08", "P09"):
            continue
        age_scale = vital_scale(p.vitals, p.age_band)
        adult_scale = vital_scale(p.vitals, "adult")
        sc = score_patient(p, force_adult_band=False, shift_mode=mode)
        ao = score_patient(p, force_adult_band=True, shift_mode=mode)
        twins.append({
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "age_band": p.age_band,
            "temp_c": 38.5,
            "scale_used": age_scale["scale"],
            "temp_points_age_aware": age_scale["sub"].get("temp", 0),
            "temp_points_adult_only": adult_scale["sub"].get("temp", 0),
            "early_warning_age_aware": age_scale["total"],
            "early_warning_adult_only": adult_scale["total"],
            "acuity_age_aware": sc["acuity"],
            "acuity_adult_only": ao["acuity"],
            "look": p.look,
        })
    order = {"preschool": 0, "toddler": 0, "infant": 0, "school": 0,
             "adolescent": 0, "adult": 1, "geriatric": 2}
    twins.sort(key=lambda x: order.get(x["age_band"], 9))
    return twins


def peds_band_proof(patients: list[Patient]) -> list[dict]:
    """The bug we shipped in the first cut, and the fix.

    One 0-17 pediatric band flagged a 16 year old with HR 64 as concerning,
    because it was carrying infant thresholds. Five APLS strata fix it. This
    shows the before and after on our own data.
    """
    out = []
    for p in patients:
        if p.age >= 18 or p.vitals.hr is None:
            continue
        lo, hi, _, _ = PEDS_NORMAL[p.age_band]
        stratified = vital_scale(p.vitals, p.age_band)["sub"].get("hr", 0)
        # what the old single-band model did: infant thresholds for everyone
        old_flag = p.vitals.hr > 140 or p.vitals.hr < 70
        out.append({
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "band": p.age_band,
            "hr": p.vitals.hr,
            "normal_for_age": f"{lo}-{hi}",
            "old_single_band_flag": bool(old_flag),
            "stratified_points": stratified,
            "fixed": bool(old_flag) and stratified == 0,
        })
    out.sort(key=lambda x: x["age"])
    return out


def scale_provenance() -> dict:
    return {
        "adult": "NEWS2, Royal College of Physicians, 2017",
        "pediatric": "APLS/PALS age-banded normal ranges, PEWS-style 0-3 scoring",
        "triage_frame": "ESI 1-5 as the reference severity scale",
        "deviations": list(DEVIATIONS),
    }


def asymmetric_cost_statement() -> str:
    return (
        "Design choice (explicit): under-triage costs more than over-triage. "
        "When confidence is low or symptoms are ambiguous, DoorBoard raises acuity "
        "(escalate) instead of optimizing for average accuracy."
    )


def rows_to_dicts(rows: list[RankedRow]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in rows]


EYE_GROUPS = (
    ("worse", "Getting worse", "Accept the move or keep them"),
    ("wait", "Wait too long", "Reassess — don’t leave them parked"),
    ("missing", "Missing a key reading", "Don’t mark can-wait"),
    ("lwbs", "May leave", "Don’t treat as safely waiting"),
    ("careful", "Being careful", "Uncertain — don’t downgrade"),
    ("moved", "Moved up", "Check why they jumped"),
)


def eye_reason(row: RankedRow, baseline: dict | None = None) -> str | None:
    """Primary reason this row needs the charge nurse."""
    baseline = baseline or {}
    was = baseline.get(row.patient_id)
    moved = was is not None and was > row.order
    if row.reassess_flag in ("vitals_worse", "both"):
        return "worse"
    if row.reassess_flag == "wait_exceeded":
        return "wait"
    if row.refuse_can_wait or row.flag == "hold":
        return "missing"
    if row.lwbs_risk:
        return "lwbs"
    if row.escalate_bias or row.confidence_label == "low":
        return "careful"
    if moved:
        return "moved"
    return None


def group_eye_rows(rows: list[RankedRow], baseline: dict | None = None):
    """[(title, hint, rows)] in nurse-priority order, empty groups omitted."""
    buckets: dict[str, list[RankedRow]] = {k: [] for k, _, _ in EYE_GROUPS}
    for r in rows:
        key = eye_reason(r, baseline)
        if key:
            buckets[key].append(r)
    out = []
    for key, title, hint in EYE_GROUPS:
        if buckets[key]:
            out.append((key, title, hint, buckets[key]))
    return out


def space_is_open(space: str) -> bool:
    """True if the suggested space is actually free to send into."""
    s = (space or "").lower()
    if s.startswith("waiting") or s.startswith("hold"):
        return False
    return True


def space_kind(space: str) -> str:
    s = (space or "").lower()
    if "isolation" in s:
        return "isolation"
    if "resus" in s:
        return "resus"
    if "bay" in s:
        return "bay"
    if "chair" in s:
        return "chair"
    return "wait"


def consume_open_space(room: RoomState, space: str) -> tuple[RoomState, str]:
    """Take one slot of the suggested kind. Returns (room, kind)."""
    out = copy.deepcopy(room)
    kind = space_kind(space)
    if kind == "resus":
        out.resus_open = max(0, out.resus_open - 1)
    elif kind == "bay":
        out.bay_open = max(0, out.bay_open - 1)
    elif kind == "chair":
        out.chair_open = max(0, out.chair_open - 1)
    elif kind == "isolation":
        out.isolation_open = max(0, out.isolation_open - 1)
    return out, kind


def slot_inventory(room: RoomState, fills: list[dict]) -> list[dict]:
    """Live space buckets for the desk."""
    by_kind = {"resus": [], "bay": [], "chair": [], "isolation": []}
    for f in fills or []:
        k = f.get("kind") or "wait"
        if k in by_kind:
            by_kind[k].append(f)
    return [
        {
            "kind": "resus",
            "label": "Resus",
            "open": room.resus_open,
            "filled": by_kind["resus"],
        },
        {
            "kind": "bay",
            "label": "Bays",
            "open": room.bay_open,
            "filled": by_kind["bay"],
        },
        {
            "kind": "chair",
            "label": "Chairs",
            "open": room.chair_open,
            "filled": by_kind["chair"],
        },
        {
            "kind": "isolation",
            "label": "Isolation",
            "open": room.isolation_open,
            "filled": by_kind["isolation"],
        },
    ]
