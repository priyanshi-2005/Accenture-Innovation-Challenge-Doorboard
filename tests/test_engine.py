"""Tests for the things that would hurt someone if they broke.

Run: python3 tests/test_engine.py   (or pytest tests/)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doorboard.engine import evaluation as ev
from doorboard.engine import feedback, text as txt
from doorboard.engine.bands import age_band_for, vital_scale
from doorboard.engine.models import Patient, Vitals
from doorboard.engine.pipeline import (
    SafetyConfirmRequired,
    apply_override,
    default_room,
    load_patients,
    peds_band_proof,
    rank_room,
    simulate_surge,
    surge_mix_report,
)
from doorboard.engine.scoring import display_acuity, score_patient


def test_age_bands_are_stratified():
    assert age_band_for(0) == "infant"
    assert age_band_for(2) == "toddler"
    assert age_band_for(4) == "preschool"
    assert age_band_for(10) == "school"
    assert age_band_for(16) == "adolescent"
    assert age_band_for(40) == "adult"
    assert age_band_for(80) == "geriatric"


def test_normal_adolescent_hr_is_not_flagged():
    # the bug from the first cut: one 0-17 band flagged HR 64 in a 16 year old
    v = Vitals(hr=64, spo2=98, sbp=110, temp_c=37.0, rr=16)
    assert vital_scale(v, "adolescent")["sub"]["hr"] == 0
    # and the same HR in an infant is very much not normal
    assert vital_scale(v, "infant")["sub"]["hr"] >= 2


def test_infant_and_teen_do_not_share_thresholds():
    v = Vitals(hr=150, spo2=98, sbp=95, temp_c=37.0, rr=30)
    assert vital_scale(v, "infant")["sub"]["hr"] == 0
    assert vital_scale(v, "adolescent")["sub"]["hr"] >= 2


def test_acuity_never_saturates():
    assert display_acuity(1000) < 100.0
    assert display_acuity(300) > display_acuity(200) > display_acuity(100)


def test_arrest_outranks_everyone():
    ps = load_patients()
    rows = rank_room(ps, default_room(False))
    assert rows[0].patient_id == "P01"
    assert rows[0].raw_points > rows[1].raw_points  # no tie at the top
    assert rows[0].space == "Resus"                 # and a space to go to


def test_negation_is_respected():
    f = txt.complaint_features("No chest pain, just anxious", [])
    assert "chest pain" not in f["terms"]
    assert "chest pain" in f["negated"]
    assert f["points"] < 10


def test_still_talking_does_not_escalate():
    assert txt.look_features("Still talking")["points"] == 0
    assert txt.look_features("Quiet, pale")["points"] > 0


def test_hedged_complaint_still_scores_the_risk():
    f = txt.complaint_features("Not sure - dizzy or chest tightness", [])
    assert f["ambiguous"] is True
    assert f["points"] >= 20


def test_no_under_triage_on_reference_set():
    ps = load_patients()
    r = ev.triage_rates(ps)
    assert r["under_triage_pct"] == 0.0
    assert r["critical_missed"] == 0
    assert r["critical_sensitivity"] == 100.0


def test_surge_holds_the_line_on_under_triage():
    ps = load_patients()
    r = ev.triage_rates(ps, shift_mode="surge")
    assert r["under_triage_pct"] == 0.0
    assert r["over_triage_pct"] >= 0.0


def test_escalation_is_stronger_in_surge():
    ps = [p for p in load_patients() if p.id == "P15"][0]
    quiet = score_patient(ps, shift_mode="quiet")
    surge = score_patient(ps, shift_mode="surge")
    assert surge["raw_points"] > quiet["raw_points"]


def test_missing_vitals_never_becomes_can_wait():
    ps = load_patients()
    for p in ps:
        sc = score_patient(p)
        if "SpO2" in sc["missing_vitals"] and p.esi >= 3:
            assert sc["refuse_can_wait"] is True


def test_surge_is_a_distribution_shift_not_clones():
    ps = load_patients()
    sp, _ = simulate_surge(ps, 3.0)
    assert len(sp) == 3 * len(ps)
    rep = surge_mix_report(ps, sp)
    new = [p for p in sp if "surge_arrival" in p.tags]
    assert len({p.complaint for p in new}) > 5
    assert not any("surge 1" in p.name for p in new)
    assert rep["ems_share_new_pct"] > 0


def test_safety_hold_override_needs_confirmation():
    ps = load_patients()
    rows = rank_room(ps, default_room(False))
    held = [r for r in rows if r.refuse_can_wait]
    assert held, "expected at least one safety hold in the demo set"
    pid = held[0].patient_id
    try:
        apply_override(rows, pid, "looks ok", reason_code="looks_well", patients=ps)
        raise AssertionError("should have refused without confirmation")
    except SafetyConfirmRequired:
        pass
    _, _, event = apply_override(
        rows, pid, "reassessed, SpO2 98", reason_code="looks_well",
        patients=ps, confirmed_safety=True,
    )
    assert event.severity == "safety_critical"
    assert event.safety_confirmed is True
    assert event.before and event.after
    assert event.lawful_basis


def test_override_up_reorders_in_the_same_call():
    ps = load_patients()
    rows = rank_room(ps, default_room(False))
    newp, _, event = apply_override(
        rows, "P10", "family worried", reason_code="family_report", patients=ps
    )
    target = [p for p in newp if p.id == "P10"][0]
    assert "charge_priority" in target.tags
    assert event.severity == "routine"
    assert rank_room(newp, default_room(False))[0].patient_id == "P10"


def test_deterioration_is_computed_not_tagged():
    ps = {p.id: p for p in load_patients()}
    d = score_patient(ps["P28"])["deterioration"]
    assert d["method"] == "trend"
    assert d["series_len"] >= 2


def test_peds_band_proof_shows_the_fix():
    fixed = [r for r in peds_band_proof(load_patients()) if r["fixed"]]
    assert fixed, "the adolescent false positive should be demonstrably fixed"


def test_feedback_needs_evidence_before_proposing():
    assert feedback.proposals([], {}) == []


def test_latency_is_reported():
    ps = load_patients()
    lat = ev.latency(ps, default_room(False))
    assert lat["rank_ms_median"] > 0
    assert lat["score_ms_per_patient"] > 0


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"pass  {fn.__name__}")
        except Exception as exc:
            bad += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
