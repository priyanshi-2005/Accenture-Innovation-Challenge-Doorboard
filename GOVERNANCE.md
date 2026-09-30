# DoorBoard — Data protection and clinical governance

**Team:** Sap_accenture · Aaditya Rathi · Shivesh Shukla · Priyanshi Agarwal · IIT Kanpur  
**Track:** PatientTriage.ai · Accenture Innovation Challenge 2026

Assumed jurisdiction: **India, Digital Personal Data Protection Act 2023 (DPDP)**, read alongside institutional clinical governance and the Clinical Establishments Act record-keeping rules. Section references are to the DPDP Act 2023; the DPDP Rules were still bedding in at the time of writing, so anything operational here would be re-checked against the final Rules before a pilot.

> Demonstration only. Every patient in the prototype is synthetic. No real personal data is processed anywhere in this build.

---

## 1. What the lawful basis actually is

The first version of this proposal treated consent as the gate for everything, which is both legally wrong and operationally dangerous — a triage tool that stops working when a collapsing patient cannot consent is not a triage tool.

| Processing | Basis | Note |
|---|---|---|
| Scoring and ranking a patient at the door | **s.7 certain legitimate uses**, medical emergency limb | Consent is not required and cannot be withheld for this. Emergency care does not wait for a form. |
| Retaining the episode record for audit | s.7 read with clinical record-keeping duties | Retention is 30 days in the demo, purpose-locked to triage assist |
| Anything beyond the episode — service improvement, model retraining, research | **s.6 consent**, granular and separately captured | This is where a refusal genuinely bites, and it does not degrade care |
| A patient under 18 | **s.9**, see below | Additional obligations stack on top of the above |

P17 in the demo set has `consent_doorboard: false`. That patient is still triaged, still ranked, still safe — because triage runs on the s.7 emergency limb. What the refusal removes is the improvement and retraining use. Showing that distinction on stage is the point of the case.

Section 7 removes the consent requirement. It removes nothing else: purpose limitation, security safeguards, retention limits, processor contracts, breach response and the ability to explain the decision afterwards all still apply in full.

## 2. Section 9 — patients under 18

Five of thirty demo patients are children. This is the highest-penalty area of the Act (up to ₹200 crore) and the first version of this submission did not mention it at all.

**What s.9 requires and how DoorBoard responds:**

- **Verifiable parental or guardian consent** for any consent-based processing of a child's data. DoorBoard therefore never uses the consent basis for a child at the door — triage runs on s.7 only, and the consent path for improvement/retraining is presented to the accompanying adult with guardian identity recorded, or is simply not taken.
- **No tracking or behavioural monitoring of children.** DoorBoard has no cross-visit behavioural profiling of any patient and specifically excludes under-18 records from the feedback corpus that generates weight proposals. A child's override history never influences the engine.
- **No targeted advertising.** Not applicable, and stated so that it is on the record.
- **Unaccompanied minors** get an explicit workflow: triage proceeds under s.7, the guardian-consent path is deferred and flagged, and the deferral itself is an audit entry rather than a silent gap.

The clinical mirror of this is already built: five APLS age strata rather than one 0–17 bucket, so a child is scored as the age they are. The legal and the clinical protections point the same way.

## 3. What an override must record

Every override writes a structured event. The field list is enforced in `AuditEvent`, not left to the UI:

```
ts, actor, actor_role, patient_id, action, reason_code, detail (free text),
before (prior recommendation + position + destination),
after (new decision), severity, safety_confirmed, lawful_basis, legal_note
```

Two things changed from the first cut:

- **`severity`** distinguishes a routine reorder from a `safety_critical` override — one that clears a hold DoorBoard placed because a reading was missing. Those are counted separately and reported at handoff.
- **`safety_confirmed`** records that the clinician was shown what they were clearing and confirmed it. The system refuses the write otherwise (`SafetyConfirmRequired`).

A regulator or a clinical governance lead asking "who downgraded this patient, what did the system say, and did they know they were clearing a safety rule" gets an answer from one row.

## 4. Data protection impact assessment — summary

A full DPIA would be a pilot deliverable. The short form:

**Nature and scope.** Health data, processed at the point of arrival, for seconds to hours, on patients who are by definition not in a position to shop around. High vulnerability, high asymmetry of power.

**Necessity and proportionality.** The processing is the minimum that supports the decision: vitals, presenting complaint, observed appearance, arrival mode, and prior-record existence. DoorBoard does not read diagnosis history, social data, payer status, address, caste, religion or any protected attribute, and it cannot — those fields are not in the model input at all (`ml.FEATURES` is twelve physiological and operational features). Excluding them is a bias control as much as a privacy one.

**Risks identified.**

| Risk | Control |
|---|---|
| Automation bias — nurse defers to the screen | Recommend-only, mandatory confirm, five-second reason line, override always one tap |
| Silent downgrade of a vulnerable patient | Engine cannot downgrade under uncertainty; ML can only lift; safety holds need confirmation |
| Age-related silent harm | Five pediatric strata, published scales, and the band comparison is inspectable in the UI |
| Re-identification from an audit export | Exports are pseudonymised by patient ID; name resolution requires a separate authorisation |
| Function creep into performance management | Purpose lock is declared and displayed; override data is explicitly excluded from staff appraisal by policy |
| Model drift | Held-out metrics re-reported per release; proposals from feedback are human-approved, never auto-applied |

**Residual risk.** Over-triage at 13.3% on the reference set, rising to 23.3% under surge. That is a deliberate purchase, priced and reported, not a defect.

## 5. Security and access

- Role-based access: charge nurse can decide; viewer role can read and is blocked from writing, with denied attempts logged rather than discarded.
- **No EHR write** anywhere in this build. The integration mocks are read-only by construction.
- Retention 30 days in the demo, with a visible purge action. Production retention would follow institutional clinical record policy, which is longer, and the two are kept as separate settings so nobody conflates them.
- Breach notification: a personal data breach obliges notification to the Data Protection Board and to affected Data Principals. The audit store is designed to make the affected-set query answerable — every read of a patient record is logged with actor and time.
- Data Fiduciary classification: a hospital group running DoorBoard across sites would likely meet the volume and sensitivity criteria for **Significant Data Fiduciary** status, which brings DPIA, independent audit and a Data Protection Officer. The pilot plan assumes that and budgets for it rather than discovering it late.

## 6. Where a real deployment would need more

Stated plainly, because pretending otherwise is the failure mode this document exists to avoid:

- Cross-border processing rules if any component is hosted outside India.
- Processor agreements with the cloud and model vendors, including a no-training-on-our-data term.
- Ethics committee review before any prospective clinical evaluation.
- Reconciliation with hospital-specific consent forms already in use at intake.
- Legal sign-off on whether the triage recommendation itself becomes part of the medical record, which determines retention and disclosure obligations downstream.
