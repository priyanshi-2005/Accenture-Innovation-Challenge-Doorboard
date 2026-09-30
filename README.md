# DoorBoard — Round 2 Prototype

**Team:** Sap_accenture  
**Members:** Aaditya Rathi · Shivesh Shukla · Priyanshi Agarwal  
**Institute:** Indian Institute of Technology Kanpur  
**Track:** PatientTriage.ai · Accenture Innovation Challenge 2026

> Demonstration only. Synthetic patients. Not a medical record. **EHR write is disabled.**

**Hosted demo:** https://doorboard-sap.streamlit.app/

---

## Problem

ESI scores one patient at a time. At the door, the charge nurse must still decide *who goes next* and *into which open space* (resus, bay, chair, isolation) when many people share the same ESI level, facts are thin, and capacity is changing. DoorBoard is built for that gap: whole-room order and destination, with the nurse in the lead and no EHR write.

---

## 1. One-line idea

ESI answers *how sick is this one person*.  
**DoorBoard answers *of everyone waiting, who goes next, and into which open space*.**

The AI recommends. The charge nurse confirms, holds, or changes. Nothing becomes clinical truth until a human acts, and this prototype never writes to an EHR.

---

## 2. Implementation approach

We treat the **waiting room**, not the single patient, as the unit of decision.

| Step | What we built | Why |
|---|---|---|
| 1. Score | Age-appropriate acuity (NEWS2 for adults; PEWS/APLS strata for children) plus an *information* score (how complete the facts are) | Adult thresholds on children are a known harm; thin data must raise caution, not invent certainty |
| 2. Second opinion | Calibrated model predicts P(critical within 4 hours) | Catches cases rules can miss |
| 3. Safety blend | Model may only *lift* acuity (never lower); gated at p ≥ 0.45 | Wrong model in the unsafe direction cannot downgrade a patient |
| 4. Rank | Whole-room order against open resus / bay / chair / isolation | ESI ties are the real door problem; capacity is part of the decision |
| 5. Control | Send / Hold / Change, reason-coded audit, safety confirmation on dangerous moves | Humans stay in the lead |
| 6. Measure | Under-triage, over-triage, wait times, latency — all from code | Claims without a measurement path are not claims |

**Design rule we will not break:** under-triage costs more than over-triage. Where facts are thin, the engine escalates; it does not fill gaps with optimism.

---

## 3. Solution architecture

```
app.py                          Streamlit UI — Floor (nurse) + Demo (evidence)
doorboard/engine/
  models.py                     Patient, room, profile, audit types
  bands.py                      NEWS2 / PEWS / APLS age strata (+ listed deviations)
  text.py                       Negation-aware complaint / appearance parse
  scoring.py                    Acuity + information + escalate rule
  deterioration.py              Trend on serial vitals; wait ceilings
  ml.py                         Calibrated GBM; lift-only blend; synthetic cohort
  ranking.py                    Whole-room order + space suggestion + why-line
  feedback.py                   Overrides → reviewable weight proposals (human-approved)
  evaluation.py                 Confusion matrix, rates, sweep, replay, latency
  pipeline.py                   Load, surge, profiles, integration mocks, proofs
doorboard/data/patients.json    30 synthetic patients
tests/test_engine.py            19 tests (no pytest required)
```

**Runtime shape**

- **Floor** — charge-nurse path: Open spaces → Next up → list → Your desk  
- **Demo** — Evidence / Model / Scales / Feedback / Privacy panels for judges  

**Integration posture (this build):** read-only mocks only. No live FHIR/HL7 write. Degrades to manual intake if feeds are absent.

---

## 4. Dependencies

From `requirements.txt`:

| Package | Role |
|---|---|
| `streamlit` ≥ 1.32 | UI |
| `pandas` ≥ 2.0 | Tables / evidence views |
| `scikit-learn` ≥ 1.4 | Calibrated risk model |
| `numpy` ≥ 1.26 | Numerics |

Python **3.11+** recommended. No API keys. Offline after install.

---

## 5. Execution instructions

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

- App: **http://localhost:8501**  
- First launch trains and caches the risk model (a few seconds).  
- Tests: `python3 tests/test_engine.py` → expect **19/19**.  
- Sign in as **Priya Nair (Charge nurse)** for the full Floor path.

**Public repository:** https://github.com/arathii23/accenture  

**Hosted demo (for judges without Terminal):** https://doorboard-sap.streamlit.app/  
Deploy notes: [HOSTING.md](HOSTING.md).

---

## 6. What we can prove (from code)

Every figure is computed by `doorboard/engine/evaluation.py` on a 30-patient reference set. Re-derive in Demo → Evidence or via the test suite.

| | Normal shift | 3× surge |
|---|---|---|
| Under-triage | **0.0%** | **0.0%** |
| Over-triage | 13.3% | 23.3% |
| Exact tier match | 86.7% | 76.7% |
| Critical sensitivity | **100%** | **100%** |

**Time to first assessment** (same 30 patients; 3 min/patient intake stream):

| Ordering | Mean wait, unstable | Worst unstable |
|---|---|---|
| DoorBoard | **23.2 min** | 48.0 min |
| ESI-only | 29.4 min | 69.0 min |
| First-come-first-served | 59.4 min | 87.0 min |

Latency (local CPU, no network, excludes UI): about **0.4 ms**/patient · about **34 ms** median to re-rank 30.

**Honest limits:** reference labels are team consensus on synthetic patients — not clinical ground truth. The ML cohort is synthetic. These numbers prove the *measurement apparatus* and the *safety tuning*, not bedside performance.

---

## 7. Three-minute judge path

1. **Floor** — Open spaces → Next up → **Send** / Hold / Change  
2. **Needs your eye** — worse / wait / missing / may leave  
3. Try to move a held patient *down* → safety confirmation blocks  
4. **Busy night** (3×) → Change → Handoff (still no EHR write)  
5. **Demo → Evidence** — under-/over-triage, sweep, latency  
6. **Demo → Model / Scales / Privacy** — lift-only blend, pediatric band fix, DPDP  

---

## 8. Repo docs

| Path | Role |
|---|---|
| [Sap_accenture_Readme.pdf](Sap_accenture_Readme.pdf) | README document (portal upload) |
| [GOVERNANCE.md](GOVERNANCE.md) | DPDP s.7 / s.6 / s.9 + DPIA notes |
| [HOSTING.md](HOSTING.md) | Streamlit Cloud deploy steps |

---

## 9. Out of scope (stated)

- Live hospital EHR write  
- Clinical validation on real patients  
- Diagnosis, discharge, or treatment advice  
- Replacing a clinician’s judgement  

---

**Sap_accenture · IIT Kanpur · PatientTriage.ai · Round 2**
