"""Build the committed fixture set: six synthetic patients as FHIR R4 bundles plus
matching ClinicalSummary JSON, validated with fhir.resources before writing.

    python scripts/make_fixtures.py

Writes data/fixtures/bundles/<patient_id>.json and data/fixtures/summaries/<patient_id>.json.

These let Task 4 (search) and Task 5 (UI) be built and tested before the
pipeline output exists. Shapes follow app/contracts.py and docs/decisions.md:
`collection` bundles, `urn:uuid:` references, deterministic UUIDv5 ids, resource ids
written as "ResourceType/uuid" in summaries. Every name, date and finding is invented.
"""

import base64
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.contracts import ClinicalSummary, MediaItem  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures"
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ehr-media-intel")
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
DR_CAT = "http://terminology.hl7.org/CodeSystem/v2-0074"
ENC_CLASS = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
MRN_SYSTEM = "urn:ehr-media-intel:mrn"


def rid(kind: str, key: str) -> str:
    """Deterministic resource id: the same input always yields the same UUID."""
    return str(uuid.uuid5(NAMESPACE, f"{kind}/{key}"))


def urn(resource_id: str) -> dict:
    return {"reference": f"urn:uuid:{resource_id}"}


def b64(text: str) -> str:
    return base64.b64encode(text.strip().encode()).decode()


def coding(system: str, code: str, display: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": display}


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------


def patient(pid: str, mrn: str, given: str, family: str, gender: str, dob: str) -> dict:
    return {
        "resourceType": "Patient",
        "id": rid("Patient", pid),
        "identifier": [{"system": MRN_SYSTEM, "value": mrn}],
        "name": [{"family": family, "given": [given]}],
        "gender": gender,
        "birthDate": dob,
    }


def encounter(pid: str, key: str, cls: tuple[str, str], start: str, end: str) -> dict:
    return {
        "resourceType": "Encounter",
        "id": rid("Encounter", f"{pid}/{key}"),
        "status": "finished",
        "class": {"system": ENC_CLASS, "code": cls[0], "display": cls[1]},
        "subject": urn(rid("Patient", pid)),
        "period": {"start": start, "end": end},
    }


def document(
    pid: str,
    key: str,
    enc_key: str,
    loinc: tuple[str, str],
    date: str,
    title: str,
    text: str,
) -> dict:
    return {
        "resourceType": "DocumentReference",
        "id": rid("DocumentReference", f"{pid}/{key}"),
        "status": "current",
        "type": coding(LOINC, *loinc),
        "subject": urn(rid("Patient", pid)),
        "date": f"{date}T12:00:00Z",
        "description": title,
        "content": [
            {"attachment": {"contentType": "text/plain", "data": b64(text), "title": title}}
        ],
        "context": {"encounter": [urn(rid("Encounter", f"{pid}/{enc_key}"))]},
    }


def observation(
    pid: str,
    key: str,
    loinc: tuple[str, str],
    date: str,
    value: float,
    unit: str,
    flag: str | None,
    ref: tuple[float, float] | None,
) -> dict:
    obs = {
        "resourceType": "Observation",
        "id": rid("Observation", f"{pid}/{key}"),
        "status": "final",
        "category": [coding(OBS_CAT, "laboratory", "Laboratory")],
        "code": coding(LOINC, *loinc),
        "subject": urn(rid("Patient", pid)),
        "effectiveDateTime": date,
        "valueQuantity": {"value": value, "unit": unit, "system": UCUM, "code": unit},
    }
    if flag:
        display = {"H": "High", "L": "Low", "N": "Normal"}[flag]
        obs["interpretation"] = [coding(INTERP, flag, display)]
    if ref:
        obs["referenceRange"] = [
            {"low": {"value": ref[0], "unit": unit}, "high": {"value": ref[1], "unit": unit}}
        ]
    return obs


def report(
    pid: str,
    key: str,
    enc_key: str,
    category: tuple[str, str],
    loinc: tuple[str, str],
    date: str,
    conclusion: str,
    text: str,
    observations: list[dict] | None = None,
) -> dict:
    rep = {
        "resourceType": "DiagnosticReport",
        "id": rid("DiagnosticReport", f"{pid}/{key}"),
        "status": "final",
        "category": [coding(DR_CAT, *category)],
        "code": coding(LOINC, *loinc),
        "subject": urn(rid("Patient", pid)),
        "encounter": urn(rid("Encounter", f"{pid}/{enc_key}")),
        "effectiveDateTime": date,
        "issued": f"{date}T16:00:00Z",
        "conclusion": conclusion,
        "presentedForm": [
            {"contentType": "text/plain", "data": b64(text), "title": loinc[1]},
        ],
    }
    if observations:
        rep["result"] = [urn(o["id"]) for o in observations]
    return rep


def bundle(pid: str, resources: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "id": rid("Bundle", pid),
        "type": "collection",
        "timestamp": "2025-10-01T00:00:00Z",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }


def ref_id(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


LAB = ("LAB", "Laboratory")
RAD = ("RAD", "Radiology")
INPATIENT = ("IMP", "inpatient encounter")
AMBULATORY = ("AMB", "ambulatory")
EMERGENCY = ("EMER", "emergency")

# ---------------------------------------------------------------------------
# The six patients. Chosen so every query in eval/queries.yaml has clear hits
# and at least one near-miss.
# ---------------------------------------------------------------------------


def build_all() -> list[tuple[str, dict, ClinicalSummary]]:
    out = []

    # --- p-0001: heart failure admission ---------------------------------
    pid = "p-0001"
    pt = patient(pid, "MRN-000101", "Abebe", "Kebede", "male", "1961-05-14")
    enc = encounter(pid, "adm-2025-02", INPATIENT, "2025-02-03", "2025-02-09")
    bnp = observation(
        pid,
        "bnp",
        ("30934-4", "Natriuretic peptide B [Mass/volume] in Blood"),
        "2025-02-03",
        1480,
        "pg/mL",
        "H",
        (0, 100),
    )
    bnp_rep = report(
        pid,
        "bnp",
        "adm-2025-02",
        LAB,
        ("30934-4", "BNP panel"),
        "2025-02-03",
        "BNP markedly elevated at 1480 pg/mL, consistent with decompensated heart failure.",
        "B-type natriuretic peptide 1480 pg/mL (reference 0-100). Markedly elevated. "
        "Correlate with clinical signs of volume overload.",
        [bnp],
    )
    cxr = report(
        pid,
        "cxr",
        "adm-2025-02",
        RAD,
        ("36643-5", "Chest X-ray 2 views"),
        "2025-02-03",
        "Abnormal chest x-ray: cardiomegaly with bilateral interstitial edema and small "
        "pleural effusions, consistent with congestive heart failure.",
        "CHEST X-RAY, PA AND LATERAL. Indication: shortness of breath, leg swelling. "
        "Findings: enlarged cardiac silhouette (cardiothoracic ratio 0.62). Bilateral "
        "interstitial markings and Kerley B lines consistent with pulmonary edema. Small "
        "bilateral pleural effusions. No consolidation or pneumothorax. Impression: "
        "cardiomegaly with pulmonary vascular congestion and edema, consistent with CHF.",
    )
    dsum = document(
        pid,
        "discharge",
        "adm-2025-02",
        ("18842-5", "Discharge summary"),
        "2025-02-09",
        "Discharge summary: acute decompensated heart failure",
        "DISCHARGE SUMMARY. Admitted 2025-02-03 with three days of worsening dyspnea, "
        "orthopnea and bilateral leg swelling. Diagnosis: acute decompensated heart failure "
        "with reduced ejection fraction (HFrEF, EF 30% on echo). BNP 1480. Chest x-ray showed "
        "pulmonary edema and effusions. Treated with IV furosemide with 6 kg weight loss. "
        "Discharged 2025-02-09 on furosemide 40 mg daily, carvedilol, sacubitril/valsartan "
        "and empagliflozin. Daily weights, 2 g sodium diet, cardiology follow-up in 1 week.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, bnp, bnp_rep, cxr, dsum]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Worsening shortness of breath and leg swelling over three days.",
                key_diagnoses=[
                    "Acute decompensated heart failure (HFrEF, EF 30%)",
                    "Hypertension",
                ],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(cxr),
                        type="imaging",
                        date=dt.date(2025, 2, 3),
                        note="Cardiomegaly, pulmonary edema, small bilateral effusions.",
                    ),
                    MediaItem(
                        resource_id=ref_id(bnp_rep),
                        type="lab",
                        date=dt.date(2025, 2, 3),
                        note="BNP 1480 pg/mL, markedly elevated.",
                    ),
                    MediaItem(
                        resource_id=ref_id(dsum),
                        type="document",
                        date=dt.date(2025, 2, 9),
                        note="Discharged after diuresis on guideline heart failure therapy.",
                    ),
                ],
                anomalies=["BNP 1480 pg/mL (reference 0-100)", "Cardiothoracic ratio 0.62"],
                citations=[ref_id(cxr), ref_id(bnp_rep), ref_id(dsum)],
                confidence="high",
            ),
        )
    )

    # --- p-0002: poorly controlled type 2 diabetes -----------------------
    pid = "p-0002"
    pt = patient(pid, "MRN-000102", "Sara", "Tesfaye", "female", "1978-11-02")
    enc = encounter(pid, "clinic-2025-03", AMBULATORY, "2025-03-14", "2025-03-14")
    a1c = observation(
        pid,
        "a1c",
        ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood"),
        "2025-03-14",
        9.8,
        "%",
        "H",
        (4.0, 5.6),
    )
    glu = observation(
        pid,
        "glucose",
        ("1558-6", "Fasting glucose [Mass/volume] in Serum or Plasma"),
        "2025-03-14",
        212,
        "mg/dL",
        "H",
        (70, 99),
    )
    a1c_rep = report(
        pid,
        "a1c",
        "clinic-2025-03",
        LAB,
        ("4548-4", "Hemoglobin A1c panel"),
        "2025-03-14",
        "Elevated HbA1c of 9.8% and fasting glucose 212 mg/dL indicate poorly controlled "
        "type 2 diabetes.",
        "HbA1c 9.8% (reference 4.0-5.6%), HIGH. Fasting plasma glucose 212 mg/dL "
        "(reference 70-99), HIGH. Previous HbA1c 8.1% six months ago; glycemic control "
        "has worsened.",
        [a1c, glu],
    )
    note = document(
        pid,
        "progress",
        "clinic-2025-03",
        ("11506-3", "Progress note"),
        "2025-03-14",
        "Endocrinology progress note: poorly controlled blood sugar",
        "PROGRESS NOTE. 46-year-old woman with type 2 diabetes for 8 years, here for "
        "follow-up. Reports poorly controlled blood sugar with home readings 200-300 mg/dL, "
        "polyuria and blurred vision. Admits missing metformin doses. HbA1c today 9.8%, up "
        "from 8.1%. Foot exam: mild loss of monofilament sensation bilaterally. Plan: "
        "continue metformin, add semaglutide weekly, diabetes education referral, retinal "
        "screening, recheck HbA1c in 3 months.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, a1c, glu, a1c_rep, note]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Follow-up for type 2 diabetes with high home glucose readings.",
                key_diagnoses=[
                    "Type 2 diabetes mellitus, poorly controlled",
                    "Early peripheral neuropathy",
                ],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(a1c_rep),
                        type="lab",
                        date=dt.date(2025, 3, 14),
                        note="HbA1c 9.8%, fasting glucose 212 mg/dL, both high.",
                    ),
                    MediaItem(
                        resource_id=ref_id(note),
                        type="note",
                        date=dt.date(2025, 3, 14),
                        note="Missed metformin doses; semaglutide added.",
                    ),
                ],
                anomalies=["HbA1c 9.8% (reference 4.0-5.6%)", "Fasting glucose 212 mg/dL"],
                citations=[ref_id(a1c_rep), ref_id(note)],
                confidence="high",
            ),
        )
    )

    # --- p-0003: community-acquired pneumonia ----------------------------
    pid = "p-0003"
    pt = patient(pid, "MRN-000103", "Daniel", "Haile", "male", "1985-07-21")
    enc = encounter(pid, "ed-2024-11", EMERGENCY, "2024-11-18", "2024-11-18")
    wbc = observation(
        pid,
        "wbc",
        ("6690-2", "Leukocytes [#/volume] in Blood by Automated count"),
        "2024-11-18",
        15.2,
        "10*3/uL",
        "H",
        (4.0, 11.0),
    )
    cbc = report(
        pid,
        "cbc",
        "ed-2024-11",
        LAB,
        ("58410-2", "CBC panel"),
        "2024-11-18",
        "Leukocytosis (WBC 15.2) with neutrophil predominance, consistent with bacterial "
        "infection.",
        "WBC 15.2 x10^3/uL (reference 4.0-11.0), HIGH, 84% neutrophils. Hemoglobin 14.1 "
        "g/dL normal. Platelets 310 normal.",
        [wbc],
    )
    cxr = report(
        pid,
        "cxr",
        "ed-2024-11",
        RAD,
        ("36643-5", "Chest X-ray 2 views"),
        "2024-11-18",
        "Right lower lobe consolidation consistent with pneumonia.",
        "CHEST X-RAY, PA AND LATERAL. Indication: fever, productive cough, right-sided "
        "pleuritic chest pain. Findings: dense airspace consolidation in the right lower "
        "lobe with air bronchograms. No pleural effusion. Heart size normal. Impression: "
        "right lower lobe consolidation, consistent with lobar pneumonia. Recommend "
        "follow-up CXR in 6 weeks to confirm resolution.",
    )
    ednote = document(
        pid,
        "ed-note",
        "ed-2024-11",
        ("34111-5", "Emergency department note"),
        "2024-11-18",
        "ED note: fever and productive cough",
        "EMERGENCY DEPARTMENT NOTE. 39-year-old man, 3 days of fever to 39.2 C, productive "
        "cough with rusty sputum and right-sided chest pain on inspiration. Vitals: HR 108, "
        "RR 24, SpO2 93% on room air. Crackles right base. CXR shows right lower lobe "
        "consolidation. WBC 15.2. Diagnosis: community-acquired pneumonia, CURB-65 score 1. "
        "Started amoxicillin-clavulanate and azithromycin. Discharged home with return "
        "precautions and primary care follow-up in 48 hours.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, wbc, cbc, cxr, ednote]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Three days of fever, productive cough and right-sided chest pain.",
                key_diagnoses=["Community-acquired pneumonia, right lower lobe"],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(cxr),
                        type="imaging",
                        date=dt.date(2024, 11, 18),
                        note="Right lower lobe consolidation with air bronchograms.",
                    ),
                    MediaItem(
                        resource_id=ref_id(cbc),
                        type="lab",
                        date=dt.date(2024, 11, 18),
                        note="WBC 15.2 with neutrophilia.",
                    ),
                    MediaItem(
                        resource_id=ref_id(ednote),
                        type="note",
                        date=dt.date(2024, 11, 18),
                        note="Treated as outpatient with oral antibiotics.",
                    ),
                ],
                anomalies=["WBC 15.2 x10^3/uL (reference 4.0-11.0)", "SpO2 93% on room air"],
                citations=[ref_id(cxr), ref_id(cbc), ref_id(ednote)],
                confidence="high",
            ),
        )
    )

    # --- p-0004: mild asthma, normal imaging (negative control) ----------
    pid = "p-0004"
    pt = patient(pid, "MRN-000104", "Hiwot", "Alemu", "female", "1992-01-30")
    enc = encounter(pid, "clinic-2024-06", AMBULATORY, "2024-06-05", "2024-06-05")
    cxr = report(
        pid,
        "cxr",
        "clinic-2024-06",
        RAD,
        ("36643-5", "Chest X-ray 2 views"),
        "2024-06-05",
        "Normal chest x-ray. No acute cardiopulmonary abnormality.",
        "CHEST X-RAY, PA AND LATERAL. Indication: intermittent wheeze. Findings: lungs "
        "clear. Heart size and mediastinal contours normal. No effusion or pneumothorax. "
        "Impression: no acute cardiopulmonary abnormality.",
    )
    note = document(
        pid,
        "progress",
        "clinic-2024-06",
        ("11506-3", "Progress note"),
        "2024-06-05",
        "Primary care note: intermittent wheeze",
        "PROGRESS NOTE. 32-year-old woman with intermittent wheeze and night cough for two "
        "months, worse with exercise and in spring. No fever. Lungs clear today, peak flow "
        "92% predicted. Chest x-ray normal. Spirometry shows 14% FEV1 improvement after "
        "bronchodilator. Diagnosis: mild persistent asthma, likely allergic. Started "
        "low-dose inhaled budesonide-formoterol as needed. Asthma action plan given.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, cxr, note]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Two months of intermittent wheeze and night cough.",
                key_diagnoses=["Mild persistent asthma, probable allergic trigger"],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(cxr),
                        type="imaging",
                        date=dt.date(2024, 6, 5),
                        note="Normal chest x-ray.",
                    ),
                    MediaItem(
                        resource_id=ref_id(note),
                        type="note",
                        date=dt.date(2024, 6, 5),
                        note="Reversible airflow obstruction on spirometry; inhaler started.",
                    ),
                ],
                anomalies=[],
                citations=[ref_id(cxr), ref_id(note)],
                confidence="medium",
            ),
        )
    )

    # --- p-0005: COPD with incidental lung nodule ------------------------
    pid = "p-0005"
    pt = patient(pid, "MRN-000105", "Yonas", "Girma", "male", "1955-09-09")
    enc = encounter(pid, "pulm-2025-05", AMBULATORY, "2025-05-20", "2025-05-20")
    cxr = report(
        pid,
        "cxr",
        "pulm-2025-05",
        RAD,
        ("36643-5", "Chest X-ray 2 views"),
        "2025-05-20",
        "Abnormal chest x-ray: hyperinflation consistent with COPD and a new 1.4 cm right "
        "upper lobe nodule. CT chest recommended.",
        "CHEST X-RAY, PA AND LATERAL. Indication: COPD, increased dyspnea, 40 pack-year "
        "smoking history. Findings: hyperinflated lungs with flattened diaphragms and "
        "increased retrosternal airspace. New 1.4 cm well-defined nodule in the right upper "
        "lobe, not present on the 2023 study. No effusion. Impression: emphysematous changes "
        "and a new right upper lobe pulmonary nodule. Recommend contrast CT chest.",
    )
    note = document(
        pid,
        "pulm-note",
        "pulm-2025-05",
        ("11506-3", "Progress note"),
        "2025-05-20",
        "Pulmonology note: COPD follow-up and new nodule",
        "PULMONOLOGY NOTE. 69-year-old man with GOLD stage 3 COPD, 40 pack-years, quit "
        "smoking 2 years ago. Reports increased exertional dyspnea, no hemoptysis or weight "
        "loss. FEV1 44% predicted. Chest x-ray shows hyperinflation and a new 1.4 cm right "
        "upper lobe nodule. Plan: urgent CT chest with contrast, continue tiotropium and "
        "budesonide-formoterol, pulmonary rehabilitation referral, discuss nodule at tumour "
        "board if CT confirms.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, cxr, note]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Increasing exertional breathlessness in known COPD.",
                key_diagnoses=["COPD, GOLD stage 3", "Right upper lobe pulmonary nodule, 1.4 cm"],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(cxr),
                        type="imaging",
                        date=dt.date(2025, 5, 20),
                        note="Hyperinflation and a new 1.4 cm right upper lobe nodule.",
                    ),
                    MediaItem(
                        resource_id=ref_id(note),
                        type="note",
                        date=dt.date(2025, 5, 20),
                        note="Urgent CT chest ordered; pulmonary rehab referral.",
                    ),
                ],
                anomalies=["New 1.4 cm right upper lobe nodule", "FEV1 44% predicted"],
                citations=[ref_id(cxr), ref_id(note)],
                confidence="high",
            ),
        )
    )

    # --- p-0006: CKD with hypertension and prediabetes -------------------
    pid = "p-0006"
    pt = patient(pid, "MRN-000106", "Marta", "Bekele", "female", "1970-03-17")
    enc = encounter(pid, "neph-2023-09", AMBULATORY, "2023-09-12", "2023-09-12")
    creat = observation(
        pid,
        "creatinine",
        ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
        "2023-09-12",
        1.9,
        "mg/dL",
        "H",
        (0.6, 1.1),
    )
    egfr = observation(
        pid,
        "egfr",
        ("62238-1", "GFR/1.73 sq M.predicted by CKD-EPI"),
        "2023-09-12",
        31,
        "mL/min/{1.73_m2}",
        "L",
        (60, 120),
    )
    a1c6 = observation(
        pid,
        "a1c",
        ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood"),
        "2023-09-12",
        6.1,
        "%",
        "H",
        (4.0, 5.6),
    )
    renal = report(
        pid,
        "renal",
        "neph-2023-09",
        LAB,
        ("24362-6", "Renal function panel"),
        "2023-09-12",
        "Creatinine 1.9 mg/dL and eGFR 31 indicate stage 3b chronic kidney disease. "
        "HbA1c 6.1% in the prediabetic range.",
        "Creatinine 1.9 mg/dL (reference 0.6-1.1), HIGH. eGFR 31 mL/min/1.73m2, LOW, "
        "CKD stage 3b. Potassium 4.9 normal. HbA1c 6.1% (reference 4.0-5.6), mildly "
        "elevated, prediabetes range. Urine albumin/creatinine ratio 180 mg/g, moderately "
        "increased.",
        [creat, egfr, a1c6],
    )
    note = document(
        pid,
        "neph-note",
        "neph-2023-09",
        ("11506-3", "Progress note"),
        "2023-09-12",
        "Nephrology note: CKD stage 3b and hypertension",
        "NEPHROLOGY NOTE. 53-year-old woman with long-standing hypertension referred for "
        "declining kidney function. BP 152/94 on amlodipine. Creatinine 1.9, eGFR 31, "
        "albuminuria 180 mg/g. HbA1c 6.1% consistent with prediabetes. Diagnosis: CKD stage "
        "3b, likely hypertensive nephrosclerosis. Plan: start losartan, target BP under "
        "130/80, add dapagliflozin for kidney protection, avoid NSAIDs, dietary sodium "
        "restriction, repeat labs in 3 months, renal ultrasound.",
    )
    out.append(
        (
            pid,
            bundle(pid, [pt, enc, creat, egfr, a1c6, renal, note]),
            ClinicalSummary(
                patient_id=pid,
                chief_concern="Referred for declining kidney function with uncontrolled "
                "hypertension.",
                key_diagnoses=["Chronic kidney disease stage 3b", "Hypertension", "Prediabetes"],
                recent_media=[
                    MediaItem(
                        resource_id=ref_id(renal),
                        type="lab",
                        date=dt.date(2023, 9, 12),
                        note="Creatinine 1.9, eGFR 31, HbA1c 6.1%, albuminuria 180 mg/g.",
                    ),
                    MediaItem(
                        resource_id=ref_id(note),
                        type="note",
                        date=dt.date(2023, 9, 12),
                        note="Losartan and dapagliflozin started; BP target under 130/80.",
                    ),
                ],
                anomalies=[
                    "eGFR 31 mL/min/1.73m2 (reference 60-120)",
                    "Creatinine 1.9 mg/dL",
                    "Urine albumin/creatinine ratio 180 mg/g",
                ],
                citations=[ref_id(renal), ref_id(note)],
                confidence="high",
            ),
        )
    )

    return out


def finalize_summary(summary: ClinicalSummary) -> ClinicalSummary:
    """Fill the code-owned fields the way Task 3 does: word count and provenance."""
    text = " ".join(
        [summary.chief_concern, *summary.key_diagnoses, *summary.anomalies]
        + [m.note for m in summary.recent_media]
    )
    return summary.model_copy(
        update={
            "word_count": len(text.split()),
            "model": "fixture",
            "prompt_version": "v1",
            "generated_at": dt.datetime(2025, 10, 1, tzinfo=dt.UTC),
        }
    )


def validate_bundle(data: dict) -> dict:
    """Round-trip through fhir.resources (R4B) so the fixture is a valid R4 bundle."""
    from fhir.resources.R4B.bundle import Bundle

    parsed = Bundle.model_validate(data)
    return json.loads(parsed.model_dump_json())


def main() -> int:
    (FIXTURES / "bundles").mkdir(parents=True, exist_ok=True)
    (FIXTURES / "summaries").mkdir(parents=True, exist_ok=True)
    for pid, bdl, summary in build_all():
        valid = validate_bundle(bdl)
        (FIXTURES / "bundles" / f"{pid}.json").write_text(
            json.dumps(valid, indent=2) + "\n", encoding="utf-8"
        )
        final = finalize_summary(summary)
        if final.word_count > 200:
            raise SystemExit(f"{pid}: summary is {final.word_count} words, limit is 200")
        (FIXTURES / "summaries" / f"{pid}.json").write_text(
            final.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(f"{pid}: {len(valid['entry'])} resources, summary {final.word_count} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
