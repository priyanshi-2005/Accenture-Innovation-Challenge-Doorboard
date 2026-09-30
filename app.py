"""
DoorBoard — Round 2 prototype (nurse-first UI)
Team Sap_accenture · PatientTriage.ai · Accenture Innovation Challenge 2026

Run: python3 -m streamlit run app.py
"""

from __future__ import annotations

import copy
import html
import json
import time
from contextlib import nullcontext
from datetime import timedelta

import streamlit as st

from doorboard.engine.pipeline import (
    OVERRIDE_LEGAL_NOTE,
    age_aware_vs_adult_only,
    apply_chart_update,
    apply_override,
    apply_staffing_shock,
    asymmetric_cost_statement,
    consume_open_space,
    explain_bits,
    fever_twin_proof,
    get_profile,
    group_eye_rows,
    load_meta,
    load_patients,
    mark_vitals_worse,
    mock_integrations,
    next_patient_id,
    patient_from_intake,
    patients_over_safe_wait,
    rank_room,
    room_for,
    slot_inventory,
    space_is_open,
    space_kind,
    tick_waits,
    simulate_surge,
    surge_mix_report,
    peds_band_proof,
    scale_provenance,
    override_options,
    SafetyConfirmRequired,
    eye_reason,
)
from doorboard.engine import evaluation as evaluation
from doorboard.engine import feedback as fb
from doorboard.engine import ml as ml
from doorboard.engine.deterioration import waiting_room_watch

st.set_page_config(
    page_title="DoorBoard · Door list",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(show_spinner=False)
def replay_numbers() -> dict:
    """Computed, not typed in. See evaluation.replay()."""
    ps = load_patients()
    return evaluation.replay(ps, room_for("urban", surge=False))


@st.cache_data(show_spinner=False)
def eval_numbers() -> dict:
    ps = load_patients()
    room = room_for("urban", surge=False)
    return {
        "normal": evaluation.triage_rates(ps, shift_mode="normal"),
        "surge": evaluation.triage_rates(ps, shift_mode="surge"),
        "matrix": evaluation.confusion_matrix(ps),
        "sweep": evaluation.threshold_sweep(ps),
        "latency": evaluation.latency(ps, room),
        "replay": evaluation.replay(ps, room),
    }


def init() -> None:
    if "patients" in st.session_state:
        return
    st.session_state.patients = load_patients()
    st.session_state.meta = load_meta()
    st.session_state.profile_id = "urban"
    st.session_state.shift_mode = "normal"
    st.session_state.room = room_for("urban", surge=False, shift_mode="normal")
    st.session_state.sealed = False
    st.session_state.audit = []
    st.session_state.audit_events = []
    st.session_state.pending_safety_override = None
    st.session_state.sealed_snapshot = None
    st.session_state.show_age_compare = False
    st.session_state.show_privacy = False
    st.session_state.show_weights = False
    st.session_state.show_decision = False
    st.session_state.show_assumptions = False
    st.session_state.redact_phi = False
    st.session_state.role = "charge_nurse"
    st.session_state.logged_in = True
    st.session_state.user_name = "Priya Nair"
    st.session_state.user_designation = "Charge nurse · ED"
    st.session_state.user_initials = "PN"
    st.session_state.replay = False
    st.session_state.ui_mode = "floor"
    st.session_state.focus_patient_id = None
    st.session_state.detail_patient_id = None
    st.session_state.show_chart = False
    st.session_state.show_add_form = False
    st.session_state.last_override = None
    st.session_state.off_board = []
    st.session_state.slot_fills = []
    st.session_state.pending_events = []
    st.session_state.change_patient_id = None
    st.session_state.access_log = []
    st.session_state.session_started_ts = time.time()
    st.session_state.last_auto_tick_wall = time.time()
    st.session_state.auto_watch_note = ""
    st.session_state.ehr_degraded_force = False
    st.session_state.wait_ack = []
    st.session_state.banner = (
        "Suggested order for who goes next and where. You decide. Nothing goes in the chart until you confirm."
    )
    st.session_state.override_box = "Walking, talking — keep in chairs"
    rows0 = rank_room(st.session_state.patients, st.session_state.room)
    st.session_state.baseline_order = {r.patient_id: r.order for r in rows0}


init()


DEMO_ACCOUNTS = {
    "charge_nurse": {
        "name": "Priya Nair",
        "designation": "Charge nurse · ED",
        "initials": "PN",
        "role": "charge_nurse",
    },
    "viewer": {
        "name": "Arjun Mehta",
        "designation": "Floor observer · view only",
        "initials": "AM",
        "role": "viewer",
    },
}


def apply_account(account_key: str) -> None:
    acc = DEMO_ACCOUNTS[account_key]
    st.session_state.logged_in = True
    st.session_state.role = acc["role"]
    st.session_state.user_name = acc["name"]
    st.session_state.user_designation = acc["designation"]
    st.session_state.user_initials = acc["initials"]


# Backfill account fields for sessions started before this feature
if "logged_in" not in st.session_state:
    st.session_state.logged_in = True
if "user_name" not in st.session_state:
    apply_account(
        "viewer"
        if st.session_state.get("role") == "viewer"
        else "charge_nurse"
    )
for _k, _default in (
    ("off_board", []),
    ("slot_fills", []),
    ("pending_events", []),
    ("change_patient_id", None),
    ("access_log", []),
    ("auto_watch_note", ""),
    ("ehr_degraded_force", False),
    ("wait_ack", []),
):
    if _k not in st.session_state:
        st.session_state[_k] = _default
if "session_started_ts" not in st.session_state:
    st.session_state.session_started_ts = time.time()
if "last_auto_tick_wall" not in st.session_state:
    st.session_state.last_auto_tick_wall = time.time()


def floor_col_widths(*, form_open: bool) -> list[float]:
    """Default Floor weights; drag the column dividers to override (browser-local)."""
    if form_open:
        return [1.05, 1.7, 1.2]
    return [1.05, 1.9, 1.05]


def inject_floor_col_resizer() -> None:
    """Drag the partition between Next up / list / desk to resize. No buttons."""
    st.html(
        """
<style>
  .db-floor-cols { position: relative !important; }
  .db-col-handle {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 14px;
    margin-left: -7px;
    cursor: col-resize;
    z-index: 40;
    background: transparent;
    touch-action: none;
    user-select: none;
  }
  .db-col-handle::after {
    content: "";
    position: absolute;
    left: 6px;
    top: 8%;
    bottom: 8%;
    width: 3px;
    border-radius: 2px;
    background: #dadce0;
    transition: background 0.12s ease, box-shadow 0.12s ease;
  }
  .db-col-handle:hover::after,
  .db-col-handle.dragging::after {
    background: #174ea6;
    box-shadow: 0 0 0 1px rgba(23,78,166,0.25);
  }
  .db-col-handle:hover,
  .db-col-handle.dragging { background: rgba(23, 78, 166, 0.06); }
</style>
<script>
(function () {
  const KEY = "doorboard.floorColFlex.v1";
  const MIN = 0.55;

  function isCol(el) {
    var id = el.getAttribute("data-testid") || "";
    return id === "stColumn" || id === "column";
  }

  function colsOf(block) {
    return Array.from(block.children).filter(isCol);
  }

  function findFloor() {
    var blocks = document.querySelectorAll('[data-testid="stHorizontalBlock"]');
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      var cols = colsOf(b);
      if (cols.length !== 3) continue;
      var t = b.innerText || "";
      if (t.indexOf("Next up") === -1) continue;
      if (t.indexOf("Your desk") === -1 && t.indexOf("Waiting list") === -1) continue;
      return { block: b, cols: cols };
    }
    // Fallback: largest 3-column row on the Floor
    var best = null, bestArea = 0;
    for (var j = 0; j < blocks.length; j++) {
      var bb = blocks[j];
      var cc = colsOf(bb);
      if (cc.length !== 3) continue;
      var r = bb.getBoundingClientRect();
      var area = r.width * r.height;
      if (area > bestArea) { bestArea = area; best = { block: bb, cols: cc }; }
    }
    return best;
  }

  function parseFlex(el) {
    var g = parseFloat(getComputedStyle(el).flexGrow);
    return isFinite(g) && g > 0 ? g : 1;
  }

  function applyFlex(cols, weights) {
    for (var i = 0; i < cols.length; i++) {
      var w = Math.max(MIN, weights[i]);
      cols[i].style.flexGrow = String(w);
      cols[i].style.flexShrink = "1";
      cols[i].style.flexBasis = "0px";
      cols[i].style.width = "auto";
      cols[i].style.minWidth = "0px";
      cols[i].style.maxWidth = "100%";
    }
  }

  function loadSaved() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return null;
      var w = JSON.parse(raw);
      if (Array.isArray(w) && w.length === 3) return w.map(Number);
    } catch (e) {}
    return null;
  }

  function save(weights) {
    try { localStorage.setItem(KEY, JSON.stringify(weights)); } catch (e) {}
  }

  function placeHandles(block, cols) {
    var handles = block.querySelectorAll(".db-col-handle");
    var br = block.getBoundingClientRect();
    for (var i = 0; i < handles.length; i++) {
      var left = cols[i].getBoundingClientRect();
      var right = cols[i + 1].getBoundingClientRect();
      var mid = (left.right + right.left) / 2 - br.left;
      handles[i].style.left = mid + "px";
    }
  }

  function wire(block, cols) {
    if (!block._dbDefaults) {
      block._dbDefaults = cols.map(parseFlex);
    }
    var saved = loadSaved();
    applyFlex(cols, saved || block._dbDefaults);
    block.classList.add("db-floor-cols");
    block.style.position = "relative";

    if (block.dataset.dbResize === "1") {
      placeHandles(block, cols);
      return;
    }
    block.dataset.dbResize = "1";

    for (var i = 0; i < 2; i++) {
      (function (idx) {
        var handle = document.createElement("div");
        handle.className = "db-col-handle";
        handle.title = "Drag to resize · double-click resets";
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-orientation", "vertical");
        block.appendChild(handle);

        handle.addEventListener("mousedown", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          handle.classList.add("dragging");
          var startX = ev.clientX;
          var startW = cols.map(parseFlex);
          var pair = startW[idx] + startW[idx + 1];
          var blockW = block.getBoundingClientRect().width || 1;

          function onMove(e) {
            var delta = ((e.clientX - startX) / blockW) * pair;
            var a = startW[idx] + delta;
            var b = pair - a;
            if (a < MIN) { a = MIN; b = pair - MIN; }
            if (b < MIN) { b = MIN; a = pair - MIN; }
            var next = startW.slice();
            next[idx] = a;
            next[idx + 1] = b;
            applyFlex(cols, next);
            placeHandles(block, cols);
          }
          function onUp() {
            handle.classList.remove("dragging");
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            save(cols.map(parseFlex));
          }
          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        });

        handle.addEventListener("dblclick", function (ev) {
          ev.preventDefault();
          try { localStorage.removeItem(KEY); } catch (e) {}
          applyFlex(cols, block._dbDefaults || [1.05, 1.9, 1.05]);
          placeHandles(block, cols);
        });
      })(i);
    }
    placeHandles(block, cols);
  }

  function tick() {
    var found = findFloor();
    if (found) wire(found.block, found.cols);
  }

  tick();
  if (!window.__dbFloorResizeObs) {
    window.__dbFloorResizeObs = new MutationObserver(function () { tick(); });
    window.__dbFloorResizeObs.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", function () { tick(); });
    setInterval(tick, 900);
  }
})();
</script>
        """,
        unsafe_allow_javascript=True,
    )


def log_access(action: str, detail: str, allowed: bool = True) -> None:
    st.session_state.access_log = (
        list(st.session_state.get("access_log") or [])
        + [
            {
                "ts": time.strftime("%H:%M:%S"),
                "actor": st.session_state.get("user_name", "user"),
                "role": st.session_state.get("role", ""),
                "action": action,
                "detail": detail,
                "allowed": allowed,
            }
        ]
    )[-40:]


def run_auto_queue_watch() -> None:
    """Advance waits on a wall clock — quiet enough for Floor work / filming."""
    now = time.time()
    last = float(st.session_state.get("last_auto_tick_wall") or now)
    # 2 minutes between ticks; small wait step so alerts don't avalanche
    if now - last < 120:
        return
    before = set(patients_over_safe_wait(st.session_state.patients))
    st.session_state.patients = tick_waits(st.session_state.patients, 2)
    st.session_state.last_auto_tick_wall = now
    off = set(st.session_state.get("off_board") or [])
    acked = set(st.session_state.get("wait_ack") or [])
    pending_ids = {e.get("id") for e in st.session_state.get("pending_events") or []}
    newly = [
        pid
        for pid in patients_over_safe_wait(st.session_state.patients)
        if pid not in before
        and pid not in off
        and pid not in acked
        and pid not in pending_ids
    ]
    # One alert at a time — worst wait first (nurse can clear, then next)
    if newly:
        pts = {p.id: p for p in st.session_state.patients}
        newly.sort(key=lambda i: -(pts[i].wait_min if i in pts else 0))
        pid = newly[0]
        p = pts.get(pid)
        detail = (
            f"Wait {p.wait_min} min past ESI {p.esi} safe ceiling"
            if p
            else "Past safe wait"
        )
        add_floor_event(pid, "wait", "Wait past safe ceiling", detail)
        note = (
            f"Auto queue-watch · +2 min · {time.strftime('%H:%M:%S')} · "
            f"alert {_short_name(p.name) if p else pid}"
        )
    else:
        note = f"Auto queue-watch · +2 min · {time.strftime('%H:%M:%S')}"
    st.session_state.auto_watch_note = note
    st.session_state.sealed = False
    st.session_state.audit.append(
        {
            "ts": time.strftime("%H:%M"),
            "actor": "system",
            "action": "queue_watch_auto",
            "patient_id": "*",
            "detail": note,
            "before": "",
            "after": newly[0] if newly else "",
            "legal_note": "Automatic safe-wait monitor — nurse still confirms moves.",
        }
    )


def mark_taken_elsewhere(row, note: str = "") -> None:
    """Person left via EMS/bay/desk outside DoorBoard — drop from list, no slot consume."""
    off = list(st.session_state.get("off_board") or [])
    if row.patient_id not in off:
        off.append(row.patient_id)
    st.session_state.off_board = off
    dismiss_events_for(row.patient_id)
    acked = list(st.session_state.get("wait_ack") or [])
    if row.patient_id not in acked:
        acked.append(row.patient_id)
    st.session_state.wait_ack = acked
    st.session_state.sealed = False
    st.session_state.change_patient_id = None
    detail = note.strip() or "Taken elsewhere — not via DoorBoard"
    st.session_state.banner = (
        f"{_short_name(row.name)} removed from the door list ({detail}). "
        "No DoorBoard space consumed. Still not in the chart."
    )
    audit(
        "taken_elsewhere",
        row.patient_id,
        detail,
        before=row.space,
        after="off_board",
    )
    log_access("taken_elsewhere", f"{row.patient_id} · {detail}")


def ack_wait_event(patient_id: str) -> None:
    """Nurse saw the wait alert — dismiss without forcing a move."""
    dismiss_events_for(patient_id)
    acked = list(st.session_state.get("wait_ack") or [])
    if patient_id not in acked:
        acked.append(patient_id)
    st.session_state.wait_ack = acked


def pick_focus_patient(rows, baseline) -> str | None:
    """Who should the override picker land on."""
    if not rows:
        return None
    for r in rows:
        if r.refuse_can_wait:
            return r.patient_id
    for r in rows:
        if r.escalate_bias or r.flag == "hold":
            return r.patient_id
    for r in rows:
        was = baseline.get(r.patient_id)
        if was is not None and was > r.order:
            return r.patient_id
    return rows[0].patient_id


def needs_your_eye(r, baseline) -> bool:
    return eye_reason(r, baseline) is not None


def _short_name(name: str) -> str:
    return (name or "").split(",")[0].strip() or name


def _actor() -> str:
    return st.session_state.get("user_name") or "charge_nurse"


def reset_floor_flow() -> None:
    st.session_state.off_board = []
    st.session_state.slot_fills = []
    st.session_state.pending_events = []
    st.session_state.change_patient_id = None
    st.session_state.last_override = None
    st.session_state.wait_ack = []


def dismiss_events_for(patient_id: str) -> None:
    st.session_state.pending_events = [
        e for e in st.session_state.get("pending_events", []) if e.get("id") != patient_id
    ]


def audit(action: str, patient_id: str, detail: str, before: str = "", after: str = "") -> None:
    st.session_state.audit.append(
        {
            "ts": "19:48",
            "actor": _actor(),
            "action": action,
            "patient_id": patient_id,
            "detail": detail,
            "before": before,
            "after": after,
            "legal_note": OVERRIDE_LEGAL_NOTE if action == "override" else "",
        }
    )


def send_patient(row, *, reason: str = "", space_override: str | None = None) -> None:
    """Confirm this one person into the suggested (or chosen) space."""
    if st.session_state.get("role") != "charge_nurse":
        log_access(
            "send_denied",
            f"View-only blocked send for {row.patient_id}",
            allowed=False,
        )
        st.session_state.banner = (
            "Blocked — view-only cannot send. Charge nurse must confirm. Logged."
        )
        return
    space = space_override or row.space
    if not space_is_open(space):
        st.session_state.banner = (
            f"{_short_name(row.name)} still waiting — {space_plain(space)}. "
            "Hold or change space."
        )
        return
    kind = space_kind(space)
    room0 = st.session_state.room
    open_n = {
        "resus": room0.resus_open,
        "bay": room0.bay_open,
        "chair": room0.chair_open,
        "isolation": room0.isolation_open,
    }.get(kind, 0)
    if kind != "wait" and open_n <= 0:
        st.session_state.banner = (
            f"No {kind} free for {_short_name(row.name)}. Hold or change space."
        )
        return
    room, kind = consume_open_space(st.session_state.room, space)
    st.session_state.room = room
    fills = list(st.session_state.get("slot_fills", []))
    fills.append(
        {
            "id": row.patient_id,
            "name": row.name,
            "kind": kind,
            "space": space_plain(space),
        }
    )
    st.session_state.slot_fills = fills
    off = list(st.session_state.get("off_board", []))
    if row.patient_id not in off:
        off.append(row.patient_id)
    st.session_state.off_board = off
    dismiss_events_for(row.patient_id)
    st.session_state.sealed = False
    st.session_state.change_patient_id = None
    note = reason.strip() or f"Sent to {space_plain(space)}"
    st.session_state.banner = (
        f"You sent {_short_name(row.name)} to {space_plain(space)}. "
        "This one is confirmed. Still not written to the chart."
    )
    audit("send", row.patient_id, note, before=row.space, after=space_plain(space))
    log_access("send", f"{row.patient_id} → {space_plain(space)}")


def hold_patient(row, reason: str = "Hold in chairs", reason_code: str = "space_constraint",
                 confirmed_safety: bool = False) -> bool:
    """Returns False if a safety hold would be cleared without confirmation."""
    try:
        patients, _, event = apply_override(
            [row], row.patient_id, reason,
            patients=st.session_state.patients,
            reason_code=reason_code,
            confirmed_safety=confirmed_safety,
        )
    except SafetyConfirmRequired:
        st.session_state.pending_safety_override = {
            "patient_id": row.patient_id,
            "name": row.name,
            "reason": reason,
            "reason_code": reason_code,
        }
        st.session_state.banner = (
            f"{_short_name(row.name)} is on a safety hold. Confirm below before "
            "that is cleared, and it will be logged as a safety override."
        )
        return False
    st.session_state.patients = patients
    st.session_state.audit_events.append(event)
    ev = event.to_dict()
    st.session_state.audit.append(ev)
    st.session_state.last_override = {
        "patient_id": row.patient_id,
        "before": ev.get("before", ""),
        "after": ev.get("after", ""),
        "detail": ev.get("detail", ""),
        "legal_note": ev.get("legal_note", ""),
        "reason_code": ev.get("reason_code", ""),
        "severity": ev.get("severity", "routine"),
    }
    st.session_state.pending_safety_override = None
    dismiss_events_for(row.patient_id)
    st.session_state.sealed = False
    st.session_state.change_patient_id = None
    st.session_state.banner = (
        f"{_short_name(row.name)} held in chairs. Reason stays on the board. "
        "Still not in the chart."
    )
    log_access("hold", f"{row.patient_id} · {reason}")
    return True


def move_patient_up(row, reason: str, reason_code: str = "clinical_concern") -> None:
    patients, _, event = apply_override(
        [row], row.patient_id, reason,
        patients=st.session_state.patients,
        reason_code=reason_code,
    )
    st.session_state.patients = patients
    st.session_state.audit_events.append(event)
    ev = event.to_dict()
    st.session_state.audit.append(ev)
    st.session_state.last_override = {
        "patient_id": row.patient_id,
        "before": ev.get("before", ""),
        "after": ev.get("after", ""),
        "detail": ev.get("detail", ""),
        "legal_note": ev.get("legal_note", ""),
        "reason_code": ev.get("reason_code", ""),
        "severity": ev.get("severity", "routine"),
    }
    st.session_state.sealed = False
    st.session_state.change_patient_id = None
    st.session_state.banner = (
        f"{_short_name(row.name)} moved up by you. List re-ranked. Still not in the chart."
    )


def add_floor_event(patient_id: str, kind: str, title: str, detail: str) -> None:
    events = [
        e for e in st.session_state.get("pending_events", []) if e.get("id") != patient_id
    ]
    events.append(
        {"id": patient_id, "kind": kind, "title": title, "detail": detail}
    )
    st.session_state.pending_events = events


def coverage_checks(rows, patients, profile, room, audit, sealed) -> list[tuple[str, bool, str]]:
    """Live Round 2 checklist for Demo mode."""
    n = len(rows)
    n_amb = sum(1 for r in rows if "ambiguous" in (r.tags or []) or r.escalate_bias)
    # fall back if tags unused
    if n_amb == 0:
        n_amb = sum(1 for r in rows if r.confidence_label != "high")
    n_ped = sum(1 for r in rows if r.age_band == "pediatric")
    n_ger = sum(1 for r in rows if r.age_band == "geriatric")
    n_zh = sum(1 for r in rows if not r.has_prior_record)
    has_conf = all(getattr(r, "confidence", None) is not None for r in rows) if rows else False
    has_override = any(ev.get("action") == "override" for ev in audit)
    surged = n >= 80 or bool(getattr(room, "surge", False))
    return [
        (f"{n} patients on list (≥15–20)", n >= 15, "Minimum"),
        ("Ambiguous / unsure cases", n_amb >= 1, "Minimum"),
        (f"Pediatric ({n_ped}) + geriatric ({n_ger})", n_ped >= 1 and n_ger >= 1, "Minimum"),
        (f"Zero-history / first visit ({n_zh})", n_zh >= 1, "Minimum"),
        ("Surge ≈3× available (Busy night)", True, "Minimum"),
        ("Uncertainty read on every row (data-completeness; ML-calibrated where lifted)", has_conf, "Minimum"),
        (
            "Override logged" if has_override else "Override not logged yet — use Change this person",
            has_override,
            "Minimum",
        ),
        (f"Urban / district / rural · ~{profile.visits_per_day}/day", True, "Reference"),
        ("ESI 1–5 + room rank", True, "Reference"),
        ("~50/50 prior history", n_zh >= max(1, n // 3) if n else False, "Reference"),
        ("DPDP · purpose · access log · chart write off", True, "Protection"),
        ("Escalate when unsure · auto queue-watch", True, "Safety"),
        (f"Integration maturity · {getattr(profile, 'maturity', '—')}", True, "Scale"),
        ("Surge currently on the board", surged, "Live"),
        ("List confirmed (still not in chart)", sealed, "Live"),
    ]


def conf_badge(label: str, conf: float, ml_lifted: bool = False, model_prob: float | None = None) -> str:
    """Nurse language: Sure / Somewhat / Unsure.

    The percentage is data completeness, not a statistical confidence — the
    tooltip says so on every badge. When the ML layer actually lifted this
    patient, its real calibrated probability (Brier-scored, isotonic) is
    shown as a second tag instead of being buried in a debug panel.
    """
    words = {"high": "Sure", "medium": "Somewhat", "low": "Unsure"}
    color = {"high": "#137333", "medium": "#b06000", "low": "#c5221f"}.get(label, "#5f6368")
    bg = {"high": "#e6f4ea", "medium": "#fef7e0", "low": "#fce8e6"}.get(label, "#f1f3f4")
    word = words.get(label, label)
    tip = (
        "How much we know about this patient (vitals completeness, history on "
        "file), not a statistical probability of being right."
    )
    badge = (
        f'<span title="{html.escape(tip)}" style="background:{bg};color:{color};font-size:11px;'
        f'font-weight:600;padding:3px 8px;border-radius:12px;">{html.escape(word)} {conf:.0%}</span>'
    )
    if ml_lifted and model_prob is not None:
        mtip = "Calibrated model probability of critical outcome (isotonic-calibrated, Brier 0.134)."
        badge += (
            f' <span title="{html.escape(mtip)}" style="background:#e8eaed;color:#1a73e8;'
            f'font-size:11px;font-weight:600;padding:3px 8px;border-radius:12px;">'
            f'model p={model_prob:.2f}</span>'
        )
    return badge


def next_card_html(r, baseline, redact: bool = False) -> str:
    was = baseline.get(r.patient_id)
    tone = ""
    if r.flag == "hold":
        tone = " hold"
    elif r.flag == "hot" or (was is not None and was > r.order):
        tone = " moved"
    elif r.escalate_bias:
        tone = " careful"
    display_name = "Patient" if redact else r.name
    careful = ' <span class="esc">Being careful</span>' if r.escalate_bias else ""
    bits = explain_bits(r, 3)
    bits_li = "".join(f"<li>{html.escape(b)}</li>" for b in bits)
    return f"""<div class="next-card{tone}">
  <div class="next-top">
    <span class="next-ord">#{r.order}</span>
    <a class="pt-link" href="?chart={html.escape(r.patient_id)}">{html.escape(display_name)}</a>
    <a class="pt-open" href="?change={html.escape(r.patient_id)}">Change</a>
  </div>
  <div class="next-meta">{conf_badge(r.confidence_label, r.confidence, r.ml_lifted, r.model_prob)}{careful}</div>
  <div class="next-space" title="{space_title(space_plain(r.space))}">{html.escape(space_plain(r.space))}</div>
  <div class="next-explain"><span class="ex-label">In 5 seconds</span><ol>{bits_li}</ol></div>
</div>"""


def space_plain(space: str) -> str:
    return (
        space.replace("Hold for resus", "Hold — resus")
        .replace("Waiting · need bay", "Waiting — need bay")
        .replace("Waiting · bay boarded", "Waiting — bay blocked")
        .replace("Waiting · need isolation", "Waiting — need isolation")
        .replace("Waiting · chairs full", "Waiting — chairs full")
        .replace("Bay open", "Bay free")
        .replace("Isolation open", "Isolation free")
    )


SPACE_TITLES = {
    "Resus": "Resus bay assigned now — this patient occupies the space.",
    "Hold — resus": (
        "Resus-tier patient (ESI ≤2), no resus bay free. Queued first for the "
        "next resus bay to open — this is intentional ordering, not a stuck "
        "or forgotten patient."
    ),
    "Bay free": "Monitored bay assigned now.",
    "Waiting — bay blocked": (
        "Bay-tier patient, no bay free because boarders are holding beds. "
        "Queued for the next bay to clear."
    ),
    "Waiting — need bay": "Bay-tier patient, all bays in active use. Queued for the next bay.",
    "Chair": "Chair assigned now — ambulatory, lower-acuity space.",
    "Isolation free": "Isolation room assigned now.",
    "Waiting — need isolation": "Needs isolation cohorting, no isolation room free yet.",
    "Waiting — chairs full": "Chair-tier patient, all chairs in use. Queued for the next chair.",
}


def space_title(space_plain_text: str) -> str:
    """Tooltip text so 'Hold' reads as an ordered queue position, not bay contention."""
    return html.escape(SPACE_TITLES.get(space_plain_text, ""))


def slots_strip_html(slots) -> str:
    """Always-visible capacity strip — do not bury under Next up."""
    bits = []
    for sl in slots:
        names = ", ".join(_short_name(f["name"]) for f in sl["filled"][:2]) or "—"
        empty = " empty" if sl["open"] == 0 and sl["kind"] in ("resus", "bay") else ""
        bits.append(
            f'<div class="slot-card{empty}"><div class="k">{html.escape(sl["label"])}</div>'
            f'<div class="v">{sl["open"]} free</div>'
            f'<div class="f">{html.escape(names)}</div></div>'
        )
    return f'<div class="slot-strip">{"".join(bits)}</div>'


def board_parts(
    rows,
    sealed,
    room,
    banner,
    baseline,
    profile,
    redact: bool = False,
    list_rows=None,
    is_demo: bool = False,
    user_name: str = "",
    user_designation: str = "",
    user_initials: str = "",
):
    """Return (header, left_next_up, middle_list) HTML fragments."""
    table_rows = list_rows if list_rows is not None else rows
    resus = "free" if room.resus_open > 0 else "full"
    bay = f"{room.bay_open} free" if room.bay_open > 0 else "none free"
    status = "Confirmed — still not in the chart" if sealed else "Suggested — not in the chart yet"
    stamp = "ok" if sealed else "wait"
    mode = room.shift_mode
    busy = {
        "surge": '<span class="chip alert">Busy night</span>',
        "quiet": '<span class="chip good">Quiet</span>',
        "normal": '<span class="chip">Steady</span>',
    }.get(mode, "")
    hosp = {
        "urban": "Big hospital",
        "district": "District ED",
        "rural": "Smaller ED",
    }.get(profile.id, profile.label)
    if profile.id == "urban":
        hospital_name = "Kaveri General Hospital"
        hospital_place = "Bengaluru"
    elif profile.id == "district":
        hospital_name = "District Hospital ED"
        hospital_place = "Bengaluru peri-urban"
    else:
        hospital_name = "Community ED"
        hospital_place = "Rural profile"
    floor_on = "" if is_demo else " on"
    demo_on = " on" if is_demo else ""

    body = []
    for r in table_rows:
        was = baseline.get(r.patient_id)
        moved = ""
        extra = ""
        if was and was != r.order and was > r.order:
            moved = f' <span class="was">was #{was}</span>'
            extra = " is-moved"
        if r.flag == "hold":
            extra = (extra + " is-hold").strip()
        elif r.flag == "hot":
            extra = (extra + " is-moved").strip()

        display_name = "Patient" if redact else r.name
        age_tag = ""
        if r.age_band == "pediatric":
            age_tag = f' <span class="age">Child {r.age}y</span>'
        elif r.age_band == "geriatric":
            age_tag = f' <span class="age">Older {r.age}y</span>'

        hist = "been here before" if r.has_prior_record else "first visit"
        if not r.consent_doorboard:
            hist += " · limited consent"

        careful = ' <span class="esc">Being careful</span>' if r.escalate_bias else ""
        watch = ""
        if r.reassess_flag == "wait_exceeded":
            watch = ' <span class="watch">Wait too long</span>'
        elif r.reassess_flag == "vitals_worse":
            watch = ' <span class="watch">Getting worse</span>'
        elif r.reassess_flag == "both":
            watch = ' <span class="watch">Wait + getting worse</span>'

        extras = ""
        if "under_report" in r.tags:
            extras += ' <span class="under">Says fine</span>'
        if "transfer" in r.tags:
            extras += ' <span class="under">Transfer</span>'
        if r.language_barrier:
            extras += ' <span class="under">Language</span>'
        if r.bounceback_72h:
            extras += ' <span class="watch">Back again</span>'
        if r.isolation_needed:
            extras += ' <span class="under">Needs isolation</span>'
        if r.pregnant:
            extras += ' <span class="age">Pregnant</span>'
        if r.intoxicated:
            extras += ' <span class="under">Intoxicated</span>'
        if r.pathway:
            extras += (
                f' <span class="watch">{html.escape(r.pathway.upper())} clock '
                f"{r.pathway_min}m</span>"
            )
        if r.lwbs_risk:
            extras += ' <span class="watch">May leave</span>'
        if any(e.get("id") == r.patient_id for e in st.session_state.get("pending_events", [])):
            extras += ' <span class="watch">Floor event</span>'

        if is_demo:
            body.append(
                f"""<tr class="e{r.esi}{extra}">
<td class="ord">{r.order}</td>
<td>
  <div class="name">
    <a class="pt-link" href="?chart={html.escape(r.patient_id)}">{html.escape(display_name)}</a>
    <a class="pt-open" href="?chart={html.escape(r.patient_id)}" title="Open chart">Open</a>
    <a class="pt-open" href="?change={html.escape(r.patient_id)}" title="Change this person">Change</a>
    {moved}{age_tag}{extras}
  </div>
  <div class="loc">{html.escape(r.chair)} · {html.escape(r.arrival)} · {hist}</div>
</td>
<td>
  <div class="cc">{html.escape(r.complaint)}</div>
  <div class="facts">{html.escape(r.look)} · {html.escape(r.vitals_text)}</div>
</td>
<td><span class="esi e{r.esi}">ESI {r.esi}</span></td>
<td class="num">{r.acuity:.0f}</td>
<td>{conf_badge(r.confidence_label, r.confidence, r.ml_lifted, r.model_prob)}{careful}{watch}</td>
<td class="space" title="{space_title(space_plain(r.space))}">{html.escape(space_plain(r.space))}</td>
<td class="why">{html.escape(r.why)}</td>
</tr>"""
            )
        else:
            body.append(
                f"""<tr class="e{r.esi}{extra}">
<td class="ord">{r.order}</td>
<td>
  <div class="name">
    <a class="pt-link" href="?chart={html.escape(r.patient_id)}">{html.escape(display_name)}</a>
    <a class="pt-open" href="?chart={html.escape(r.patient_id)}" title="Open chart">Open</a>
    <a class="pt-open" href="?change={html.escape(r.patient_id)}" title="Change this person">Change</a>
    {moved}{age_tag}{extras}
  </div>
  <div class="loc"><span class="esi e{r.esi}">ESI {r.esi}</span> · {html.escape(r.complaint)}</div>
  <div class="facts">{html.escape(r.chair)} · {html.escape(r.arrival)}</div>
</td>
<td>{conf_badge(r.confidence_label, r.confidence, r.ml_lifted, r.model_prob)}{careful}{watch}</td>
<td class="space" title="{space_title(space_plain(r.space))}">{html.escape(space_plain(r.space))}</td>
<td class="why">{html.escape(r.why)}</td>
</tr>"""
            )

    replay_html = ""
    if st.session_state.replay:
        rp = replay_numbers()
        replay_html = f"""
<div class="replay">
  <div><strong>{rp['gain_vs_esi_min']:.1f} min sooner</strong><span>to reach unstable vs ESI-only order</span></div>
  <div><strong>{rp['gain_vs_fifo_min']:.1f} min sooner</strong><span>vs first-come-first-served</span></div>
  <div><strong>+{rp['mild_cost_vs_fifo_min']:.0f} min</strong><span>mild wait longer — on purpose, measured</span></div>
</div>"""

    next_cards = []
    for r in rows[:3]:
        was = baseline.get(r.patient_id)
        tone = ""
        if r.flag == "hold":
            tone = " hold"
        elif r.flag == "hot" or (was is not None and was > r.order):
            tone = " moved"
        elif r.escalate_bias:
            tone = " careful"
        display_name = "Patient" if redact else r.name
        careful = (
            ' <span class="esc">Being careful</span>' if r.escalate_bias else ""
        )
        next_cards.append(
            f"""<div class="next-card{tone}">
  <div class="next-top">
    <span class="next-ord">#{r.order}</span>
    <a class="pt-link" href="?chart={html.escape(r.patient_id)}">{html.escape(display_name)}</a>
    <a class="pt-open" href="?chart={html.escape(r.patient_id)}">Open</a>
  </div>
  <div class="next-meta">{conf_badge(r.confidence_label, r.confidence, r.ml_lifted, r.model_prob)}{careful}</div>
  <div class="next-space" title="{space_title(space_plain(r.space))}">{html.escape(space_plain(r.space))}</div>
  <div class="next-why">{html.escape(r.why)}</div>
</div>"""
        )

    n_ped = sum(1 for r in rows if r.age_band == "pediatric")
    n_ger = sum(1 for r in rows if r.age_band == "geriatric")
    n_zh = sum(1 for r in rows if not r.has_prior_record)
    n_unsure = sum(1 for r in rows if r.confidence_label == "low")
    n_careful = sum(1 for r in rows if r.escalate_bias)
    n_wait = sum(
        1
        for r in rows
        if r.reassess_flag in ("wait_exceeded", "both")
    )
    n_worse = sum(
        1
        for r in rows
        if r.reassess_flag in ("vitals_worse", "both")
    )
    n_lwbs = sum(1 for r in rows if r.lwbs_risk)

    styles = """
<style>
  .ed-shell, .ed-side, .ed-list {
    font-family: "Google Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    color: #202124;
  }
  .ed-shell *, .ed-side *, .ed-list * { box-sizing: border-box; }
  .ed-shell .bar {
    display: flex; justify-content: space-between; gap: 12px; align-items: center;
    background: #1a73e8; color: #fff; padding: 10px 14px;
    border-radius: 10px 10px 0 0;
  }
  .ed-shell .bar-brand { min-width: 0; flex: 1; }
  .ed-shell .hosp-name {
    margin: 0; font-size: 20px; font-weight: 600; color: #fff !important;
    letter-spacing: -0.02em; line-height: 1.15;
  }
  .ed-shell .bar-sub {
    margin: 3px 0 0 0; font-size: 11px; font-weight: 400;
    color: #d2e3fc !important; line-height: 1.3;
  }
  .ed-shell .bar h1 {
    margin: 0; font-size: 15px; font-weight: 500; color: #fff !important; letter-spacing: -0.01em;
  }
  .ed-shell .bar h1 span {
    display: block; font-size: 11px; font-weight: 400;
    color: #d2e3fc !important; margin-top: 1px;
  }
  .ed-shell .meta {
    display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; max-width: 520px;
    align-items: center;
  }
  .ed-shell .chip {
    background: rgba(255,255,255,0.18); border: none;
    font-size: 11px; padding: 3px 8px; border-radius: 14px; color: #fff !important;
  }
  .ed-shell .chip.alert { background: #ea4335; }
  .ed-shell .chip.good { background: #34a853; }
  .ed-shell .mode-tog {
    display: inline-flex; gap: 0; margin-left: 4px;
    background: rgba(0,0,0,0.15); border-radius: 14px; padding: 2px;
  }
  .ed-shell .mode-tog a {
    font-size: 11px; font-weight: 500; color: #d2e3fc !important;
    text-decoration: none; padding: 3px 10px; border-radius: 12px;
  }
  .ed-shell .mode-tog a.on {
    background: #fff; color: #174ea6 !important;
  }
  .ed-shell .stamp {
    font-size: 10px; font-weight: 600; padding: 4px 10px; border-radius: 14px;
    width: 100%; text-align: center; margin-top: 2px;
  }
  .ed-shell .stamp.wait { background: #fef7e0; color: #b06000; }
  .ed-shell .stamp.ok { background: #e6f4ea; color: #137333; }
  .ed-shell .callout-wrap {
    background: #fff; border: 1px solid #e8eaed; border-top: none;
    border-radius: 0 0 10px 10px;
    box-shadow: 0 1px 2px rgba(60,64,67,0.12);
    margin-bottom: 6px;
  }
  .ed-shell .callout {
    margin: 0; padding: 6px 12px; font-size: 12px; font-weight: 500; line-height: 1.35;
    color: #174ea6; background: #e8f0fe;
  }
  .ed-shell .replay {
    display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; padding: 8px 12px;
    background: #e6f4ea; border-top: 1px solid #ceead6;
  }
  .ed-shell .replay strong { display: block; font-size: 13px; color: #137333; font-weight: 500; }
  .ed-shell .replay span { font-size: 11px; color: #5f6368; }

  .ed-side {
    max-height: min(720px, calc(100vh - 200px));
    overflow-y: auto;
    background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
    box-shadow: 0 1px 2px rgba(60,64,67,0.12);
    padding: 10px 12px 10px;
    display: flex; flex-direction: column;
  }
  .ed-side .brand-foot {
    margin-top: auto; padding: 20px 0 4px; border-top: 1px solid #e8eaed;
    position: sticky; bottom: 0; z-index: 2; background: #fff;
  }
  .ed-side .brand-foot .brand {
    font-size: 16px; font-weight: 700; letter-spacing: 0.1em;
    color: #174ea6; margin: 0;
  }
  .ed-side .brand-foot .tag {
    font-size: 11px; color: #5f6368; margin: 4px 0 0 0; line-height: 1.35;
  }
  .ed-side .brand-foot .legal {
    font-size: 10px; color: #80868b; margin: 6px 0 0 0; line-height: 1.3;
  }
  .ed-side .next-head {
    display: flex; flex-direction: column; gap: 1px; margin-bottom: 8px;
  }
  .ed-side .next-head strong {
    font-size: 13px; font-weight: 600; color: #202124;
  }
  .ed-side .next-head span { font-size: 11px; color: #80868b; }
  .ed-side .next-stack { display: flex; flex-direction: column; gap: 8px; }
  .ed-side .next-card {
    background: #fafbfc; border: 1px solid #e8eaed; border-radius: 10px;
    padding: 10px; border-left: 3px solid #1a73e8;
  }
  .ed-side .next-card.moved { background: #f0faf3; border-left-color: #34a853; }
  .ed-side .next-card.hold { background: #fff8e8; border-left-color: #f9ab00; }
  .ed-side .next-card.careful { background: #fce8e6; border-left-color: #ea4335; }
  .ed-side .next-top {
    display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px;
  }
  .ed-side .next-ord { font-size: 14px; font-weight: 600; color: #5f6368; }
  .ed-side .next-card.moved .next-ord { color: #137333; }
  .ed-side .next-name { font-size: 13px; font-weight: 500; color: #202124; }
  .ed-side .next-meta { margin-bottom: 4px; }
  .ed-side .next-space {
    font-size: 11px; font-weight: 500; color: #174ea6; margin-bottom: 3px;
  }
  .ed-side .next-why { font-size: 11px; color: #5f6368; line-height: 1.35; }
  .ed-side .esc {
    display: inline-block; margin-left: 4px; font-size: 10px; font-weight: 600; color: #c5221f;
  }
  .ed-side .block {
    margin-top: 10px; padding-top: 10px; border-top: 1px solid #e8eaed;
  }
  .ed-side .block-h {
    font-size: 10px; font-weight: 600; color: #5f6368;
    text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 6px 0;
  }
  .ed-side .watch-list {
    list-style: none; margin: 0; padding: 0;
  }
  .ed-side .watch-list li {
    font-size: 12px; color: #202124; padding: 5px 0;
    border-bottom: 1px solid #f1f3f4;
    display: flex; justify-content: space-between; gap: 8px;
  }
  .ed-side .watch-list li:last-child { border-bottom: none; }
  .ed-side .watch-list .n {
    font-weight: 600; color: #174ea6; min-width: 1.5em; text-align: right;
  }
  .ed-side .watch-list .n.hot { color: #c5221f; }
  .ed-side .assume {
    font-size: 11px; color: #5f6368; line-height: 1.4; margin: 0;
  }
  .ed-side .assume strong { color: #202124; font-weight: 500; }

  .ed-list {
    background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
    box-shadow: 0 1px 2px rgba(60,64,67,0.12);
    display: flex; flex-direction: column;
    max-height: min(720px, calc(100vh - 200px));
    overflow: hidden;
  }
  .ed-list .list-title {
    padding: 8px 12px 6px; font-size: 12px; font-weight: 600; color: #202124;
    border-bottom: 1px solid #e8eaed; background: #fafbfc;
    display: flex; justify-content: space-between; align-items: center;
    flex: 0 0 auto; gap: 10px;
  }
  .ed-list .list-title span { font-weight: 400; font-size: 11px; color: #80868b; }
  .ed-list .list-title .new-arr {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 12px; font-weight: 600; color: #1a73e8 !important;
    text-decoration: none; background: #e8f0fe; padding: 5px 10px;
    border-radius: 16px; white-space: nowrap;
  }
  .ed-list .list-title .new-arr:hover { background: #d2e3fc; }
  .ed-list .pt-link, .ed-side .pt-link {
    color: #174ea6 !important; font-weight: 500; text-decoration: none;
    border-bottom: 1px solid transparent;
  }
  .ed-list .pt-link:hover, .ed-side .pt-link:hover {
    border-bottom-color: #174ea6; text-decoration: none;
  }
  .ed-list .pt-open, .ed-side .pt-open {
    display: inline-block; margin-left: 6px; font-size: 10px; font-weight: 600;
    color: #1a73e8 !important; text-decoration: none;
    background: #e8f0fe; padding: 2px 7px; border-radius: 10px;
  }
  .ed-list .pt-open:hover, .ed-side .pt-open:hover { background: #d2e3fc; }
  .ed-list .list-scroll {
    overflow-y: auto;
    overflow-x: auto;
    flex: 1 1 auto;
    min-height: 280px;
    max-height: min(560px, calc(100vh - 320px));
    -webkit-overflow-scrolling: touch;
  }
  .ed-list table { width: 100%; border-collapse: collapse; }
  .ed-list th {
    text-align: left; font-size: 11px; font-weight: 500; letter-spacing: 0.02em;
    text-transform: none; color: #5f6368; padding: 8px 10px;
    border-bottom: 1px solid #e8eaed; background: #f8f9fa;
    position: sticky; top: 0; z-index: 2;
  }
  .ed-list td {
    padding: 8px 10px; border-bottom: 1px solid #f1f3f4;
    vertical-align: middle; font-size: 13px;
  }
  .ed-list tr.e1 td:first-child { box-shadow: inset 4px 0 0 #ea4335; }
  .ed-list tr.e2 td:first-child { box-shadow: inset 4px 0 0 #fa7b17; }
  .ed-list tr.e3 td:first-child { box-shadow: inset 4px 0 0 #fbbc04; }
  .ed-list tr.e4 td:first-child { box-shadow: inset 4px 0 0 #1a73e8; }
  .ed-list tr.e5 td:first-child { box-shadow: inset 4px 0 0 #9aa0a6; }
  .ed-list tr.is-moved { background: #f0faf3; }
  .ed-list tr.is-hold { background: #fff8e8; }
  .ed-list .ord { width: 36px; font-size: 14px; font-weight: 500; color: #80868b; }
  .ed-list tr.is-moved .ord { color: #137333; }
  .ed-list .was {
    display: inline-block; margin-left: 6px; font-size: 10px; font-weight: 600;
    background: #ceead6; color: #137333; padding: 2px 7px; border-radius: 10px;
  }
  .ed-list .age {
    display: inline-block; margin-left: 6px; font-size: 10px; font-weight: 600;
    background: #e8f0fe; color: #1967d2; padding: 2px 7px; border-radius: 10px;
  }
  .ed-list .under {
    display: inline-block; margin-left: 6px; font-size: 10px; font-weight: 600;
    background: #feefc3; color: #b06000; padding: 2px 7px; border-radius: 10px;
  }
  .ed-list .esc {
    display: inline-block; margin-left: 4px; font-size: 10px; font-weight: 600; color: #c5221f;
  }
  .ed-list .watch {
    display: inline-block; margin-left: 4px; font-size: 10px; font-weight: 600;
    background: #fce8e6; color: #c5221f; padding: 2px 6px; border-radius: 10px;
  }
  .ed-list .name { font-weight: 500; font-size: 13px; color: #202124; }
  .ed-list .loc, .ed-list .facts { font-size: 11px; color: #5f6368; margin-top: 2px; }
  .ed-list .cc { font-weight: 500; }
  .ed-list .esi {
    display: inline-block; font-size: 10px; font-weight: 600;
    padding: 2px 6px; border-radius: 8px;
  }
  .ed-list .esi.e1 { background: #fce8e6; color: #c5221f; }
  .ed-list .esi.e2 { background: #feefc3; color: #b06000; }
  .ed-list .esi.e3 { background: #fef7e0; color: #b06000; }
  .ed-list .esi.e4 { background: #e8f0fe; color: #1967d2; }
  .ed-list .esi.e5 { background: #f1f3f4; color: #5f6368; }
  .ed-list .num { font-weight: 500; color: #174ea6; }
  .ed-list .space { font-weight: 500; color: #174ea6; font-size: 12px; }
  .ed-list .why { font-size: 12px; color: #5f6368; line-height: 1.35; }
</style>
"""

    u_name = html.escape(user_name or "User")
    u_desig = html.escape(user_designation or "")
    u_init = html.escape(user_initials or "?")
    legal_line = (
        '<p class="legal">Demo · fake patients · not a medical record</p>'
        if is_demo
        else ""
    )

    header = f"""
{styles}
<div class="ed-shell">
  <div class="bar">
    <div class="bar-brand">
      <p class="hosp-name">{html.escape(hospital_name)}</p>
      <p class="bar-sub">{html.escape(hospital_place)} · Door list — who goes next, and where · you confirm</p>
    </div>
    <div class="meta">
      <span class="chip">19:40</span>
      <span class="chip">{hosp}</span>
      {busy}
      <span class="chip alert">Resus {resus}</span>
      <span class="chip {'good' if room.bay_open else 'alert'}">Bays {bay}</span>
      <span class="chip">{getattr(room, 'nurses_on_floor', '?')} nurses</span>
      <span class="chip">{len(rows)} waiting</span>
      <span class="mode-tog">
        <a class="mode{floor_on}" href="?mode=floor">Floor</a>
        <a class="mode{demo_on}" href="?mode=demo">Demo</a>
      </span>
      <span class="stamp {stamp}">{status}</span>
    </div>
  </div>
  <div class="callout-wrap">
    <p class="callout">{html.escape(banner)}</p>
    {replay_html}
  </div>
</div>
"""

    demo_blocks = ""
    if is_demo:
        demo_blocks = f"""
  <div class="block">
    <p class="block-h">Who’s here</p>
    <ul class="watch-list">
      <li><span>Children</span><span class="n">{n_ped}</span></li>
      <li><span>Older adults</span><span class="n">{n_ger}</span></li>
      <li><span>First visit</span><span class="n">{n_zh}</span></li>
      <li><span>Unsure (thin information)</span><span class="n {'hot' if n_unsure else ''}">{n_unsure}</span></li>
    </ul>
  </div>

  <div class="block">
    <p class="block-h">Stated assumptions</p>
    <p class="assume">
      <strong>{html.escape(hosp)}</strong> · ~{profile.visits_per_day}/day<br/>
      ESI 1–5 · DoorBoard ranks who next<br/>
      History on file: ~50/50 · {n_zh} first visits here<br/>
      India DPDP 2023 · chart write off · you confirm
    </p>
  </div>
"""

    left = f"""
<div class="ed-side">
{demo_blocks}
  <div class="brand-foot">
    <p class="brand">DOORBOARD</p>
    <p class="tag">Who goes next · which open space — you confirm before anything is final</p>
    {legal_line}
  </div>
</div>
"""

    if is_demo:
        thead = (
            "<th>#</th><th>Patient</th><th>At the door</th><th>ESI</th>"
            "<th>Urgency</th><th>How sure</th><th>Where</th><th>Why</th>"
        )
        empty_cols = 8
    else:
        thead = "<th>#</th><th>Patient</th><th>How sure</th><th>Where</th><th>Why</th>"
        empty_cols = 5

    middle = f"""
<div class="ed-list">
  <div class="list-title">
    <div>Waiting list <span>{len(table_rows)} shown · Open chart · Change in place</span></div>
    <a class="new-arr" href="?new=1">＋ New arrival</a>
  </div>
  <div class="list-scroll">
    <table>
      <thead>
        <tr>{thead}</tr>
      </thead>
      <tbody>{''.join(body) if body else f'<tr><td colspan="{empty_cols}" style="padding:20px;color:#5f6368;">No one matches this filter.</td></tr>'}</tbody>
    </table>
  </div>
</div>
"""
    return header, left, middle


def board_html(rows, sealed, room, banner, baseline, profile, redact: bool = False) -> str:
    """Back-compat single block (unused by main layout)."""
    h, l, m = board_parts(rows, sealed, room, banner, baseline, profile, redact)
    return h + l + m


def _vital_field(val) -> str:
    return "" if val is None else str(val)


def _parse_optional_num(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    return float(raw)


ARRIVAL_OPTS = ["Walk-in", "EMS", "Transfer"]
PATHWAY_OPTS = ["(none)", "stroke", "stemi", "sepsis"]


# --- Page chrome ---
st.markdown(
    """
    <style>
      .stApp { background: #f8f9fa !important; color: #202124; }
      .block-container {
        padding: 0.4rem 0.75rem 1rem !important;
        max-width: 100% !important;
      }
      header[data-testid="stHeader"] { background: transparent; }
      [data-testid="stToolbar"], [data-testid="stDecoration"],
      [data-testid="stStatusWidget"], #MainMenu, footer,
      [data-testid="collapsedControl"] { display: none !important; }

      /* Desk panel = Streamlit bordered container, not a fake HTML wrapper */
      [data-testid="stVerticalBlockBorderWrapper"] {
        background: #fff !important;
        border: 1px solid #e8eaed !important;
        border-radius: 12px !important;
        box-shadow: 0 1px 2px rgba(60,64,67,0.12) !important;
        padding: 4px 6px 8px !important;
      }
      /* Pin Your desk while the middle list scrolls */
      [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child,
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
      .db-floor-cols > [data-testid="column"]:last-child,
      .db-floor-cols > [data-testid="stColumn"]:last-child {
        position: sticky;
        top: 0.5rem;
        align-self: flex-start;
        z-index: 2;
      }
      /* Drag partitions between Next up / list / desk */
      .db-floor-cols { position: relative !important; }
      .db-col-handle {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 14px;
        margin-left: -7px;
        cursor: col-resize;
        z-index: 40;
        background: transparent;
        touch-action: none;
        user-select: none;
      }
      .db-col-handle::after {
        content: "";
        position: absolute;
        left: 6px;
        top: 8%;
        bottom: 8%;
        width: 3px;
        border-radius: 2px;
        background: #dadce0;
        transition: background 0.12s ease;
      }
      .db-col-handle:hover::after,
      .db-col-handle.dragging::after { background: #174ea6; }
      .db-col-handle:hover,
      .db-col-handle.dragging { background: rgba(23, 78, 166, 0.06); }
      /* Left = Next up only — stay tall enough, don't force page scroll for capacity */
      [data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child
      [data-testid="stVerticalBlockBorderWrapper"],
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child
      [data-testid="stVerticalBlockBorderWrapper"] {
        max-height: calc(100vh - 140px);
        overflow-y: auto;
      }
      /* Side form / desk: taller usable area, scroll inside the card */
      [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child
      [data-testid="stVerticalBlockBorderWrapper"],
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child
      [data-testid="stVerticalBlockBorderWrapper"] {
        max-height: calc(100vh - 140px);
        overflow-y: auto;
      }
      .desk-status {
        font-size: 11px; color: #5f6368; margin: 0 0 6px 0;
        padding: 5px 8px; background: #f8f9fa; border-radius: 8px; line-height: 1.3;
      }
      .desk-status strong { color: #202124; font-weight: 500; }
      .desk-title {
        font-size: 15px; font-weight: 500; color: #202124; margin: 0 0 2px 0;
      }
      .desk-sub {
        font-size: 12px; color: #5f6368; margin: 0 0 6px 0; line-height: 1.3;
      }
      .cover-card {
        background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
        padding: 10px 12px; margin: 8px 0 6px 0;
        box-shadow: 0 1px 2px rgba(60,64,67,0.1);
      }
      .cover-card h4 {
        margin: 0 0 4px 0; font-size: 13px; font-weight: 600; color: #202124;
      }
      .cover-card .assume-line {
        font-size: 11px; color: #5f6368; margin: 0 0 8px 0; line-height: 1.35;
      }
      .cover-card ul { list-style: none; margin: 0; padding: 0; }
      .cover-card li {
        font-size: 12px; color: #202124; padding: 3px 0;
        display: flex; gap: 8px; align-items: flex-start; line-height: 1.3;
      }
      .cover-card .ok { color: #137333; font-weight: 700; }
      .cover-card .no { color: #b06000; font-weight: 700; }
      .cover-card .tag { font-size: 10px; color: #80868b; white-space: nowrap; }
      .desk-h {
        font-size: 11px; font-weight: 600; color: #5f6368;
        text-transform: uppercase; letter-spacing: 0.04em;
        margin: 6px 0 4px 0;
      }
      .slot-grid {
        display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 4px 0 8px;
      }
      .slot-strip {
        display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px;
        margin: 0 0 10px 0;
      }
      .slot-strip .slot-card {
        background: #fff; border: 1px solid #e8eaed; border-radius: 10px;
        padding: 8px 10px; box-shadow: 0 1px 2px rgba(60,64,67,0.08);
      }
      .slot-strip .slot-card .k {
        font-size: 10px; color: #5f6368; font-weight: 600;
        letter-spacing: 0.04em; text-transform: uppercase;
      }
      .slot-strip .slot-card .v {
        font-size: 18px; font-weight: 600; color: #174ea6; line-height: 1.2;
      }
      .slot-strip .slot-card.empty .v { color: #c5221f; }
      .slot-strip .slot-card .f {
        font-size: 10px; color: #5f6368; margin-top: 2px; line-height: 1.3;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .spaces-head {
        display: flex; align-items: baseline; justify-content: space-between;
        margin: 2px 0 6px 0;
      }
      .spaces-head .desk-h { margin: 0; }
      .spaces-head .hint { font-size: 11px; color: #80868b; }
      .then-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; padding: 6px 0; border-bottom: 1px solid #f1f3f4;
        font-size: 12px; color: #202124;
      }
      .then-row:last-child { border-bottom: none; }
      .then-row .meta { color: #5f6368; font-size: 11px; }
      .eye-panel {
        background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
        padding: 8px 10px; margin: 0 0 8px 0;
      }
      .eye-panel .group-title {
        font-size: 12px; font-weight: 600; color: #202124; margin: 6px 0 2px;
      }
      .slot-card {
        background: #f8f9fa; border: 1px solid #e8eaed; border-radius: 8px;
        padding: 6px 8px;
      }
      .slot-card .k { font-size: 10px; color: #5f6368; font-weight: 600; letter-spacing: 0.03em; }
      .slot-card .v { font-size: 16px; font-weight: 600; color: #174ea6; line-height: 1.2; }
      .slot-card .f { font-size: 10px; color: #5f6368; margin-top: 2px; line-height: 1.3; }
      .slot-card.empty .v { color: #c5221f; }
      .event-card {
        background: #fce8e6; border: 1px solid #f6aea9; border-radius: 10px;
        padding: 8px 10px; margin: 0 0 8px 0;
      }
      .event-card strong { display: block; font-size: 13px; color: #202124; }
      .event-card span { font-size: 11px; color: #5f6368; line-height: 1.35; }
      .eye-hint { font-size: 11px; color: #5f6368; margin: 0 0 4px 0; }
      .next-explain {
        margin-top: 6px; padding: 6px 8px; background: #f8f9fa;
        border-radius: 8px; border: 1px solid #e8eaed;
      }
      .next-explain .ex-label {
        display: block; font-size: 10px; font-weight: 700; color: #174ea6;
        letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 2px;
      }
      .next-explain ol {
        margin: 0; padding-left: 16px; font-size: 11px; color: #3c4043; line-height: 1.35;
      }
      .habit-strip {
        display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;
        margin: 2px 0 6px 0;
      }
      .habit-strip .h {
        background: #e8f0fe; border-radius: 8px; padding: 6px 8px;
      }
      .habit-strip .k { font-size: 10px; color: #5f6368; font-weight: 600; }
      .habit-strip .v { font-size: 14px; font-weight: 600; color: #174ea6; }
      .integ-card {
        background: #fff; border: 1px solid #e8eaed; border-radius: 10px;
        padding: 8px 10px; margin: 6px 0; font-size: 12px; line-height: 1.4;
      }
      .integ-card .deg { color: #c5221f; font-weight: 600; }
      .integ-card .ok { color: #137333; font-weight: 600; }
      .before-after {
        margin: 8px 0 4px 0; padding: 8px 10px; background: #e8f0fe;
        border-radius: 8px; font-size: 12px; color: #202124; line-height: 1.4;
        border: 1px solid #d2e3fc;
      }
      .before-after strong { color: #174ea6; font-weight: 600; }
      .before-after .ba-label { color: #5f6368; font-weight: 500; }
      .before-after .ba-note { color: #5f6368; font-size: 11px; }
      .script-card {
        background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
        padding: 10px 12px; margin: 6px 0 8px 0; font-size: 12px; color: #5f6368;
        line-height: 1.45;
      }
      .script-card h4 {
        margin: 0 0 6px 0; font-size: 13px; font-weight: 600; color: #202124;
      }
      .script-card ol { margin: 0; padding-left: 18px; }
      .script-card li { margin: 2px 0; }
      .tiny-foot {
        margin-top: 10px; padding: 6px 0; text-align: center;
        font-size: 11px; color: #80868b; line-height: 1.3;
      }
      .brand-foot {
        margin-top: 12px; padding: 8px 2px 4px;
        display: flex; flex-direction: column; align-items: flex-start; gap: 1px;
      }
      .brand-foot .brand {
        font-size: 13px; font-weight: 700; letter-spacing: 0.08em;
        color: #174ea6; margin: 0;
      }
      .brand-foot .tag {
        font-size: 11px; color: #5f6368; margin: 0; line-height: 1.35;
      }
      .brand-foot .legal {
        font-size: 10px; color: #80868b; margin: 4px 0 0 0;
      }
      .account-bar {
        margin: 4px 0 0 auto; padding: 5px 10px 5px 6px;
        background: #fff; border: 1px solid #e8eaed; border-radius: 10px;
        box-shadow: 0 1px 2px rgba(60,64,67,0.08);
        display: inline-flex; align-items: center; gap: 8px;
        width: fit-content; max-width: 100%;
      }
      .account-bar .avatar {
        width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
        background: #1a73e8; color: #fff; font-size: 10px; font-weight: 600;
        display: flex; align-items: center; justify-content: center;
        letter-spacing: 0.02em; line-height: 1;
      }
      .account-bar .who { min-width: 0; flex: 0 1 auto; }
      .account-bar .who strong {
        display: block; font-size: 12px; font-weight: 600; color: #202124;
        line-height: 1.2; white-space: nowrap;
      }
      .account-bar .who span {
        display: block; font-size: 10px; color: #5f6368; margin-top: 1px;
        line-height: 1.2; white-space: nowrap;
      }
      .account-row {
        display: flex; justify-content: flex-end; align-items: center;
        gap: 8px; margin-top: 2px;
      }
      .login-wrap {
        max-width: 100%;
        margin: 0;
        padding: 0;
      }
      .login-card {
        background: #fff; border: 1px solid #e8eaed; border-radius: 16px;
        box-shadow: 0 2px 8px rgba(60,64,67,0.10); padding: 30px 26px 24px;
        text-align: center;
      }
      .login-card .mark {
        font-size: 28px; font-weight: 700; letter-spacing: 0.12em; color: #174ea6;
        margin: 0 0 8px 0; line-height: 1.1;
      }
      .login-card .tagline {
        margin: 0 0 20px 0; font-size: 12px; color: #5f6368; line-height: 1.4;
      }
      .login-card h2 {
        margin: 0 0 6px 0; font-size: 22px; font-weight: 600; color: #202124;
      }
      .login-card .hint {
        margin: 0; font-size: 13px; color: #5f6368; line-height: 1.45;
      }
      .login-foot {
        margin: 16px 0 0 0; text-align: center;
        font-size: 11px; color: #80868b; line-height: 1.35;
      }
      .login-admin {
        margin: 10px 0 0 0; text-align: center;
        font-size: 12px; color: #5f6368;
      }
      .login-admin a {
        color: #174ea6; font-weight: 500; text-decoration: none;
      }
      .login-spacer {
        height: max(72px, calc((100vh - 560px) / 2));
      }
      .app-topnav {
        display: flex; align-items: center; gap: 8px;
        padding: 2px 0; overflow: hidden; max-width: 100%;
      }
      .app-topnav .mark, .app-mark {
        font-size: 12px; font-weight: 700; letter-spacing: 0.1em; color: #174ea6;
        margin: 0; padding: 8px 0;
      }
      .app-topnav .av {
        width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
        background: #1a73e8; color: #fff; font-size: 11px; font-weight: 700;
        display: inline-flex; align-items: center; justify-content: center;
      }
      .app-topnav .nm { min-width: 0; line-height: 1.15; text-align: left; overflow: hidden; }
      .app-topnav .nm strong {
        display: block; font-size: 12px; font-weight: 600; color: #202124;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .app-topnav .nm span {
        display: block; font-size: 10px; color: #5f6368;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .stButton > button {
        cursor: pointer !important;
      }
      [data-testid="stHorizontalBlock"]:has([data-testid="stVerticalBlock"] .login-card)
      [data-testid="column"]:nth-child(2) .stButton { margin-top: 2px; }
      [data-testid="stHorizontalBlock"]:has([data-testid="stVerticalBlock"] .login-card)
      [data-testid="column"]:nth-child(2) .stButton > button {
        min-height: 44px !important; border-radius: 22px !important;
      }

      .stButton > button {
        border-radius: 20px !important; font-weight: 500 !important;
        color: #202124 !important; background: #fff !important;
        border: 1px solid #dadce0 !important; min-height: 38px !important;
        box-shadow: none !important;
        cursor: pointer !important;
      }
      .stButton > button:hover {
        background: #f8f9fa !important; border-color: #dadce0 !important;
      }
      .stButton > button[kind="primary"],
      .stButton > button[data-testid="stBaseButton-primary"] {
        background: #1a73e8 !important; border-color: #1a73e8 !important;
        color: #ffffff !important;
      }
      .stButton > button[kind="primary"]:hover,
      .stButton > button[data-testid="stBaseButton-primary"]:hover {
        background: #1765cc !important; border-color: #1765cc !important;
        color: #ffffff !important;
      }
      div[data-testid="stExpander"] {
        background: #fff; border: 1px solid #e8eaed; border-radius: 12px;
        margin-top: 6px;
      }
      .stTextInput input, .stSelectbox > div > div {
        border-radius: 8px !important;
      }
      div[data-testid="stRadio"] [role="radiogroup"] label {
        background: #fff; border: 1px solid #dadce0; border-radius: 20px;
        padding: 2px 12px !important; margin-right: 6px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Soft logout via query link (optional)
if "logout" in st.query_params:
    st.session_state.logged_in = False
    st.session_state.show_chart = False
    st.session_state.show_add_form = False
    try:
        del st.query_params["logout"]
    except Exception:
        st.query_params.clear()

if st.session_state.get("logged_in") is False:
    if "show_create_account" not in st.session_state:
        st.session_state.show_create_account = False
    st.session_state.show_chart = False
    st.session_state.show_add_form = False
    st.markdown('<div class="login-spacer"></div>', unsafe_allow_html=True)
    _l, mid, _r = st.columns([1.25, 1, 1.25], gap="small")
    with mid:
        st.markdown(
            '<div class="login-wrap"><div class="login-card">'
            '<p class="mark">DOORBOARD</p>'
            '<p class="tagline">Who goes next · which open space — you confirm</p>'
            "<h2>Sign in</h2>"
            '<p class="hint">Demo accounts only — pick who you are on the door list. '
            "Nothing writes to the EHR.</p>"
            "</div></div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        if not st.session_state.show_create_account:
            if st.button(
                "Priya Nair · Charge nurse",
                use_container_width=True,
                type="primary",
                key="login_cn",
            ):
                apply_account("charge_nurse")
                st.session_state.banner = (
                    "Signed in as Priya Nair (charge nurse). "
                    "You can confirm and change the list."
                )
                st.rerun()
            if st.button(
                "Arjun Mehta · View only",
                use_container_width=True,
                key="login_viewer",
            ):
                apply_account("viewer")
                st.session_state.banner = (
                    "Signed in as Arjun Mehta (view only). "
                    "You can watch — not change or confirm."
                )
                st.rerun()
            if st.button(
                "Create a new account · admin only",
                use_container_width=True,
                key="login_create_toggle",
            ):
                st.session_state.show_create_account = True
                st.rerun()
        else:
            st.caption("Create account — admin only (demo PIN: admin)")
            admin_pin = st.text_input(
                "Admin PIN *",
                type="password",
                key="create_admin_pin",
                placeholder="admin",
            )
            new_name = st.text_input(
                "Full name *",
                key="create_name",
                placeholder="Family, Given",
            )
            new_desig = st.text_input(
                "Designation *",
                key="create_desig",
                placeholder="e.g. Charge nurse · ED",
            )
            new_role = st.selectbox(
                "Access *",
                options=["charge_nurse", "viewer"],
                format_func=lambda r: (
                    "Charge nurse (can confirm)"
                    if r == "charge_nurse"
                    else "View only"
                ),
                key="create_role",
            )
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Back", use_container_width=True, key="create_back"):
                    st.session_state.show_create_account = False
                    st.rerun()
            with b2:
                if st.button(
                    "Create & sign in",
                    use_container_width=True,
                    type="primary",
                    key="create_submit",
                ):
                    if (admin_pin or "").strip() != "admin":
                        st.error("Admin PIN required.")
                    elif not (new_name or "").strip() or not (new_desig or "").strip():
                        st.error("Name and designation are required.")
                    else:
                        name = new_name.strip()
                        parts = [p for p in name.replace(",", " ").split() if p]
                        initials = (
                            "".join(p[0] for p in parts[:2]).upper()
                            if parts
                            else "?"
                        )
                        st.session_state.logged_in = True
                        st.session_state.role = new_role
                        st.session_state.user_name = name
                        st.session_state.user_designation = new_desig.strip()
                        st.session_state.user_initials = initials
                        st.session_state.show_create_account = False
                        st.session_state.banner = (
                            f"Signed in as {name} ({new_desig.strip()}). "
                            "Demo account — not written to any directory."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:00",
                                "actor": "admin",
                                "action": "create_account",
                                "patient_id": "*",
                                "detail": f"Created demo user {name} · {new_role}",
                                "before": "",
                                "after": new_role,
                                "legal_note": "Admin-only demo account create.",
                            }
                        )
                        st.rerun()

        st.markdown(
            '<p class="login-foot">Kaveri General · triage assist demo · chart write off</p>',
            unsafe_allow_html=True,
        )
    st.stop()

profile = get_profile(st.session_state.profile_id)
if (
    not st.session_state.room.surge
    and not getattr(st.session_state.room, "staffing_shock", False)
    and not st.session_state.get("slot_fills")
):
    st.session_state.room = room_for(
        st.session_state.profile_id,
        surge=False,
        shift_mode=st.session_state.shift_mode,
    )
else:
    if st.session_state.room.surge:
        st.session_state.shift_mode = "surge"

run_auto_queue_watch()

off = set(st.session_state.get("off_board") or [])
waiting_pts = [p for p in st.session_state.patients if p.id not in off]
rows = rank_room(waiting_pts, st.session_state.room)
integrations = mock_integrations(
    profile, force_degraded=bool(st.session_state.get("ehr_degraded_force"))
)
can_act = st.session_state.get("role", "charge_nurse") == "charge_nurse"


@st.fragment(run_every=timedelta(seconds=120))
def _auto_watch_keepalive():
    """Force a full rerun so queue-watch advances without a button press."""
    if time.time() - float(st.session_state.get("last_auto_tick_wall") or 0) >= 120:
        st.rerun()


_auto_watch_keepalive()

# Deep links: chart, new arrival, Floor/Demo mode
qp = st.query_params
if "chart" in qp:
    cid = qp["chart"]
    if isinstance(cid, (list, tuple)):
        cid = cid[0]
    st.session_state.detail_patient_id = str(cid)
    st.session_state.show_chart = True
    st.session_state.show_add_form = False
    st.session_state.change_patient_id = None
    st.query_params.clear()
elif "change" in qp:
    cid = qp["change"]
    if isinstance(cid, (list, tuple)):
        cid = cid[0]
    st.session_state.change_patient_id = str(cid)
    st.session_state.focus_patient_id = str(cid)
    st.session_state.show_chart = False
    st.session_state.show_add_form = False
    st.query_params.clear()
elif "new" in qp:
    st.session_state.show_add_form = True
    st.session_state.show_chart = False
    st.query_params.clear()
elif "mode" in qp:
    mode = qp["mode"]
    if isinstance(mode, (list, tuple)):
        mode = mode[0]
    if str(mode) in ("floor", "demo"):
        st.session_state.ui_mode = str(mode)
    st.query_params.clear()

is_demo = st.session_state.get("ui_mode", "floor") == "demo"

baseline = st.session_state.baseline_order
if "list_filter" not in st.session_state:
    st.session_state.list_filter = "all"

eye_rows = [r for r in rows if needs_your_eye(r, baseline)]
list_rows = eye_rows if st.session_state.list_filter == "eye" else rows

header_html, next_html, list_html = board_parts(
    rows,
    st.session_state.sealed,
    st.session_state.room,
    st.session_state.banner,
    baseline,
    profile,
    redact=st.session_state.get("redact_phi", False),
    list_rows=list_rows,
    is_demo=is_demo,
    user_name=st.session_state.get("user_name", ""),
    user_designation=st.session_state.get("user_designation", ""),
    user_initials=st.session_state.get("user_initials", ""),
)

st.html(header_html)

# Account strip under header — compact card + Log out (no empty stretch)
_sp, foot_r = st.columns([2.4, 1.1], gap="small")
with foot_r:
    u_name = html.escape(st.session_state.get("user_name", "User"))
    u_desig = html.escape(st.session_state.get("user_designation", ""))
    u_init = html.escape(st.session_state.get("user_initials", "?"))
    acc_l, acc_r = st.columns([2.6, 1], gap="small")
    with acc_l:
        st.markdown(
            f'<div class="account-row"><div class="account-bar">'
            f'<div class="avatar" title="Signed in">{u_init}</div>'
            f'<div class="who"><strong>{u_name}</strong>'
            f"<span>{u_desig}</span></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    with acc_r:
        if st.button("Log out", use_container_width=True, key="btn_logout"):
            st.session_state.logged_in = False
            st.session_state.show_chart = False
            st.session_state.show_add_form = False
            st.session_state.show_create_account = False
            if "audit" in st.session_state:
                st.session_state.audit.append(
                    {
                        "ts": "19:55",
                        "actor": st.session_state.get("user_name", "user"),
                        "action": "logout",
                        "patient_id": "*",
                        "detail": "Signed out of DoorBoard",
                        "before": st.session_state.get("role", ""),
                        "after": "logged_out",
                        "legal_note": "",
                    }
                )
            st.rerun()

# Forms open in the right column (side panel) so the board stays visible
_form_open = st.session_state.get("show_add_form") or (
    st.session_state.get("show_chart") and st.session_state.get("detail_patient_id")
)

redact = st.session_state.get("redact_phi", False)
eye_groups = group_eye_rows(rows, baseline)
# Cap stacked wait alerts from older sessions (one at a time going forward)
_pending = list(st.session_state.get("pending_events") or [])
_wait_ev = [e for e in _pending if e.get("kind") == "wait"]
_other_ev = [e for e in _pending if e.get("kind") != "wait"]
if len(_wait_ev) > 1:
    st.session_state.pending_events = _other_ev + _wait_ev[:1]

pending = list(st.session_state.get("pending_events") or [])
fills = list(st.session_state.get("slot_fills") or [])
slots = slot_inventory(st.session_state.room, fills)

# Capacity first — full width, always on screen before Next up / list / desk
st.markdown(
    '<div class="spaces-head"><p class="desk-h">Open spaces</p>'
    '<span class="hint">Check capacity here · then Send on the left</span></div>',
    unsafe_allow_html=True,
)
st.markdown(slots_strip_html(slots), unsafe_allow_html=True)
st.caption(
    "\"Hold\" means queued first for that space when it opens next — an ordering "
    "decision, not a stuck patient. Hover any space cell in the list for what it means. "
    "Drag the divider between columns to widen or shrink · double-click resets."
)

left, mid, right = st.columns(
    floor_col_widths(form_open=bool(_form_open)), gap="small"
)
inject_floor_col_resizer()

with left:
    with st.container(border=True):
        st.markdown('<p class="desk-h">Next up</p>', unsafe_allow_html=True)
        st.caption("One person. You confirm — not the whole list.")

        for ev in pending:
            erow = next((r for r in rows if r.patient_id == ev.get("id")), None)
            ev_name = _short_name(erow.name) if erow else ev.get("id")
            kind = ev.get("kind") or "wait"
            st.markdown(
                f'<div class="event-card"><strong>{html.escape(ev.get("title", "Floor event"))}</strong>'
                f"<span>{html.escape(ev_name)} — {html.escape(ev.get('detail', ''))}</span></div>",
                unsafe_allow_html=True,
            )
            if kind == "worse":
                ea, ek = st.columns(2)
                with ea:
                    if st.button(
                        "Accept move",
                        use_container_width=True,
                        disabled=not can_act or erow is None,
                        key=f"ev_accept_{ev.get('id')}",
                        type="primary",
                    ):
                        if erow:
                            override = None
                            if (
                                st.session_state.room.bay_open > 0
                                and not space_is_open(erow.space)
                            ):
                                override = "Bay open"
                            send_patient(
                                erow,
                                reason=ev.get("title", "Accepted floor event"),
                                space_override=override,
                            )
                            st.rerun()
                with ek:
                    if st.button(
                        "Keep",
                        use_container_width=True,
                        disabled=not can_act,
                        key=f"ev_keep_{ev.get('id')}",
                    ):
                        if erow:
                            hold_patient(
                                erow, reason="Keep — charge reviewed floor event"
                            )
                        else:
                            dismiss_events_for(ev.get("id"))
                        st.rerun()
            else:
                # Wait / other alerts: don't fake "Accept move" when no space
                can_send_ev = (
                    erow is not None and space_is_open(erow.space) and can_act
                )
                e1, e2, e3 = st.columns(3)
                with e1:
                    if st.button(
                        "Send" if can_send_ev else "Change",
                        use_container_width=True,
                        disabled=not can_act or erow is None,
                        key=f"ev_act_{ev.get('id')}",
                        type="primary",
                    ):
                        if erow and can_send_ev:
                            send_patient(erow, reason=ev.get("title", "Wait alert send"))
                        elif erow:
                            st.session_state.change_patient_id = erow.patient_id
                            st.session_state.focus_patient_id = erow.patient_id
                            ack_wait_event(erow.patient_id)
                        st.rerun()
                with e2:
                    if st.button(
                        "Dismiss",
                        use_container_width=True,
                        disabled=not can_act,
                        key=f"ev_dismiss_{ev.get('id')}",
                    ):
                        ack_wait_event(ev.get("id"))
                        st.session_state.banner = (
                            f"Alert cleared for {ev_name}. Still on the list — "
                            "Change if you disagree with the suggestion."
                        )
                        st.rerun()
                with e3:
                    if st.button(
                        "Elsewhere",
                        use_container_width=True,
                        disabled=not can_act or erow is None,
                        key=f"ev_else_{ev.get('id')}",
                        help="Taken to a bay/EMS without DoorBoard",
                    ):
                        if erow:
                            mark_taken_elsewhere(
                                erow, "Taken elsewhere — alert cleared"
                            )
                        st.rerun()

        if pending:
            if st.button("Clear all alerts", use_container_width=True, key="clr_alerts"):
                for e in list(pending):
                    if e.get("kind") == "wait":
                        ack_wait_event(e.get("id"))
                    else:
                        dismiss_events_for(e.get("id"))
                st.session_state.banner = "All floor alerts cleared."
                st.rerun()

        if not rows:
            st.caption("No one waiting.")
        else:
            top = rows[0]
            st.html(
                f'<div class="ed-side" style="border:none;box-shadow:none;padding:0;'
                f'max-height:none;overflow:visible;background:transparent;">'
                f"{next_card_html(top, baseline, redact)}</div>"
            )
            can_send = space_is_open(top.space) and can_act
            s1, s2, s3 = st.columns(3)
            with s1:
                if st.button(
                    "Send",
                    use_container_width=True,
                    type="primary",
                    disabled=not can_send,
                    key=f"send_{top.patient_id}",
                ):
                    send_patient(top)
                    st.rerun()
            with s2:
                if st.button(
                    "Hold",
                    use_container_width=True,
                    disabled=not can_act,
                    key=f"hold_{top.patient_id}",
                ):
                    hold_patient(top)
                    st.rerun()
            with s3:
                if st.button(
                    "Change",
                    use_container_width=True,
                    disabled=not can_act,
                    key=f"chg_{top.patient_id}",
                ):
                    st.session_state.change_patient_id = top.patient_id
                    st.session_state.focus_patient_id = top.patient_id
                    st.session_state.show_chart = False
                    st.session_state.show_add_form = False
                    st.rerun()
            if not space_is_open(top.space):
                st.caption(f"No open slot — {space_plain(top.space)}.")

            if len(rows) > 1:
                st.markdown('<p class="desk-h">Then</p>', unsafe_allow_html=True)
                for r in rows[1:3]:
                    label = "Patient" if redact else _short_name(r.name)
                    st.markdown(
                        f'<div class="then-row"><div><strong>#{r.order} {html.escape(label)}</strong>'
                        f'<div class="meta">{html.escape(space_plain(r.space))}</div></div></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        f"Send {_short_name(r.name)}"
                        if space_is_open(r.space)
                        else f"Hold {_short_name(r.name)}",
                        use_container_width=True,
                        disabled=not can_act,
                        key=f"send2_{r.patient_id}",
                    ):
                        if space_is_open(r.space):
                            send_patient(r)
                        else:
                            hold_patient(r)
                        st.rerun()

        if st.session_state.get("auto_watch_note"):
            st.caption(st.session_state.auto_watch_note)

        # Floor: brand only. Demo: coverage / assumptions from board_parts.
        st.html(next_html)

with mid:
    st.radio(
        "List filter",
        options=["all", "eye"],
        format_func=lambda x: (
            f"All waiting ({len(rows)})"
            if x == "all"
            else f"Needs your eye ({len(eye_rows)})"
        ),
        horizontal=True,
        key="list_filter",
        label_visibility="collapsed",
    )

    # Act on flagged people here — not buried under Next up
    if st.session_state.list_filter == "eye":
        with st.container(border=True):
            st.markdown(
                f'<p class="desk-h">Act on flagged · {len(eye_rows)}</p>',
                unsafe_allow_html=True,
            )
            if not eye_groups:
                st.caption("No one flagged right now.")
            for key, title, hint, group in eye_groups:
                st.markdown(f"**{title}** · {len(group)}")
                st.markdown(
                    f'<p class="eye-hint">{html.escape(hint)}</p>',
                    unsafe_allow_html=True,
                )
                for r in group[:5]:
                    label = "Patient" if redact else _short_name(r.name)
                    g1, g2 = st.columns([2.2, 1])
                    with g1:
                        st.caption(
                            f"#{r.order} · {label} · {space_plain(r.space)}"
                        )
                    with g2:
                        if key == "worse":
                            if st.button(
                                "Accept",
                                use_container_width=True,
                                disabled=not can_act,
                                key=f"eye_acc_{r.patient_id}",
                            ):
                                send_patient(r, reason="Accepted — getting worse")
                                st.rerun()
                        elif key in ("wait", "missing"):
                            if st.button(
                                "Chart",
                                use_container_width=True,
                                key=f"eye_ch_{r.patient_id}",
                            ):
                                st.session_state.detail_patient_id = r.patient_id
                                st.session_state.show_chart = True
                                st.session_state.show_add_form = False
                                st.rerun()
                        else:
                            if st.button(
                                "Change",
                                use_container_width=True,
                                disabled=not can_act,
                                key=f"eye_cg_{r.patient_id}",
                            ):
                                st.session_state.change_patient_id = r.patient_id
                                st.session_state.focus_patient_id = r.patient_id
                                st.session_state.show_chart = False
                                st.rerun()
    elif eye_rows:
        st.caption(
            f"{len(eye_rows)} need your eye — switch filter above to act without hunting the list."
        )

    st.html(list_html)

with right:
    role_label = "Charge nurse" if can_act else "View only"
    seal_label = (
        "Confirmed — not in chart"
        if st.session_state.sealed
        else "Suggested — not in chart"
    )

    # Side panel: chart / new arrival (replaces desk while open)
    if st.session_state.get("show_add_form"):
        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            with h1:
                st.markdown(
                    '<p class="desk-title">New arrival</p>',
                    unsafe_allow_html=True,
                )
            with h2:
                if st.button("Close", key="close_add", use_container_width=True):
                    st.session_state.show_add_form = False
                    st.rerun()
            st.caption("Required below · optional vitals · not in EHR")
            a_name = st.text_input(
                "Name *", key="add_name", placeholder="Family, Given"
            )
            a1, a2 = st.columns(2)
            with a1:
                a_age = st.number_input(
                    "Age *", min_value=0, max_value=120, value=40, key="add_age"
                )
            with a2:
                a_esi = st.selectbox(
                    "ESI *", options=[1, 2, 3, 4, 5], index=2, key="add_esi"
                )
            a_cc = st.text_input(
                "Complaint *", key="add_cc", placeholder="Chief complaint"
            )
            a_arr = st.selectbox("Arrival *", options=ARRIVAL_OPTS, key="add_arr")
            a_look = st.text_input(
                "How they look", key="add_look", placeholder="Optional"
            )
            with st.expander("Optional vitals", expanded=False):
                av1, av2 = st.columns(2)
                with av1:
                    a_hr = st.text_input("HR", key="add_hr")
                    a_rr = st.text_input("RR", key="add_rr")
                    a_sbp = st.text_input("BP (systolic)", key="add_sbp")
                with av2:
                    a_spo2 = st.text_input("SpO2", key="add_spo2")
                    a_temp = st.text_input("Temp °C", key="add_temp")
            if st.button(
                "Add to door list",
                use_container_width=True,
                disabled=not can_act,
                type="primary",
                key="btn_add_arrival",
            ):
                if not can_act:
                    st.error("View-only — you can’t add arrivals.")
                elif not (a_name or "").strip() or not (a_cc or "").strip():
                    st.error("Name and complaint are required.")
                else:
                    try:
                        new_id = next_patient_id(st.session_state.patients)
                        fields = {
                            "name": a_name,
                            "age": a_age,
                            "complaint": a_cc,
                            "esi": a_esi,
                            "arrival": a_arr,
                            "look": a_look,
                            "hr": _parse_optional_num(a_hr),
                            "spo2": _parse_optional_num(a_spo2),
                            "sbp": _parse_optional_num(a_sbp),
                            "temp_c": _parse_optional_num(a_temp),
                            "rr": _parse_optional_num(a_rr),
                            "wait_min": 0,
                            "chair": "Intake",
                            "has_prior_record": False,
                            "history_note": "",
                            "consent_doorboard": True,
                            "tags": [],
                        }
                        newbie = patient_from_intake(fields, patient_id=new_id)
                        st.session_state.patients = list(st.session_state.patients) + [
                            newbie
                        ]
                        st.session_state.sealed = False
                        st.session_state.detail_patient_id = new_id
                        st.session_state.focus_patient_id = new_id
                        st.session_state.show_add_form = False
                        st.session_state.show_chart = True
                        st.session_state.banner = (
                            f"New arrival {newbie.name} ({new_id}) on the door list. "
                            "Chart opened. Not in the EHR."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:47",
                                "actor": "charge_nurse",
                                "action": "add_arrival",
                                "patient_id": new_id,
                                "detail": f"Added {newbie.name}; ESI {newbie.esi}",
                                "before": "",
                                "after": new_id,
                                "legal_note": "",
                            }
                        )
                        st.rerun()
                    except ValueError as err:
                        st.error(str(err))
            if not can_act:
                st.caption("View-only: you can’t add arrivals.")

    elif st.session_state.get("show_chart") and st.session_state.get("detail_patient_id"):
        open_id = st.session_state.detail_patient_id
        patient = next(
            (p for p in st.session_state.patients if p.id == open_id), None
        )
        ranked = next((r for r in rows if r.patient_id == open_id), None)
        if patient is None:
            st.session_state.show_chart = False
            st.rerun()
        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            with h1:
                st.markdown(
                    f'<p class="desk-title">Chart · {html.escape(open_id)}</p>',
                    unsafe_allow_html=True,
                )
                st.caption(html.escape(patient.name))
            with h2:
                if st.button("Close", key="close_chart", use_container_width=True):
                    st.session_state.show_chart = False
                    st.rerun()
            if ranked:
                info_bit = f"Info known: {ranked.confidence_label} {ranked.confidence:.0%}"
                if ranked.ml_lifted and ranked.model_prob is not None:
                    info_bit += f" · model p={ranked.model_prob:.2f} (calibrated)"
                st.caption(f"Urgency {ranked.acuity:.0f} · {info_bit} · {ranked.space}")
            c_name = st.text_input(
                "Name *", value=patient.name, key=f"ch_name_{open_id}"
            )
            c1, c2 = st.columns(2)
            with c1:
                c_age = st.number_input(
                    "Age *",
                    min_value=0,
                    max_value=120,
                    value=int(patient.age),
                    key=f"ch_age_{open_id}",
                )
            with c2:
                c_esi = st.selectbox(
                    "ESI *",
                    options=[1, 2, 3, 4, 5],
                    index=max(0, min(4, int(patient.esi) - 1)),
                    key=f"ch_esi_{open_id}",
                )
            c_complaint = st.text_input(
                "Complaint *", value=patient.complaint, key=f"ch_cc_{open_id}"
            )
            arr_idx = (
                ARRIVAL_OPTS.index(patient.arrival)
                if patient.arrival in ARRIVAL_OPTS
                else 0
            )
            c_arrival = st.selectbox(
                "Arrival *",
                options=ARRIVAL_OPTS,
                index=arr_idx,
                key=f"ch_arr_{open_id}",
            )
            c_look = st.text_input(
                "How they look", value=patient.look, key=f"ch_look_{open_id}"
            )
            w1, w2 = st.columns(2)
            with w1:
                c_wait = st.number_input(
                    "Wait (min)",
                    min_value=0,
                    max_value=600,
                    value=int(patient.wait_min),
                    key=f"ch_wait_{open_id}",
                )
            with w2:
                c_chair = st.text_input(
                    "Chair",
                    value=patient.chair,
                    key=f"ch_chair_{open_id}",
                )
            with st.expander("Vitals (optional)", expanded=False):
                v1, v2 = st.columns(2)
                with v1:
                    c_hr = st.text_input(
                        "HR",
                        value=_vital_field(patient.vitals.hr),
                        key=f"ch_hr_{open_id}",
                    )
                    c_rr = st.text_input(
                        "RR",
                        value=_vital_field(patient.vitals.rr),
                        key=f"ch_rr_{open_id}",
                    )
                    c_sbp = st.text_input(
                        "BP (systolic)",
                        value=_vital_field(patient.vitals.sbp),
                        key=f"ch_sbp_{open_id}",
                    )
                with v2:
                    c_spo2 = st.text_input(
                        "SpO2",
                        value=_vital_field(patient.vitals.spo2),
                        key=f"ch_spo2_{open_id}",
                    )
                    c_temp = st.text_input(
                        "Temp °C",
                        value=_vital_field(patient.vitals.temp_c),
                        key=f"ch_temp_{open_id}",
                    )
            with st.expander("History & flags", expanded=False):
                c_prior = st.checkbox(
                    "Prior record on file",
                    value=patient.has_prior_record,
                    key=f"ch_prior_{open_id}",
                )
                c_hist = st.text_input(
                    "History note",
                    value=patient.history_note,
                    key=f"ch_hist_{open_id}",
                )
                c_lang = st.checkbox(
                    "Language barrier",
                    value=patient.language_barrier,
                    key=f"ch_lang_{open_id}",
                )
                c_bounce = st.checkbox(
                    "Bounceback <72h",
                    value=patient.bounceback_72h,
                    key=f"ch_bounce_{open_id}",
                )
                c_iso = st.checkbox(
                    "Needs isolation",
                    value=patient.isolation_needed,
                    key=f"ch_iso_{open_id}",
                )
                c_preg = st.checkbox(
                    "Pregnant", value=patient.pregnant, key=f"ch_preg_{open_id}"
                )
                c_intox = st.checkbox(
                    "Intoxicated",
                    value=patient.intoxicated,
                    key=f"ch_intox_{open_id}",
                )
                c_consent = st.checkbox(
                    "Consent for DoorBoard",
                    value=patient.consent_doorboard,
                    key=f"ch_consent_{open_id}",
                )
            with st.expander("Pathway", expanded=False):
                pw = (
                    patient.pathway
                    if patient.pathway in PATHWAY_OPTS
                    else (patient.pathway if patient.pathway else "(none)")
                )
                if pw not in PATHWAY_OPTS:
                    pw = "(none)"
                c_path = st.selectbox(
                    "Pathway",
                    options=PATHWAY_OPTS,
                    index=PATHWAY_OPTS.index(pw),
                    key=f"ch_path_{open_id}",
                )
                c_pmin = st.number_input(
                    "Pathway clock (min)",
                    min_value=0,
                    max_value=600,
                    value=int(patient.pathway_min or 0),
                    key=f"ch_pmin_{open_id}",
                )
            if st.button(
                "Save chart",
                use_container_width=True,
                disabled=not can_act,
                type="primary",
                key=f"ch_save_{open_id}",
            ):
                if not can_act:
                    st.error("View-only — you can’t save the chart.")
                elif not c_name.strip() or not c_complaint.strip():
                    st.error("Name and complaint are required.")
                else:
                    try:
                        fields = {
                            "name": c_name,
                            "age": c_age,
                            "complaint": c_complaint,
                            "esi": c_esi,
                            "arrival": c_arrival,
                            "look": c_look,
                            "wait_min": c_wait,
                            "chair": c_chair,
                            "hr": _parse_optional_num(c_hr),
                            "spo2": _parse_optional_num(c_spo2),
                            "sbp": _parse_optional_num(c_sbp),
                            "temp_c": _parse_optional_num(c_temp),
                            "rr": _parse_optional_num(c_rr),
                            "has_prior_record": c_prior,
                            "history_note": c_hist,
                            "language_barrier": c_lang,
                            "bounceback_72h": c_bounce,
                            "isolation_needed": c_iso,
                            "pregnant": c_preg,
                            "intoxicated": c_intox,
                            "consent_doorboard": c_consent,
                            "pathway": c_path,
                            "pathway_min": c_pmin,
                            "tags": list(patient.tags),
                        }
                        st.session_state.patients = apply_chart_update(
                            st.session_state.patients, open_id, fields
                        )
                        st.session_state.sealed = False
                        st.session_state.banner = (
                            f"Chart saved for {c_name.strip()}. List re-ranked. "
                            "Still not written to the EHR."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:46",
                                "actor": "charge_nurse",
                                "action": "chart_save",
                                "patient_id": open_id,
                                "detail": "Chart updated; optional fields may be blank",
                                "before": "",
                                "after": "saved",
                                "legal_note": "",
                            }
                        )
                        st.rerun()
                    except ValueError as err:
                        st.error(str(err))
            if not can_act:
                st.caption("View-only: chart is visible, not editable.")

    else:
        pend = st.session_state.get("pending_safety_override")
        change_id = st.session_state.get("change_patient_id")
        change_row = next((r for r in rows if r.patient_id == change_id), None)
        desk_focus = bool(pend) or change_row is not None

        with st.container(border=True):
            st.markdown('<p class="desk-title">Your desk</p>', unsafe_allow_html=True)
            if not desk_focus:
                st.markdown(
                    '<p class="desk-sub"><strong>You decide.</strong> Send the next person. '
                    "Nothing goes in the chart until you confirm that one.</p>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<p class="desk-status"><strong>{role_label}</strong> · Chart write: off · '
                f"{seal_label} · {len(rows)} waiting · {len(fills)} sent</p>",
                unsafe_allow_html=True,
            )
            if desk_focus:
                st.caption(
                    "Finish the task below — Confirm / Floor events are folded under expanders."
                )
            else:
                audit_acts = st.session_state.get("audit") or []
                n_send = sum(1 for a in audit_acts if a.get("action") == "send")
                sess_m = max(
                    0,
                    int(
                        (
                            time.time()
                            - float(
                                st.session_state.get("session_started_ts") or time.time()
                            )
                        )
                        / 60
                    ),
                )
                over_n = len(
                    [
                        pid
                        for pid in patients_over_safe_wait(st.session_state.patients)
                        if pid not in off
                    ]
                )
                st.markdown(
                    f'<div class="habit-strip">'
                    f'<div class="h"><div class="k">Confirmed</div><div class="v">{n_send}</div></div>'
                    f'<div class="h"><div class="k">Session</div><div class="v">{sess_m}m</div></div>'
                    f'<div class="h"><div class="k">Over wait</div><div class="v">{over_n}</div></div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                _rp = replay_numbers()
                st.caption(
                    f"Impact: unstable {_rp['gain_vs_esi_min']:.1f} min sooner vs ESI-only · "
                    f"mild +{_rp['mild_cost_vs_fifo_min']:.0f} min on purpose."
                )

            if pend:
                st.warning(
                    f"**Safety override — {_short_name(pend['name'])}**\n\n"
                    "DoorBoard is holding this patient because a reading it needs is "
                    "missing. Moving them down clears that hold. This is recorded as a "
                    "safety override with your ID, the time, the old recommendation and "
                    "your reason."
                )
                pc1, pc2 = st.columns(2)
                if pc1.button(
                    "Confirm and record", use_container_width=True, key="safe_ok"
                ):
                    prow = next(
                        (r for r in rows if r.patient_id == pend["patient_id"]), None
                    )
                    if prow is not None:
                        hold_patient(
                            prow,
                            pend["reason"],
                            pend["reason_code"],
                            confirmed_safety=True,
                        )
                    st.rerun()
                if pc2.button(
                    "Keep the hold", use_container_width=True, key="safe_no"
                ):
                    st.session_state.pending_safety_override = None
                    st.session_state.banner = "Hold kept. Nothing recorded."
                    st.rerun()

            if change_row is not None:
                st.markdown(
                    f'<p class="desk-h">Change · {html.escape(_short_name(change_row.name))}</p>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Now: #{change_row.order} · {space_plain(change_row.space)}"
                )
                act = st.radio(
                    "What do you want?",
                    options=["keep", "up", "send", "elsewhere"],
                    format_func=lambda x: {
                        "keep": "Keep in chairs (disagree with suggestion)",
                        "up": "Move up",
                        "send": "Send to a different space",
                        "elsewhere": "Taken elsewhere (not via DoorBoard)",
                    }[x],
                    key="change_act",
                )
                dest = None
                if act == "send":
                    dest = st.selectbox(
                        "Space",
                        options=["Bay open", "Resus", "Chair", "Isolation open"],
                        key="change_dest",
                    )
                reason = st.text_input(
                    "Your reason (required)",
                    key="override_box",
                    placeholder="e.g. Walking, talking — keep in chairs",
                )
                c_ok, c_x = st.columns(2)
                with c_ok:
                    if st.button(
                        "Apply",
                        use_container_width=True,
                        type="primary",
                        disabled=not can_act,
                        key="btn_override",
                    ):
                        if not reason.strip() and act != "elsewhere":
                            st.error("Please add a short reason (stays on the board).")
                        elif act == "keep":
                            hold_patient(change_row, reason.strip())
                            st.rerun()
                        elif act == "up":
                            move_patient_up(change_row, reason.strip())
                            st.rerun()
                        elif act == "elsewhere":
                            mark_taken_elsewhere(
                                change_row,
                                reason.strip()
                                or "Taken elsewhere — not via DoorBoard",
                            )
                            st.rerun()
                        else:
                            change_row.space = dest
                            if space_is_open(dest):
                                send_patient(change_row, reason=reason.strip())
                            else:
                                hold_patient(change_row, reason.strip())
                            st.rerun()
                with c_x:
                    if st.button(
                        "Cancel", use_container_width=True, key="change_cancel"
                    ):
                        st.session_state.change_patient_id = None
                        st.rerun()
                st.caption(
                    "Disagree → Keep or Move up. "
                    "Sent outside DoorBoard → Taken elsewhere."
                )

            confirm_box = (
                st.expander("Confirm / handoff", expanded=False)
                if desk_focus
                else nullcontext()
            )
            with confirm_box:
                if desk_focus:
                    st.markdown(
                        '<p class="desk-h">1 · Confirm next</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<p class="desk-h">1 · Confirm next</p>',
                        unsafe_allow_html=True,
                    )
                if rows:
                    nxt = rows[0]
                    send_ok = space_is_open(nxt.space) and can_act
                    if st.button(
                        f"Confirm & send {_short_name(nxt.name)} → {space_plain(nxt.space)}",
                        type="primary",
                        use_container_width=True,
                        disabled=not send_ok,
                        key="btn_confirm_next",
                    ):
                        send_patient(nxt)
                        st.rerun()
                    ready3 = [r for r in rows[:3] if space_is_open(r.space)]
                    if len(ready3) >= 2 and st.button(
                        f"Send next {len(ready3)}",
                        use_container_width=True,
                        disabled=not can_act,
                        key="btn_send_next3",
                    ):
                        for r in ready3:
                            send_patient(r)
                        st.rerun()
                else:
                    st.caption("No one left to send.")

                with st.expander("Handoff — confirm whole list", expanded=False):
                    if st.button(
                        "Confirm this list",
                        use_container_width=True,
                        disabled=st.session_state.sealed or not can_act,
                        key="btn_confirm",
                    ):
                        if not can_act:
                            st.error("View-only mode — you can’t confirm.")
                        else:
                            st.session_state.sealed = True
                            st.session_state.sealed_snapshot = [
                                {
                                    "order": r.order,
                                    "id": r.patient_id,
                                    "name": r.name,
                                    "acuity": r.acuity,
                                    "confidence": r.confidence,
                                    "space": r.space,
                                    "why": r.why,
                                }
                                for r in rows
                            ]
                            st.session_state.banner = (
                                "You confirmed this list for handoff. "
                                "Still not written to the chart."
                            )
                            audit(
                                "confirm",
                                "*",
                                f"Confirmed {len(rows)} waiting; EHR write=false",
                                after="sealed",
                            )
                            st.rerun()

                last_ov = st.session_state.get("last_override")
                if last_ov:
                    st.markdown(
                        f'<div class="before-after">'
                        f"<strong>Before → after</strong> · {html.escape(str(last_ov.get('patient_id','')))}<br/>"
                        f"<span class=\"ba-label\">Before:</span> {html.escape(str(last_ov.get('before') or '—'))}<br/>"
                        f"<span class=\"ba-label\">After:</span> {html.escape(str(last_ov.get('after') or '—'))}<br/>"
                        f"<span class=\"ba-note\">{html.escape(str(last_ov.get('detail') or ''))}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                if not can_act:
                    st.caption(
                        "View-only: you can see the list, not change or confirm it."
                    )

            floor_box = (
                st.expander("Floor events", expanded=False)
                if desk_focus
                else nullcontext()
            )
            with floor_box:
                if not desk_focus:
                    st.markdown(
                        '<p class="desk-h">2 · Floor events</p>',
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "Shows on that person — Accept move or Keep. List re-ranks."
                    )
                if st.button(
                    "Patient getting worse · bay free",
                    use_container_width=True,
                    key="btn_joseph",
                ):
                    st.session_state.patients = mark_vitals_worse(
                        st.session_state.patients, "P02", {"hr": 110, "rr": 20}
                    )
                    for p in st.session_state.patients:
                        if p.id == "P02" and "charge_priority" not in p.tags:
                            p.tags.append("charge_priority")
                    room = copy.deepcopy(st.session_state.room)
                    room.boarders_blocking = 0
                    room.bay_open = max(2, int(room.bay_open or 0))
                    st.session_state.room = room
                    st.session_state.sealed = False
                    st.session_state.focus_patient_id = "P02"
                    st.session_state.show_chart = False
                    st.session_state.show_add_form = False
                    add_floor_event(
                        "P02",
                        "worse",
                        "Getting worse · bay free",
                        "Heart rate rose. Same ESI 3. Accept the move or keep.",
                    )
                    st.session_state.banner = (
                        "Joseph needs your eye — getting worse, bay free. "
                        "Accept or keep on the left."
                    )
                    audit(
                        "reassess",
                        "P02",
                        "Vitals worse HR 88→110; boarders cleared; bay open; list re-ranked",
                    )
                    st.rerun()

                if st.button(
                    "5 minutes passed", use_container_width=True, key="btn_wait"
                ):
                    before = set(
                        patients_over_safe_wait(st.session_state.patients)
                    )
                    st.session_state.patients = tick_waits(
                        st.session_state.patients, 5
                    )
                    st.session_state.last_auto_tick_wall = time.time()
                    newly = [
                        pid
                        for pid in patients_over_safe_wait(
                            st.session_state.patients
                        )
                        if pid not in before
                    ]
                    for pid in newly:
                        p = next(
                            (
                                x
                                for x in st.session_state.patients
                                if x.id == pid
                            ),
                            None,
                        )
                        add_floor_event(
                            pid,
                            "wait",
                            "Wait past safe ceiling",
                            f"Wait {getattr(p, 'wait_min', '?')} min past ESI safe ceiling"
                            if p
                            else "Past safe wait",
                        )
                    st.session_state.sealed = False
                    st.session_state.auto_watch_note = (
                        f"Manual +5 min · {len(newly)} newly over safe wait"
                        if newly
                        else "Manual +5 min · safe-wait check"
                    )
                    st.session_state.banner = (
                        "Five minutes passed. Anyone waiting too long for their ESI is flagged."
                    )
                    st.session_state.audit.append(
                        {
                            "ts": "19:45",
                            "actor": "system",
                            "action": "queue_watch",
                            "patient_id": "*",
                            "detail": "+5 min; safe-wait check",
                            "before": "",
                            "after": "",
                            "legal_note": "",
                        }
                    )
                    st.rerun()
                st.caption(
                    "Also auto every ~2 min on the Floor — one alert at a time."
                )

                if st.button(
                    "Busy night — many more arrivals",
                    use_container_width=True,
                    key="btn_surge",
                ):
                    base_n = len(load_patients())
                    pts, room = simulate_surge(
                        load_patients(),
                        3.0,
                        profile_id=st.session_state.profile_id,
                    )
                    st.session_state.patients = pts
                    st.session_state.room = room
                    st.session_state.shift_mode = "surge"
                    reset_floor_flow()
                    st.session_state.sealed = False
                    st.session_state.banner = (
                        f"Busy night: {len(pts)} people (≥3× of {base_n}). "
                        "We protect the unstable first. Mild waits may grow — on purpose."
                    )
                    st.session_state.audit.append(
                        {
                            "ts": "19:50",
                            "actor": "system",
                            "action": "surge",
                            "patient_id": "*",
                            "detail": f"n={len(pts)}; bay={room.bay_open}",
                            "before": "",
                            "after": "",
                            "legal_note": "",
                        }
                    )
                    st.rerun()

                if not desk_focus:
                    audit = st.session_state.audit
                    if audit:
                        st.markdown(
                            '<p class="desk-h">Recent activity</p>',
                            unsafe_allow_html=True,
                        )
                        for ev in reversed(audit[-3:]):
                            st.caption(
                                f"{ev.get('ts', '')} · {ev.get('action')} · {ev.get('patient_id')} — "
                                f"{(ev.get('detail') or ev.get('after') or '')[:90]}"
                            )

        if is_demo:
            checks = coverage_checks(
                rows,
                st.session_state.patients,
                profile,
                st.session_state.room,
                st.session_state.audit,
                st.session_state.sealed,
            )
            items = "".join(
                f'<li><span class="{"ok" if ok else "no"}">{"✓" if ok else "○"}</span>'
                f"<span>{html.escape(label)}</span>"
                f'<span class="tag">{html.escape(tag)}</span></li>'
                for label, ok, tag in checks
            )
            n_zh_cov = sum(1 for r in rows if not r.has_prior_record)
            hosp_cov = {
                "urban": "Big hospital",
                "district": "District ED",
                "rural": "Smaller ED",
            }.get(profile.id, profile.label)
            st.markdown(
                f'<div class="cover-card">'
                f"<h4>Round 2 coverage</h4>"
                f'<p class="assume-line"><strong>{html.escape(hosp_cov)}</strong> · '
                f"~{profile.visits_per_day}/day · maturity {html.escape(str(profile.maturity))} · "
                f"ESI 1–5 · ~50/50 history · "
                f"{n_zh_cov} first visits · DPDP · chart write off · you confirm</p>"
                f"<ul>{items}</ul></div>",
                unsafe_allow_html=True,
            )

            deg = integrations.get("degraded_mode")
            deg_cls = "deg" if deg else "ok"
            deg_word = "Degraded — first-minute only" if deg else "Adapters mock online"
            st.markdown(
                f'<div class="integ-card"><strong>Integration maturity</strong> · '
                f'{html.escape(integrations.get("maturity_label", ""))}<br/>'
                f'<span class="{deg_cls}">{deg_word}</span><br/>'
                f"Beds: {html.escape(integrations['bed_board']['source'])} · "
                f"Roster: {html.escape(integrations['roster']['source'])}<br/>"
                f"EHR: {html.escape(integrations['ehr']['source'])} · write off<br/>"
                f"Specialty: trauma={'yes' if integrations['specialty']['trauma'] else 'no'} · "
                f"peds={'yes' if integrations['specialty']['peds'] else 'no'} · "
                f"stroke={html.escape(integrations['specialty']['stroke_policy'])}<br/>"
                f"<span style=\"font-size:11px;color:#5f6368\">{html.escape(integrations.get('degraded_note',''))}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                "Simulate EHR outage"
                if not st.session_state.get("ehr_degraded_force")
                else "Restore EHR mock",
                use_container_width=True,
                key="ehr_deg_toggle",
            ):
                st.session_state.ehr_degraded_force = not st.session_state.get(
                    "ehr_degraded_force", False
                )
                log_access(
                    "ehr_degraded_toggle",
                    f"force={st.session_state.ehr_degraded_force}",
                )
                st.session_state.banner = (
                    "EHR mock offline — first-minute facts only. Same ranking engine."
                    if st.session_state.ehr_degraded_force
                    else "EHR mock restored for this profile."
                )
                st.rerun()

            st.markdown(
                '<div class="script-card"><h4>3-minute judge path</h4><ol>'
            "<li>Send next (or Accept on a floor event)</li>"
            "<li>Floor events → Patient getting worse · bay free → Accept move</li>"
            "<li>Wait 45s for auto queue-watch — or press 5 minutes passed</li>"
            "<li>Busy night — many more arrivals (≥3×)</li>"
            "<li>Change on a name → Keep / Move up / Send</li>"
            "<li>Handoff — confirm whole list</li>"
                "<li>Prove it → age twin / weights / privacy</li>"
                "<li>More → Big / District / Smaller ED · maturity panel</li>"
                "<li>More · activity → Download activity log</li>"
                "</ol></div>",
                unsafe_allow_html=True,
            )

            st.markdown('<p class="desk-h">Prove it</p>', unsafe_allow_html=True)
            st.caption("One-click Round 2 proofs — full detail also under More.")
            p1, p2, p3 = st.columns(3)
            with p1:
                if st.button("Same fever, different ages", use_container_width=True, key="prove_age"):
                    st.session_state.show_age_compare = not st.session_state.show_age_compare
                    st.rerun()
            with p2:
                if st.button("Under-reporter weights", use_container_width=True, key="prove_w"):
                    st.session_state.show_weights = not st.session_state.show_weights
                    st.rerun()
            with p3:
                if st.button("Privacy / DPDP", use_container_width=True, key="prove_priv"):
                    st.session_state.show_privacy = not st.session_state.show_privacy
                    st.rerun()

            if st.session_state.show_age_compare:
                st.markdown("**Fever 38.5°C — child vs adult vs older adult**")
                twins = fever_twin_proof(st.session_state.patients, st.session_state.room)
                for t in twins:
                    st.text(
                        f"{t['name']} · {t['age']}y ({t['age_band']}): "
                        f"fever weight {t['temp_points_age_aware']:.0f} "
                        f"(adult-only would use {t['temp_points_adult_only']:.0f}) · "
                        f"urgency {t['acuity_age_aware']:.0f}"
                    )
                st.caption("Same thermometer number. Different urgency by age.")

            if st.session_state.show_weights and rows:
                sample = next((r for r in rows if r.patient_id == "P20"), rows[0])
                st.caption(f"Example — {sample.name} (under-reporter / how pieces were weighed):")
                st.json(sample.input_weights)

            if st.session_state.get("show_privacy"):
                meta = st.session_state.meta
                st.markdown("**Lawful basis — this is not all consent**")
                st.info(
                    "Triage itself runs on DPDP s.7 certain legitimate uses, medical "
                    "emergency limb. Consent is not the gate for scoring a collapsing "
                    "patient, and a refusal cannot switch triage off.\n\n"
                    "Consent under s.6 governs everything past the episode: service "
                    "improvement and model retraining. P17 in this room withheld it. "
                    "P17 is still triaged and still safe. What the refusal removes is "
                    "the retraining use.\n\n"
                    "s.7 removes the consent requirement and nothing else. Purpose "
                    "limitation, retention, security and explainability all still apply."
                )
                st.markdown("**Section 9 — the five children in this room**")
                n_minor = sum(1 for r in rows if r.age < 18)
                st.warning(
                    f"{n_minor} patients here are under 18. s.9 carries the heaviest "
                    "penalties in the Act.\n\n"
                    "- Verifiable guardian consent for any consent-based processing, so "
                    "DoorBoard never uses the consent basis for a child at the door\n"
                    "- No tracking or behavioural monitoring: under-18 records are "
                    "excluded from the feedback corpus that proposes weight changes\n"
                    "- Unaccompanied minor: triage proceeds under s.7, the guardian "
                    "path is deferred and the deferral is itself an audit entry"
                )
                st.caption(
                    "Clinical mirror of the same principle: five APLS age strata, so a "
                    "child is scored as the age they actually are. See Scales panel."
                )
                st.markdown("**What we do not look at**")
                st.caption(
                    "The model input is twelve physiological and operational features. "
                    "No diagnosis history, address, payer status, caste, religion or any "
                    "protected attribute is in the model at all. That is a bias control "
                    "as much as a privacy one. Full DPIA summary in GOVERNANCE.md."
                )
                st.info(
                    f"**Jurisdiction:** {meta.get('jurisdiction')}\n\n"
                    f"**Purpose:** {meta.get('purpose')}\n\n"
                    f"**Keep for:** {meta.get('retention_days_demo')} days\n\n"
                    f"**Chart write:** off in this demo\n\n"
                    f"**Who can change:** charge nurse only\n\n"
                    f"**When you change someone, we keep:** your ID, time, who, old suggestion, "
                    f"new choice, reason\n\n{OVERRIDE_LEGAL_NOTE}"
                )

            with st.expander("More · hospital & shift", expanded=False):
                st.caption("Same door list engine — different building size, specialty, or maturity.")
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Big hospital", use_container_width=True, key="u_prof"):
                        st.session_state.profile_id = "urban"
                        st.session_state.room = room_for(
                            "urban", surge=False, shift_mode=st.session_state.shift_mode
                        )
                        st.session_state.patients = load_patients()
                        reset_floor_flow()
                        st.session_state.ehr_degraded_force = False
                        st.session_state.sealed = False
                        st.session_state.banner = (
                            f"Big hospital · about {get_profile('urban').visits_per_day} visits/day · more bays."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:40",
                                "actor": "system",
                                "action": "profile",
                                "patient_id": "*",
                                "detail": "urban",
                                "before": "",
                                "after": "urban",
                                "legal_note": "",
                            }
                        )
                        st.rerun()
                with c2:
                    if st.button("District ED", use_container_width=True, key="d_prof"):
                        st.session_state.profile_id = "district"
                        st.session_state.room = room_for(
                            "district", surge=False, shift_mode=st.session_state.shift_mode
                        )
                        st.session_state.patients = load_patients()
                        reset_floor_flow()
                        st.session_state.ehr_degraded_force = False
                        st.session_state.sealed = False
                        st.session_state.banner = (
                            f"District ED · about {get_profile('district').visits_per_day}/day · "
                            "mid maturity · stroke stabilize then transfer."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:40",
                                "actor": "system",
                                "action": "profile",
                                "patient_id": "*",
                                "detail": "district",
                                "before": "",
                                "after": "district",
                                "legal_note": "",
                            }
                        )
                        st.rerun()
                with c3:
                    if st.button("Smaller ED", use_container_width=True, key="r_prof"):
                        st.session_state.profile_id = "rural"
                        st.session_state.room = room_for(
                            "rural", surge=False, shift_mode=st.session_state.shift_mode
                        )
                        st.session_state.patients = load_patients()
                        reset_floor_flow()
                        st.session_state.ehr_degraded_force = False
                        st.session_state.sealed = False
                        st.session_state.banner = (
                            "Smaller ED · fewer beds and nurses · same ranking engine."
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:40",
                                "actor": "system",
                                "action": "profile",
                                "patient_id": "*",
                                "detail": "rural",
                                "before": "",
                                "after": "rural",
                                "legal_note": "",
                            }
                        )
                        st.rerun()

                s1, s2, s3 = st.columns(3)
                with s1:
                    if st.button("Quiet", use_container_width=True, key="q_mode"):
                        st.session_state.shift_mode = "quiet"
                        st.session_state.room = room_for(
                            st.session_state.profile_id, surge=False, shift_mode="quiet"
                        )
                        st.session_state.sealed = False
                        st.session_state.banner = "Quiet shift — gentler “being careful” boost."
                        st.rerun()
                with s2:
                    if st.button("Steady", use_container_width=True, key="n_mode"):
                        st.session_state.shift_mode = "normal"
                        st.session_state.room = room_for(
                            st.session_state.profile_id, surge=False, shift_mode="normal"
                        )
                        st.session_state.sealed = False
                        st.session_state.banner = "Steady shift."
                        st.rerun()
                with s3:
                    if st.button("Busy feel", use_container_width=True, key="sg_mode"):
                        st.session_state.shift_mode = "surge"
                        room = room_for(
                            st.session_state.profile_id, surge=False, shift_mode="surge"
                        )
                        room.bay_open = max(1, room.bay_open // 2)
                        room.chair_open = max(2, room.chair_open // 2)
                        room.surge = True
                        st.session_state.room = room
                        st.session_state.sealed = False
                        st.session_state.banner = (
                            "Busy feel — stronger “being careful” when info is thin; fewer free spaces."
                        )
                        st.rerun()

                if st.button("Fewer nurses on the floor", use_container_width=True):
                    st.session_state.room = apply_staffing_shock(st.session_state.room, 3)
                    st.session_state.shift_mode = "surge"
                    st.session_state.sealed = False
                    st.session_state.banner = (
                        f"Staffing drop: {st.session_state.room.nurses_on_floor} nurses left. "
                        "Tighter space. Being careful stays on."
                    )
                    st.session_state.audit.append(
                        {
                            "ts": "19:48",
                            "actor": "system",
                            "action": "staffing_shock",
                            "patient_id": "*",
                            "detail": f"nurses→{st.session_state.room.nurses_on_floor}",
                            "before": "",
                            "after": "",
                            "legal_note": "",
                        }
                    )
                    st.rerun()

                if st.button("Save for handoff", use_container_width=True):
                    handoff = [
                        {
                            "order": r.order,
                            "id": r.patient_id,
                            "esi": r.esi,
                            "acuity": r.acuity,
                            "confidence": r.confidence,
                            "space": r.space,
                            "pathway": r.pathway,
                            "lwbs": r.lwbs_risk,
                            "why": r.why,
                        }
                        for r in rows
                    ]
                    st.session_state.sealed_snapshot = handoff
                    st.session_state.audit.append(
                        {
                            "ts": "19:55",
                            "actor": "charge_nurse",
                            "action": "handoff",
                            "patient_id": "*",
                            "detail": f"Handoff snapshot · {len(handoff)} patients",
                            "before": "",
                            "after": "handoff_ready",
                            "legal_note": "For oncoming charge — not a diagnosis note.",
                        }
                    )
                    st.session_state.banner = (
                        "Handoff saved for the next charge nurse. Download below if you need a file."
                    )
                    st.rerun()

                if st.button("Show yesterday’s proof", use_container_width=True):
                    st.session_state.replay = True
                    st.session_state.banner = (
                        "We shorten dangerous waits, not every wait. Mild waits can grow — we say that out loud."
                    )
                    st.rerun()
                if st.session_state.replay:
                    _rp = replay_numbers()
                    _ev = eval_numbers()["normal"]
                    a, b = st.columns(2)
                    a.metric("vs ESI-only order", f"{_rp['gain_vs_esi_min']:.1f} min sooner")
                    b.metric("vs first-come-first-served", f"{_rp['gain_vs_fifo_min']:.1f} min sooner")
                    c, d = st.columns(2)
                    c.metric("Under-triage", f"{_ev['under_triage_pct']:.0f}%")
                    d.metric("Mild waits", f"+{_rp['mild_cost_vs_fifo_min']:.0f} min")
                    st.caption(_rp["assumption"])

                st.caption(profile.specialty_note)
                st.caption(
                    f"Beds: {integrations['bed_board']['source']} · "
                    f"Roster: {integrations['roster']['source']} · "
                    f"Chart write: off"
                )

            with st.expander("Evidence · how we know it works", expanded=False):
                ev = eval_numbers()
                n1, n2, n3 = st.columns(3)
                n1.metric("Under-triage", f"{ev['normal']['under_triage_pct']:.1f}%",
                          help="assigned less urgent than the reference tier")
                n2.metric("Over-triage", f"{ev['normal']['over_triage_pct']:.1f}%",
                          help="the price we pay for the line above")
                n3.metric("Critical sensitivity", f"{ev['normal']['critical_sensitivity']:.0f}%",
                          help="reference tier 1-2 patients not missed")
                st.caption(ev["normal"]["note"])

                st.markdown("**Under surge (3x volume, shifted acuity mix)**")
                s1, s2, s3 = st.columns(3)
                s1.metric("Under-triage", f"{ev['surge']['under_triage_pct']:.1f}%")
                s2.metric("Over-triage", f"{ev['surge']['over_triage_pct']:.1f}%")
                s3.metric("Critical missed", ev["surge"]["critical_missed"])
                st.caption(
                    "Surge buys more over-triage to hold under-triage at zero. "
                    "That is the asymmetric-cost choice, priced."
                )

                if st.button("Confusion matrix", use_container_width=True, key="ev_cm"):
                    st.session_state.show_cm = not st.session_state.get("show_cm", False)
                    st.rerun()
                if st.session_state.get("show_cm"):
                    import pandas as pd
                    g = ev["matrix"]["grid"]
                    df = pd.DataFrame(
                        [[g[r][c] for c in range(1, 6)] for r in range(1, 6)],
                        index=[f"reference {r}" for r in range(1, 6)],
                        columns=[f"assigned {c}" for c in range(1, 6)],
                    )
                    st.dataframe(df, use_container_width=True)
                    st.caption("Anything below the diagonal is under-triage. That triangle is empty.")

                if st.button("Threshold sweep (the tradeoff)", use_container_width=True, key="ev_sw"):
                    st.session_state.show_sweep = not st.session_state.get("show_sweep", False)
                    st.rerun()
                if st.session_state.get("show_sweep"):
                    import pandas as pd
                    sw = pd.DataFrame(ev["sweep"]).set_index("tier2_cutoff")
                    st.line_chart(sw[["under_pct", "over_pct"]])
                    st.caption(
                        "We ship the leftmost cutoff where under-triage is zero. "
                        "Moving right buys accuracy and starts missing critical patients."
                    )
                    st.dataframe(
                        sw[sw["shipped"]][["under_pct", "over_pct", "exact_pct", "critical_missed"]],
                        use_container_width=True,
                    )

                st.markdown("**Speed**")
                lat = ev["latency"]
                l1, l2 = st.columns(2)
                l1.metric("Per patient", f"{lat['score_ms_per_patient']:.2f} ms")
                l2.metric(f"Re-rank {lat['n']} patients", f"{lat['rank_ms_median']:.0f} ms")
                st.caption(lat["note"])

            with st.expander("Model · calibrated second opinion", expanded=False):
                st.caption(
                    "A gradient-boosted model predicts P(critical within 4h) beside the "
                    "rules. It can raise someone the rules under-rate. It can never lower "
                    "anyone. If it is wrong in the unsafe direction, the rules floor holds."
                )
                mstate = ml.model_state()
                if not mstate.get("available"):
                    st.warning(
                        "scikit-learn not installed, running rules-only. "
                        "pip install -r requirements.txt to enable the model layer."
                    )
                else:
                    m1, m2, m3 = st.columns(3)
                    m1.metric("AUC (held out)", mstate["auc"])
                    m2.metric("Brier, calibrated", mstate["brier_calibrated"])
                    m3.metric("Brier, uncalibrated", mstate["brier_raw"])
                    st.caption(
                        f"n_train {mstate['n_train']} · n_calibrate {mstate['n_calib']} · "
                        f"n_test {mstate['n_test']} · outcome prevalence {mstate['prevalence']}"
                    )
                    if st.button("Reliability curve", use_container_width=True, key="ml_rel"):
                        st.session_state.show_rel = not st.session_state.get("show_rel", False)
                        st.rerun()
                    if st.session_state.get("show_rel"):
                        import pandas as pd
                        rel = pd.DataFrame(mstate["reliability"]).set_index("bin")
                        st.bar_chart(rel[["predicted", "observed"]])
                        st.caption(
                            "Predicted vs observed by decile. This is what lets us say a "
                            "0.7 means roughly 70 percent, which the information score "
                            "never could."
                        )
                    lifted = [r for r in rows if r.ml_lifted]
                    if lifted:
                        st.markdown("**Model raised these patients above the rules**")
                        for r in lifted:
                            st.text(f"{r.name} ({r.age}y) — model p={r.model_prob:.2f} → moved up")
                    else:
                        st.caption("On this room the model agrees with the rules. Nobody lifted.")
                    st.markdown("**Where the training data came from**")
                    st.code(mstate["cohort"], language="text")
                    st.warning(
                        "Synthetic cohort. The model has never seen a real patient, so these "
                        "numbers demonstrate the pipeline, not clinical performance. "
                        "Swapping in MIMIC-IV-ED is a change to ml.load_cohort()."
                    )

            with st.expander("Scales · where the numbers come from", expanded=False):
                prov = scale_provenance()
                st.markdown(
                    f"- Adults: **{prov['adult']}**\n"
                    f"- Under 18: **{prov['pediatric']}**\n"
                    f"- Severity frame: **{prov['triage_frame']}**"
                )
                st.markdown("**Where we deviate, and why**")
                for d in prov["deviations"]:
                    st.text("- " + d)
                if st.button("The pediatric band bug we found", use_container_width=True, key="peds_bug"):
                    st.session_state.show_peds = not st.session_state.get("show_peds", False)
                    st.rerun()
                if st.session_state.get("show_peds"):
                    st.caption(
                        "Round 1 used one 0-17 pediatric band. That is the same class of "
                        "error the brief warns about, one level down: a 16 year old was "
                        "carrying infant thresholds. Five APLS strata fix it."
                    )
                    import pandas as pd
                    st.dataframe(pd.DataFrame(peds_band_proof(st.session_state.patients)),
                                 use_container_width=True)

            with st.expander("Feedback · what overrides changed", expanded=False):
                events = st.session_state.get("audit_events", [])
                rows_by_id = {r.patient_id: r for r in rows}
                st.caption(
                    "Overrides are not just logged. They are counted and turned into "
                    "proposals a named clinical owner accepts or rejects below — that "
                    "decision is itself an audit event. Nothing retunes a weight "
                    "automatically, because a tool that quietly learns from the "
                    "busiest shift will drift and nobody will be able to say why. "
                    "Applying an accepted proposal is a deliberate code change made "
                    "by an engineer after review, not something this build does for you."
                )
                f1, f2 = st.columns(2)
                f1.metric("Overrides this session",
                          sum(1 for e in events if getattr(e, "action", "") == "override"))
                f2.metric("Safety-critical", fb.safety_override_count(events))
                props = fb.proposals(events, rows_by_id)
                reviewed = st.session_state.setdefault("proposal_reviews", {})
                if props:
                    for pr in props:
                        key = f"{pr['signal']}::{pr['direction']}"
                        decision = reviewed.get(key)
                        st.info(f"{pr['text']}\n\nStatus: {decision or pr['status']}")
                        if decision is None:
                            pa, pr_, pc = st.columns(3)
                            if pa.button("Accept for review", key=f"acc_{key}", use_container_width=True):
                                reviewed[key] = "accepted for clinical review — no weight changed automatically"
                                audit(
                                    "proposal_review", f"signal:{pr['signal']}",
                                    f"Accepted: {pr['text']}", after="accepted",
                                )
                                st.rerun()
                            if pr_.button("Reject", key=f"rej_{key}", use_container_width=True):
                                reviewed[key] = "rejected by clinical owner"
                                audit(
                                    "proposal_review", f"signal:{pr['signal']}",
                                    f"Rejected: {pr['text']}", after="rejected",
                                )
                                st.rerun()
                            pc.caption("Either choice is logged. Nothing here edits a weight.")
                        else:
                            st.caption(f"Decision recorded: {decision}")
                else:
                    st.caption(
                        f"No proposal yet. We need at least {fb.MIN_EVIDENCE} overrides "
                        "carrying the same signal before suggesting a weight change."
                    )

            with st.expander("More · safety proofs", expanded=False):
                st.caption(
                    "When we’re unsure, we move people up — not down. Missing a critical case is worse."
                )
                st.caption(asymmetric_cost_statement())
                if st.button("Same fever, different ages", use_container_width=True):
                    st.session_state.show_age_compare = not st.session_state.show_age_compare
                    st.rerun()
                if st.session_state.show_age_compare:
                    st.markdown("**Fever 38.5°C — child vs adult vs older adult**")
                    twins = fever_twin_proof(st.session_state.patients, st.session_state.room)
                    for t in twins:
                        st.text(
                            f"{t['name']} · {t['age']}y ({t['age_band']}): "
                            f"fever weight {t['temp_points_age_aware']:.0f} "
                            f"(adult-only would use {t['temp_points_adult_only']:.0f}) · "
                            f"urgency {t['acuity_age_aware']:.0f}"
                        )
                    st.caption("Same thermometer number. Different urgency by age.")
                    diffs = age_aware_vs_adult_only(
                        st.session_state.patients, st.session_state.room
                    )
                    for d in diffs[:5]:
                        st.text(
                            f"{d['name']} ({d['age']}y): age-aware {d['age_aware']:.0f} vs "
                            f"adult-only {d['adult_only']:.0f} ({d['delta']:+.0f})"
                        )

                if st.button("What went into the score", use_container_width=True):
                    st.session_state.show_weights = not st.session_state.show_weights
                    st.rerun()
                if st.session_state.show_weights and rows:
                    sample = next((r for r in rows if r.patient_id == "P20"), rows[0])
                    st.caption(f"Example — {sample.name} (how pieces of info were weighed):")
                    st.json(sample.input_weights)

                if st.button("How DoorBoard decides", use_container_width=True):
                    st.session_state.show_decision = not st.session_state.show_decision
                    st.rerun()
                if st.session_state.show_decision:
                    st.info(
                        "1. Age-aware vitals, complaint, and how they look\n\n"
                        "2. History, pathways, bouncebacks when known\n\n"
                        "3. Rank the whole waiting room against open space\n\n"
                        "4. Show how sure we are\n\n"
                        "5. If unsure — be careful (move up, never quietly down)"
                    )

                if st.button("Our demo assumptions", use_container_width=True):
                    st.session_state.show_assumptions = not st.session_state.show_assumptions
                    st.rerun()
                if st.session_state.get("show_assumptions"):
                    meta = st.session_state.meta
                    st.info(
                        f"**Visits/day:** urban {get_profile('urban').visits_per_day} · "
                        f"district {get_profile('district').visits_per_day} · "
                        f"rural {get_profile('rural').visits_per_day}\n\n"
                        f"**Scale:** ESI 1–5, then DoorBoard ranks within ties\n\n"
                        f"**Maturity:** high / mid / low adapters (FHIR · HL7+CSV · paper)\n\n"
                        f"**Records on file:** {meta.get('prior_record_mix')}\n\n"
                        f"**Rules:** {meta.get('jurisdiction')}\n\n"
                        f"**Keep data:** {meta.get('retention_days_demo')} days · "
                        f"{meta.get('purpose')}\n\n"
                        f"**People on this board:** {len(st.session_state.patients)} (demo only)"
                    )

            with st.expander("More · privacy & access", expanded=False):
                meta = st.session_state.meta
                retain = int(meta.get("retention_days_demo") or 30)
                started = float(st.session_state.get("session_started_ts") or time.time())
                age_h = max(0, (time.time() - started) / 3600)
                st.markdown(
                    f"**Purpose lock:** {meta.get('purpose')}\n\n"
                    f"**Jurisdiction:** {meta.get('jurisdiction')}\n\n"
                    f"**Retention:** {retain} days demo · session age {age_h:.1f}h · "
                    "audit events older than retention are purged on open (demo)."
                )
                # Enforce retention on access_log / audit for demo
                max_keep = max(20, retain)  # demo: keep last N by "days" mapped to count
                if len(st.session_state.get("access_log") or []) > max_keep:
                    st.session_state.access_log = st.session_state.access_log[-max_keep:]
                if len(st.session_state.get("audit") or []) > max_keep * 2:
                    st.session_state.audit = st.session_state.audit[-(max_keep * 2) :]

                p1, p2 = st.columns(2)
                with p1:
                    if st.button("Privacy details", use_container_width=True):
                        st.session_state.show_privacy = not st.session_state.show_privacy
                        log_access("view_privacy", "Opened purpose / retention panel")
                        st.rerun()
                with p2:
                    if st.button(
                        "Hide names" if not st.session_state.get("redact_phi") else "Show names",
                        use_container_width=True,
                    ):
                        st.session_state.redact_phi = not st.session_state.get("redact_phi", False)
                        log_access(
                            "redact_toggle",
                            f"redact={st.session_state.redact_phi}",
                        )
                        st.session_state.audit.append(
                            {
                                "ts": "19:43",
                                "actor": st.session_state.get("role", "charge_nurse"),
                                "action": "redact_toggle",
                                "patient_id": "*",
                                "detail": f"redact={st.session_state.redact_phi}",
                                "before": "",
                                "after": str(st.session_state.redact_phi),
                                "legal_note": "Hide names for screenshots / shared screens.",
                            }
                        )
                        st.rerun()

                r1, r2 = st.columns(2)
                with r1:
                    if st.button("I am charge nurse", use_container_width=True):
                        apply_account("charge_nurse")
                        log_access("role_switch", "charge_nurse")
                        st.rerun()
                with r2:
                    if st.button("View only", use_container_width=True):
                        apply_account("viewer")
                        log_access("role_switch", "viewer")
                        st.session_state.audit.append(
                            {
                                "ts": "19:44",
                                "actor": "system",
                                "action": "rbac",
                                "patient_id": "*",
                                "detail": "viewer — cannot change or confirm",
                                "before": "",
                                "after": "viewer",
                                "legal_note": "Blocks unauthorized writes.",
                            }
                        )
                        st.rerun()

                if st.session_state.get("show_privacy"):
                    st.info(
                        f"**Jurisdiction:** {meta.get('jurisdiction')}\n\n"
                        f"**Purpose:** {meta.get('purpose')}\n\n"
                        f"**Keep for:** {meta.get('retention_days_demo')} days\n\n"
                        f"**Chart write:** off in this demo\n\n"
                        f"**Who can change:** charge nurse only\n\n"
                        f"**When you change someone, we keep:** your ID, time, who, old suggestion, "
                        f"new choice, reason\n\n{OVERRIDE_LEGAL_NOTE}"
                    )

                st.markdown("**Access log** (denied writes included)")
                alog = list(st.session_state.get("access_log") or [])
                if alog:
                    for ev in reversed(alog[-8:]):
                        mark = "OK" if ev.get("allowed", True) else "DENIED"
                        st.caption(
                            f"{ev.get('ts')} · {mark} · {ev.get('actor')} · "
                            f"{ev.get('action')} — {ev.get('detail', '')[:80]}"
                        )
                else:
                    st.caption("No access events yet — Send / role switch / privacy open will log here.")

            with st.expander("More · activity", expanded=False):
                n_ped = sum(1 for r in rows if r.age_band == "pediatric")
                n_ger = sum(1 for r in rows if r.age_band == "geriatric")
                n_zh = sum(1 for r in rows if not r.has_prior_record)
                st.caption(
                    f"{len(rows)} on the list · {n_ped} children · {n_ger} older adults · "
                    f"{n_zh} first visits"
                )
                if st.session_state.audit:
                    for ev in reversed(st.session_state.audit[-6:]):
                        st.text(
                            f"{ev.get('ts', '')} · {ev.get('action')} · {ev.get('patient_id')}"
                        )
                        st.caption(ev.get("detail", "") or ev.get("after", ""))
                else:
                    st.caption("No activity yet.")

                st.download_button(
                    "Download activity log",
                    data=json.dumps(st.session_state.audit, indent=2),
                    file_name="doorboard_audit.json",
                    mime="application/json",
                    use_container_width=True,
                )
                if st.session_state.sealed_snapshot:
                    st.download_button(
                        "Download confirmed / handoff list",
                        data=json.dumps(st.session_state.sealed_snapshot, indent=2),
                        file_name="doorboard_sealed.json",
                        mime="application/json",
                        use_container_width=True,
                    )

                if st.button("Start over", use_container_width=True):
                    for k in list(st.session_state.keys()):
                        del st.session_state[k]
                    st.rerun()


