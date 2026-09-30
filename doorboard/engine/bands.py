"""Age bands and published-scale vital scoring.

Sources the numbers instead of inventing them:

  Adults 18-64   NEWS2 (Royal College of Physicians, National Early Warning
                 Score 2, 2017). Per-parameter subscore 0-3.
  Geriatric 65+  NEWS2 with two documented deviations (see GERI_NOTE).
  Under 18       Age-stratified normal ranges from APLS / PALS, scored in a
                 PEWS-style 0-3 band. Five strata, not one "pediatric" bucket.

Every deviation from the published scale is named in DEVIATIONS so a clinical
reviewer can see what we changed and why.
"""

from __future__ import annotations


BANDS = ("infant", "toddler", "preschool", "school", "adolescent", "adult", "geriatric")

PEDS_BANDS = ("infant", "toddler", "preschool", "school", "adolescent")


def age_band_for(age: int) -> str:
    if age < 1:
        return "infant"
    if age <= 2:
        return "toddler"
    if age <= 5:
        return "preschool"
    if age <= 12:
        return "school"
    if age < 18:
        return "adolescent"
    if age >= 65:
        return "geriatric"
    return "adult"


def is_pediatric(band: str) -> bool:
    return band in PEDS_BANDS


# APLS normal ranges: (hr_low, hr_high, rr_low, rr_high)
PEDS_NORMAL = {
    "infant": (110, 160, 30, 40),
    "toddler": (100, 150, 25, 35),
    "preschool": (95, 140, 25, 30),
    "school": (80, 120, 20, 25),
    "adolescent": (60, 100, 15, 20),
}

# PALS hypotension floor by band (5th centile SBP)
PEDS_SBP_FLOOR = {
    "infant": 70,
    "toddler": 72,
    "preschool": 76,
    "school": 84,
    "adolescent": 90,
}

GERI_NOTE = (
    "Geriatric deviations from NEWS2: (1) fever threshold lowered by 0.5C because "
    "older adults under-mount temperature response to sepsis; (2) SpO2 warn band "
    "shifted up 2 points. Both raise sensitivity, never lower it."
)

DEVIATIONS = [
    "Adult vitals = NEWS2 (RCP 2017) unmodified.",
    GERI_NOTE,
    "Pediatric vitals = APLS/PALS normal ranges scored PEWS-style 0-3. "
    "Five age strata (infant/toddler/preschool/school/adolescent).",
    "Missing parameters score 0 clinical points. They lower the information "
    "score instead, which is what drives the escalate rule. We do not impute.",
    "NEWS2 total is multiplied by 5.0 to sit on the same points scale as "
    "complaint, look and arrival signals. The multiplier is a scaling choice, "
    "not a clinical claim.",
]

NEWS2_SCALE = 5.0


def _news2_rr(rr: float) -> int:
    if rr <= 8:
        return 3
    if rr <= 11:
        return 1
    if rr <= 20:
        return 0
    if rr <= 24:
        return 2
    return 3


def _news2_spo2(spo2: float, geriatric: bool) -> int:
    shift = 2 if geriatric else 0
    if spo2 <= 91 + shift:
        return 3
    if spo2 <= 93 + shift:
        return 2
    if spo2 <= 95 + shift:
        return 1
    return 0


def _news2_sbp(sbp: float) -> int:
    if sbp <= 90:
        return 3
    if sbp <= 100:
        return 2
    if sbp <= 110:
        return 1
    if sbp >= 220:
        return 3
    return 0


def _news2_hr(hr: float) -> int:
    if hr <= 40:
        return 3
    if hr <= 50:
        return 1
    if hr <= 90:
        return 0
    if hr <= 110:
        return 1
    if hr <= 130:
        return 2
    return 3


def _news2_temp(temp: float, geriatric: bool) -> int:
    shift = 0.5 if geriatric else 0.0
    if temp <= 35.0:
        return 3
    if temp <= 36.0:
        return 1
    if temp <= 38.0 - shift:
        return 0
    if temp <= 39.0 - shift:
        return 1
    return 2


def news2(vit, *, geriatric: bool = False, alert: bool = True) -> dict:
    """NEWS2 subscores. Missing parameters are skipped, not imputed."""
    sub = {}
    if vit.rr is not None:
        sub["rr"] = _news2_rr(vit.rr)
    if vit.spo2 is not None:
        sub["spo2"] = _news2_spo2(vit.spo2, geriatric)
    if vit.sbp is not None:
        sub["sbp"] = _news2_sbp(vit.sbp)
    if vit.hr is not None:
        sub["hr"] = _news2_hr(vit.hr)
    if vit.temp_c is not None:
        sub["temp"] = _news2_temp(vit.temp_c, geriatric)
    if not alert:
        sub["consciousness"] = 3
    total = sum(sub.values())
    return {
        "scale": "NEWS2" + (" (geriatric-adjusted)" if geriatric else ""),
        "sub": sub,
        "total": total,
        "any_three": any(v >= 3 for v in sub.values()),
        "measured": len(sub),
    }


def _band_pts(val: float, low: float, high: float, wide: float) -> int:
    """0 inside normal, 1 just outside, 2 further, 3 far outside."""
    if low <= val <= high:
        return 0
    if val < low:
        gap = low - val
        span = low - (low - wide)
    else:
        gap = val - high
        span = wide
    if gap <= span * 0.34:
        return 1
    if gap <= span * 0.7:
        return 2
    return 3


def pews(vit, band: str, *, alert: bool = True) -> dict:
    """PEWS-style 0-3 per parameter against APLS ranges for this age stratum."""
    lo_hr, hi_hr, lo_rr, hi_rr = PEDS_NORMAL[band]
    floor = PEDS_SBP_FLOOR[band]
    sub = {}
    if vit.hr is not None:
        sub["hr"] = _band_pts(vit.hr, lo_hr, hi_hr, max(25.0, hi_hr * 0.25))
    if vit.rr is not None:
        sub["rr"] = _band_pts(vit.rr, lo_rr, hi_rr, max(8.0, hi_rr * 0.4))
    if vit.spo2 is not None:
        if vit.spo2 <= 90:
            sub["spo2"] = 3
        elif vit.spo2 <= 93:
            sub["spo2"] = 2
        elif vit.spo2 <= 95:
            sub["spo2"] = 1
        else:
            sub["spo2"] = 0
    if vit.sbp is not None:
        if vit.sbp < floor:
            sub["sbp"] = 3
        elif vit.sbp < floor + 10:
            sub["sbp"] = 2
        else:
            sub["sbp"] = 0
    if vit.temp_c is not None:
        # infants under 3 months and any child with fever + poor perfusion run hot
        if vit.temp_c >= 39.5 or vit.temp_c < 36.0:
            sub["temp"] = 2
        elif vit.temp_c >= 38.5:
            sub["temp"] = 1 if band != "infant" else 2
        else:
            sub["temp"] = 0
    if not alert:
        sub["consciousness"] = 3
    total = sum(sub.values())
    return {
        "scale": f"PEWS/APLS ({band})",
        "sub": sub,
        "total": total,
        "any_three": any(v >= 3 for v in sub.values()),
        "measured": len(sub),
        "normal_hr": (lo_hr, hi_hr),
        "normal_rr": (lo_rr, hi_rr),
        "sbp_floor": floor,
    }


def vital_scale(vit, band: str, *, alert: bool = True) -> dict:
    """Route to the right published scale for this band."""
    if is_pediatric(band):
        return pews(vit, band, alert=alert)
    return news2(vit, geriatric=(band == "geriatric"), alert=alert)
