from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


AgeBand = str  # infant | toddler | preschool | school | adolescent | adult | geriatric
ShiftMode = str  # "quiet" | "normal" | "surge"
ProfileId = str  # "urban" | "district" | "rural"


@dataclass
class Vitals:
    hr: Optional[float] = None
    spo2: Optional[float] = None
    sbp: Optional[float] = None
    temp_c: Optional[float] = None
    rr: Optional[float] = None

    def missing_keys(self) -> list[str]:
        keys = []
        if self.hr is None:
            keys.append("HR")
        if self.spo2 is None:
            keys.append("SpO2")
        if self.sbp is None:
            keys.append("BP")
        if self.temp_c is None:
            keys.append("Temp")
        if self.rr is None:
            keys.append("RR")
        return keys

    def completeness(self) -> float:
        vals = [self.hr, self.spo2, self.sbp, self.temp_c, self.rr]
        return sum(v is not None for v in vals) / len(vals)


@dataclass
class Patient:
    id: str
    name: str
    age: int
    age_band: AgeBand
    complaint: str
    look: str
    arrival: str
    esi: int
    vitals: Vitals
    wait_min: int = 0
    chair: str = ""
    has_prior_record: bool = False
    history_note: str = ""
    tags: list[str] = field(default_factory=list)
    consent_doorboard: bool = True
    # Extra real-world complexities (beyond Round 2 brief)
    language_barrier: bool = False
    bounceback_72h: bool = False
    isolation_needed: bool = False
    pregnant: bool = False
    intoxicated: bool = False
    pathway: str = ""  # "" | "stroke" | "stemi" | "sepsis"
    pathway_min: int = 0  # minutes since pathway clock started
    # serial vitals for the deterioration trend, oldest first
    # [{"t_minus_min": 30, "vitals": {...}}]
    vitals_series: list[dict] = field(default_factory=list)
    # team consensus target tier for the evaluation harness, not ground truth
    reference_priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HospitalProfile:
    """Same engine, different capacity / staffing / maturity (scalability)."""

    id: ProfileId
    label: str
    visits_per_day: int
    resus_open: int
    bay_open: int
    chair_open: int
    nurses_on_floor: int
    has_ehr_feed: bool
    has_bed_board: bool
    specialty_note: str
    has_trauma: bool = False
    has_peds: bool = True
    has_stroke_bay: bool = True
    stroke_policy: str = "treat_on_site"  # treat_on_site | transfer_out
    maturity: str = "high"  # high | mid | low — integration maturity

    def room(self, surge: bool = False) -> "RoomState":
        isolation = 2 if self.id == "urban" else (1 if self.id == "district" else 0)
        boarders = 2 if self.id == "urban" else (1 if self.id == "district" else 1)
        if surge:
            # a real ED clears a resus space for a crashing patient even in surge
            return RoomState(
                resus_open=max(1, self.resus_open) if self.resus_open else 0,
                bay_open=max(1, self.bay_open // 3),
                chair_open=max(2, self.chair_open // 3),
                isolation_open=max(0, isolation // 2),
                boarders_blocking=boarders + 1,
                nurses_on_floor=max(2, self.nurses_on_floor - 2),
                surge=True,
                profile_id=self.id,
            )
        return RoomState(
            resus_open=self.resus_open,
            bay_open=self.bay_open,
            chair_open=self.chair_open,
            isolation_open=isolation,
            boarders_blocking=boarders,
            nurses_on_floor=self.nurses_on_floor,
            surge=False,
            profile_id=self.id,
        )


PROFILES: dict[str, HospitalProfile] = {
    "urban": HospitalProfile(
        id="urban",
        label="Urban trauma center (Kaveri General)",
        visits_per_day=420,
        # 2 resus bays, not 1: a trauma+stroke-capable center at this volume
        # (roughly 1 resus bay per 150-200 daily high-acuity visits is a
        # reasonable planning ratio for a Level-1-equivalent center) would
        # realistically run more than a single bay. 1 bay understated
        # capacity and manufactured more "Hold for resus" queueing in the
        # demo than a real unit this size would show.
        resus_open=2,
        bay_open=3,
        chair_open=14,
        nurses_on_floor=8,
        has_ehr_feed=True,
        has_bed_board=True,
        specialty_note="Trauma + peds capable · stroke bay · FHIR bed + roster feed",
        has_trauma=True,
        has_peds=True,
        has_stroke_bay=True,
        stroke_policy="treat_on_site",
        maturity="high",
    ),
    "district": HospitalProfile(
        id="district",
        label="District hospital ED (Bengaluru peri-urban)",
        visits_per_day=220,
        resus_open=1,
        bay_open=2,
        chair_open=10,
        nurses_on_floor=5,
        has_ehr_feed=True,
        has_bed_board=False,
        specialty_note="Peds capable · no trauma bay · stroke treat then transfer if needed · HL7 ADT + CSV beds",
        has_trauma=False,
        has_peds=True,
        has_stroke_bay=False,
        stroke_policy="stabilize_transfer",
        maturity="mid",
    ),
    "rural": HospitalProfile(
        id="rural",
        label="Rural community ED",
        visits_per_day=110,
        resus_open=0,
        bay_open=1,
        chair_open=6,
        nurses_on_floor=3,
        has_ehr_feed=False,
        has_bed_board=False,
        specialty_note="No trauma/peds specialty on site · stroke = transfer out · paper roster · CSV beds",
        has_trauma=False,
        has_peds=False,
        has_stroke_bay=False,
        stroke_policy="transfer_out",
        maturity="low",
    ),
}


@dataclass
class RoomState:
    resus_open: int = 0
    bay_open: int = 0
    chair_open: int = 12
    isolation_open: int = 0
    boarders_blocking: int = 0
    nurses_on_floor: int = 8
    surge: bool = False
    clock: str = "19:40"
    profile_id: str = "urban"
    shift_mode: ShiftMode = "normal"
    staffing_shock: bool = False


@dataclass
class RankedRow:
    order: int
    patient_id: str
    name: str
    age: int
    age_band: str
    chair: str
    complaint: str
    look: str
    arrival: str
    vitals_text: str
    esi: int
    space: str
    why: str
    acuity: float
    confidence: float
    confidence_label: str
    refuse_can_wait: bool
    escalate_bias: bool
    reassess_flag: str
    flag: str
    has_prior_record: bool
    tags: list[str] = field(default_factory=list)
    history_boost: float = 0.0
    adult_only_acuity: float = 0.0
    input_weights: dict[str, float] = field(default_factory=dict)
    consent_doorboard: bool = True
    lwbs_risk: bool = False
    isolation_needed: bool = False
    pathway: str = ""
    pathway_min: int = 0
    language_barrier: bool = False
    bounceback_72h: bool = False
    pregnant: bool = False
    intoxicated: bool = False
    raw_points: float = 0.0
    tier: int = 3
    information: float = 0.0
    information_label: str = ""
    information_reasons: list[str] = field(default_factory=list)
    vital_scale: str = ""
    vital_subscores: dict[str, int] = field(default_factory=dict)
    vital_total: int = 0
    single_param_red: bool = False
    model_prob: float | None = None
    model_source: str = ""
    ml_lifted: bool = False
    deterioration: dict[str, Any] = field(default_factory=dict)
    negated_terms: list[str] = field(default_factory=list)
    raw_parts: dict[str, float] = field(default_factory=dict)
    band_group: str = "adult"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEvent:
    ts: str
    actor: str
    action: str
    patient_id: str
    detail: str
    before: str = ""
    after: str = ""
    legal_note: str = ""  # what DPDP/clinical governance requires on override
    reason_code: str = ""
    severity: str = "routine"  # routine | safety_critical
    actor_role: str = "charge_nurse"
    safety_confirmed: bool = False
    lawful_basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
