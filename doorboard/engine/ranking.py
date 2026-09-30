"""Whole-room ranking + open-space routing."""

from __future__ import annotations

from .models import Patient, RankedRow, RoomState
from .scoring import display_acuity, score_patient, tier_for

USE_ML = True  # rules-only fallback if sklearn is unavailable


def _vitals_text(p: Patient) -> str:
    v = p.vitals
    bits = []
    if v.hr is not None:
        bits.append(f"HR {int(v.hr)}")
    if v.spo2 is not None:
        bits.append(f"SpO2 {int(v.spo2)}")
    if v.sbp is not None:
        bits.append(f"BP {int(v.sbp)}")
    if v.temp_c is not None:
        bits.append(f"Temp {v.temp_c}")
    if v.rr is not None:
        bits.append(f"RR {int(v.rr)}")
    if not bits:
        return "No vitals yet"
    missing = v.missing_keys()
    text = " · ".join(bits)
    if missing:
        text += f" · blank: {', '.join(missing)}"
    return text


def _assign_space(p: Patient, scored: dict, room: RoomState, claimed: dict) -> str:
    acuity = scored["acuity"]
    # Isolation cohorting before normal bays
    if p.isolation_needed:
        if claimed.get("isolation", 0) < room.isolation_open:
            claimed["isolation"] = claimed.get("isolation", 0) + 1
            return "Isolation open"
        return "Waiting · need isolation"
    if p.esi <= 2 or p.pathway in ("stroke", "stemi"):
        if claimed["resus"] < room.resus_open:
            claimed["resus"] += 1
            return "Resus"
        return "Hold for resus"
    if acuity >= 48 or p.esi == 3 or p.pregnant:
        effective_bays = max(0, room.bay_open - min(room.boarders_blocking, room.bay_open))
        # boarders reduce usable bays
        if claimed["bay"] < effective_bays:
            claimed["bay"] += 1
            return "Bay open"
        if room.boarders_blocking > 0:
            return "Waiting · bay boarded"
        return "Waiting · need bay"
    if claimed["chair"] < room.chair_open:
        claimed["chair"] += 1
        return "Chair"
    return "Waiting · chairs full"


def _why(p: Patient, scored: dict, peers: list[tuple[Patient, dict]], space: str) -> str:
    bits = []
    if p.history_note.startswith("OVERRIDE:"):
        return f"Changed by charge: {p.history_note.replace('OVERRIDE:', '').strip()} · → {space}"
    if scored["escalate_bias"]:
        bits.append(f"Uncertain → escalated ({scored.get('shift_mode', 'normal')} shift)")
    if scored["refuse_can_wait"]:
        miss = ", ".join(scored["missing_vitals"][:2]) or "key vitals"
        bits.append(f"Will not mark can-wait — {miss} missing")
    if p.pathway:
        bits.append(f"{p.pathway.upper()} pathway {p.pathway_min}m — clock running")
    if p.bounceback_72h:
        bits.append("Bounceback <72h — do not treat as new mild")
    if p.pregnant:
        bits.append("Pregnancy — abdominal pathway caution")
    if p.language_barrier:
        bits.append("Language barrier — thin history of present illness")
    if p.intoxicated:
        bits.append("Intoxicated — unreliable historian")
    if p.isolation_needed:
        bits.append("Isolation / cohort needed")
    if scored.get("lwbs_risk"):
        bits.append(f"LWBS risk — wait {p.wait_min}m mild ESI {p.esi}")
    if scored.get("wait_exceeded"):
        bits.append(f"Wait {p.wait_min}m over safe for ESI {p.esi}")
    det = scored.get("deterioration") or {}
    if det.get("flag") and det.get("method") == "trend":
        bits.append(f"Trend rising: {det['detail']}")
    elif det.get("flag"):
        bits.append("Quiet change / vitals worse while waiting")
    if scored.get("ml_lifted"):
        bits.append(f"Risk model raised this one (p={scored.get('model_prob'):.2f})")
    if scored.get("single_param_red"):
        bits.append(f"One parameter at red on {scored.get('vital_scale','scale')}")
    if "under_report" in p.tags:
        bits.append("Under-reports — trust look/vitals over words")
    if "ambiguous" in p.tags:
        bits.append("Ambiguous complaint — do not park low")
    if p.has_prior_record and scored.get("history_boost", 0) > 0:
        bits.append(f"History weight +{scored['history_boost']:.0f}")
    elif not p.has_prior_record:
        bits.append("First-time / thin history")
    if p.age_band not in ("adult",):
        bits.append(f"{p.age_band.title()} thresholds ({scored.get('vital_scale','')})")

    same = [(q, s) for q, s in peers if q.id != p.id and q.esi == p.esi]
    if same:
        same.sort(key=lambda x: -x[1]["acuity"])
        top = same[0]
        if scored["acuity"] >= top[1]["acuity"]:
            bits.append(
                f"Before {top[0].name.split(',')[0]} (ESI {p.esi}): "
                f"{scored['acuity']:.0f} vs {top[1]['acuity']:.0f}"
            )
        else:
            bits.append(f"Same ESI {p.esi} — after higher-acuity peer")

    bits.append(f"→ {space}")
    return " · ".join(bits[:4])


def _reassess_flag(p: Patient, scored: dict) -> str:
    wait = scored.get("wait_exceeded", False)
    worse = bool((scored.get("deterioration") or {}).get("flag"))
    if wait and worse:
        return "both"
    if wait:
        return "wait_exceeded"
    if worse:
        return "vitals_worse"
    return ""


def rank_patients(patients: list[Patient], room: RoomState) -> list[RankedRow]:
    mode = room.shift_mode or ("surge" if room.surge else "normal")
    # Staffing shock: behave like surge escalate even if not volume-surge
    if room.staffing_shock and mode == "normal":
        mode = "surge"

    scored_pairs: list[tuple[Patient, dict]] = []
    adult_only: dict[str, float] = {}

    ml_on = False
    risks = [{} for _ in patients]
    if USE_ML:
        try:
            from . import ml
            ml_on = ml.available()
            if ml_on:
                risks = ml.risk_batch(patients)
        except Exception:
            ml_on = False

    for idx, p in enumerate(patients):
        sc = score_patient(p, force_adult_band=False, shift_mode=mode)
        if ml_on:
            r = risks[idx]
            b = ml.blend(sc["raw_points"], r.get("prob"))
            sc["model_prob"] = r.get("prob")
            sc["model_source"] = r.get("source", "")
            sc["ml_lifted"] = b["lifted"]
            sc["ml_disagree"] = b["disagree"]
            if b["lifted"]:
                # the model can raise someone the rules under-rate, never lower
                sc["raw_points"] = round(b["raw"], 1)
                sc["acuity"] = display_acuity(b["raw"])
                sc["tier"] = tier_for(b["raw"])
        else:
            sc["model_prob"] = None
            sc["model_source"] = ""
            sc["ml_lifted"] = False
            sc["ml_disagree"] = False
        scored_pairs.append((p, sc))
        ao = score_patient(p, force_adult_band=True, shift_mode=mode)
        adult_only[p.id] = ao["acuity"]

    def _priority(p: Patient) -> int:
        return 0 if "charge_priority" in (p.tags or []) else 1

    if mode == "quiet":
        scored_pairs.sort(
            key=lambda x: (
                _priority(x[0]),
                -x[1]["raw_points"],
                x[0].esi,
                -x[0].wait_min,
                x[1]["confidence"],
            )
        )
    else:
        scored_pairs.sort(
            key=lambda x: (
                _priority(x[0]),
                -x[1]["raw_points"],
                x[0].esi,
                x[1]["confidence"],
                -x[0].wait_min,
            )
        )

    claimed = {"resus": 0, "bay": 0, "chair": 0, "isolation": 0}
    rows: list[RankedRow] = []
    for i, (p, sc) in enumerate(scored_pairs, start=1):
        space = _assign_space(p, sc, room, claimed)
        flag = ""
        if sc["refuse_can_wait"]:
            flag = "hold"
        reassess = _reassess_flag(p, sc)
        if reassess:
            flag = "hot" if flag != "hold" else "hold"
        if (sc.get("deterioration") or {}).get("flag") or "under_report" in p.tags:
            flag = flag or "hot"
        if sc.get("lwbs_risk"):
            flag = flag or "watch"

        rows.append(
            RankedRow(
                order=i,
                patient_id=p.id,
                name=p.name,
                age=p.age,
                age_band=p.age_band,
                chair=p.chair or f"C{i}",
                complaint=p.complaint,
                look=p.look,
                arrival=p.arrival,
                vitals_text=_vitals_text(p),
                esi=p.esi,
                space=space,
                why=_why(p, sc, scored_pairs, space),
                acuity=sc["acuity"],
                confidence=sc["confidence"],
                confidence_label=sc["confidence_label"],
                refuse_can_wait=sc["refuse_can_wait"],
                escalate_bias=sc["escalate_bias"],
                reassess_flag=reassess,
                flag=flag,
                has_prior_record=p.has_prior_record,
                tags=list(p.tags),
                history_boost=sc["history_boost"],
                adult_only_acuity=adult_only[p.id],
                input_weights=sc["input_weights"],
                consent_doorboard=getattr(p, "consent_doorboard", True),
                lwbs_risk=bool(sc.get("lwbs_risk")),
                isolation_needed=p.isolation_needed,
                pathway=p.pathway,
                pathway_min=p.pathway_min,
                language_barrier=p.language_barrier,
                bounceback_72h=p.bounceback_72h,
                pregnant=p.pregnant,
                intoxicated=p.intoxicated,
                raw_points=sc["raw_points"],
                tier=sc["tier"],
                information=sc["information"],
                information_label=sc["information_label"],
                information_reasons=sc["information_reasons"],
                vital_scale=sc["vital_scale"],
                vital_subscores=sc["vital_subscores"],
                vital_total=sc["vital_total"],
                single_param_red=sc["single_param_red"],
                model_prob=sc.get("model_prob"),
                model_source=sc.get("model_source", ""),
                ml_lifted=bool(sc.get("ml_lifted")),
                deterioration=sc["deterioration"],
                negated_terms=sc["negated_terms"],
                raw_parts=sc["raw_parts"],
                band_group=sc["band_group"],
            )
        )
    return rows
