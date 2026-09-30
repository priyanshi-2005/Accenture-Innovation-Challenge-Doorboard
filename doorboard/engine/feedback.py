"""Overrides go somewhere.

The audit log used to be write-only: events were captured and nothing ever read
them. This turns them into a proposal queue.

Deliberately not auto-applied. A triage tool that silently retunes itself from
nurse behaviour will drift toward whatever the busiest shift did, and no one
will be able to say why the numbers moved. So: DoorBoard counts the pattern,
proposes a change, and a named clinical owner accepts or rejects it. That
approval is itself an audit event.
"""

from __future__ import annotations

from collections import Counter

from .models import AuditEvent


MIN_EVIDENCE = 3  # don't propose anything off one or two overrides


def signal_counts(events: list[AuditEvent], rows_by_id: dict) -> dict:
    """Which signals were present on patients the nurse overrode."""
    up = Counter()
    down = Counter()
    for e in events:
        if e.action != "override":
            continue
        row = rows_by_id.get(e.patient_id)
        if row is None:
            continue
        sigs = set(row.tags or [])
        if row.escalate_bias:
            sigs.add("escalate_bias")
        if row.refuse_can_wait:
            sigs.add("refuse_can_wait")
        if row.ml_lifted:
            sigs.add("ml_lifted")
        if row.information_label:
            sigs.add(f"info:{row.information_label}")
        bucket = up if e.reason_code in UP_CODES else down
        for s in sigs:
            bucket[s] += 1
    return {"moved_up": dict(up), "moved_down": dict(down)}


UP_CODES = {"clinical_concern", "deterioration_seen", "pathway_clock", "family_report"}
DOWN_CODES = {"looks_well", "already_seen", "duplicate", "left_department"}

REASON_CODES = {
    "clinical_concern": ("Move up: I am worried about this patient", "up", False),
    "deterioration_seen": ("Move up: I saw them change", "up", False),
    "pathway_clock": ("Move up: pathway clock", "up", False),
    "family_report": ("Move up: family or carer concern", "up", False),
    "looks_well": ("Move down: reassessed, looks well", "down", True),
    "already_seen": ("Move down: already assessed by a clinician", "down", True),
    "duplicate": ("Move down: duplicate entry", "down", False),
    "left_department": ("Move down: left the department", "down", False),
    "space_constraint": ("Change destination: space not usable", "side", False),
    "isolation_required": ("Change destination: infection control", "side", False),
}

# codes that can clear a safety hold, and therefore need explicit confirmation
SAFETY_CODES = {"looks_well", "already_seen"}


def proposals(events: list[AuditEvent], rows_by_id: dict) -> list[dict]:
    """Weight changes worth a human looking at. Never applied automatically."""
    counts = signal_counts(events, rows_by_id)
    out = []
    for sig, n in sorted(counts["moved_down"].items(), key=lambda x: -x[1]):
        if n < MIN_EVIDENCE:
            continue
        out.append({
            "signal": sig,
            "direction": "reduce",
            "evidence": n,
            "text": f"Nurses moved {n} patients down who carried '{sig}'. "
                    f"Candidate: reduce its weight or narrow when it fires.",
            "status": "awaiting clinical review",
        })
    for sig, n in sorted(counts["moved_up"].items(), key=lambda x: -x[1]):
        if n < MIN_EVIDENCE:
            continue
        out.append({
            "signal": sig,
            "direction": "increase",
            "evidence": n,
            "text": f"Nurses moved {n} patients up who carried '{sig}'. "
                    f"Candidate: the engine is under-weighting it.",
            "status": "awaiting clinical review",
        })
    return out


def safety_override_count(events: list[AuditEvent]) -> int:
    return sum(1 for e in events if e.severity == "safety_critical")


def override_rate(events: list[AuditEvent], n_decisions: int) -> float:
    n = sum(1 for e in events if e.action == "override")
    if n_decisions <= 0:
        return 0.0
    return round(100.0 * n / n_decisions, 1)
