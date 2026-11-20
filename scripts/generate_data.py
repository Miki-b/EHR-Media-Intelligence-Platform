"""Generate synthetic, deliberately messy EHR source files.

Usage:
    python scripts/generate_data.py [--seed 42] [--out data/raw]

Writes into the output directory:
    ehr_export.json    simulated Epic export (patients with nested documents, labs, imaging)
    lab_results.csv    lab feed, one row per result
    notes/*.txt        "scanned" notes: loose header lines, then free text
    DEFECTS.md         every injected defect, one row per instance (human-readable oracle)
    ground_truth.json  answer key for tests ONLY; the ingestion code must never read it

Defect strategy
---------------
All data is synthetic (Faker + templates); no real patient data is used.

1. A fixed cast of 18 canonical patients (``CAST``) is planned by hand: each entry fixes the
   patient's clinical storyline, which sources they appear in and how their gender is written.
   Faker only fills in names, addresses and phone numbers, so the defect plan is identical
   for every seed while the surface values change.
2. Clean raw structures are built first: ISO dates, ``MRN-000123`` MRNs, ``Given Family``
   names. Every raw record carries a hidden id (``_rid``) that is stripped on output.
3. Each defect is injected by a small ``inject_*`` function that mutates specific records
   and logs one ``Defect`` per instance. Nothing is messy by accident, so every cleaning rule
   in Task 1 has a known test case and a known expected result.
4. At write time hidden ids are resolved to stable locations, which DEFECTS.md and
   ground_truth.json both use:
       ehr_export.json#/<patient index>[/documents|labs|imaging/<i>]
       lab_results.csv#row=<n>      (1-based data row, header excluded)
       notes/<file>.txt

Determinism: only ``random.Random(seed)`` and ``Faker.seed_instance(seed)`` are used, and
every file is written with explicit encoding and line endings. Same seed and same Faker
version give byte-identical files.
"""

import argparse
import copy
import csv
import io
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from faker import Faker

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "raw"
NBSP = " "
FIRST_EPISODE, LAST_EPISODE = date(2023, 1, 1), date(2025, 11, 1)
DOB_YEARS = (1936, 2004)  # aged 18 to 90 across 2023-2025

DEFECT_NAMES = {
    "1": "Mixed date formats",
    "2": "Impossible date",
    "3": "Gender variant",
    "4": "MRN variant",
    "5": "Exact duplicate",
    "6": "Near duplicate",
    "7": "Same patient, two MRNs",
    "8": "Same MRN, two different people",
    "9": "Missing field",
    "10": "Name noise",
    "11": "Encoding/whitespace",
    "12": "Cross-source patient",
    "FHIR": "Seeded FHIR failure",
}

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip
DATE_STYLES = {
    "iso": "YYYY-MM-DD",
    "us": "MM/DD/YYYY",
    "compact": "YYYYMMDD",
    "long": "Month D, YYYY",
    "dmy2": "D-Mon-YY",
}


def fmt_date(d: date, style: str = "iso") -> str:
    """Render a date in one of DATE_STYLES (locale-independent, no strftime)."""
    if style == "iso":
        return d.isoformat()
    if style == "us":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if style == "compact":
        return f"{d.year}{d.month:02d}{d.day:02d}"
    if style == "long":
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    if style == "dmy2":
        return f"{d.day}-{MONTHS[d.month - 1][:3]}-{d.year % 100:02d}"
    raise ValueError(f"unknown date style {style!r}")


def json_ts(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:00")


def csv_ts(t: datetime, style: str = "iso") -> str:
    return f"{fmt_date(t.date(), style)} {t.hour:02d}:{t.minute:02d}"


def is_ambiguous_us(d: date, style: str) -> bool:
    return style == "us" and d.day <= 12 and d.day != d.month


def expected_date(field_name: str, d: date, style: str) -> str:
    note = " (ambiguous; US MM/DD assumed, audit `ambiguous_date_assumed_us`)"
    return f"{field_name} -> {d.isoformat()}" + (note if is_ambiguous_us(d, style) else "")


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def at_time(d: date, rng: random.Random) -> datetime:
    return datetime(d.year, d.month, d.day, rng.randint(6, 18), rng.choice(range(0, 60, 5)))


def q(value: str) -> str:
    """Quote a raw value so whitespace, tabs and NBSPs stay visible."""
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Clinical content
# ---------------------------------------------------------------------------

ABBREVIATIONS = {  # token -> (abbreviated style, full style)
    "CXR": ("CXR", "chest X-ray"),
    "SOB": ("SOB", "shortness of breath"),
    "CHF": ("CHF", "congestive heart failure"),
    "T2DM": ("T2DM", "type 2 diabetes mellitus"),
    "HbA1c": ("HbA1c", "hemoglobin A1c"),
    "HTN": ("HTN", "hypertension"),
    "COPD": ("COPD", "chronic obstructive pulmonary disease"),
    "DKA": ("DKA", "diabetic ketoacidosis"),
    "Pt": ("Pt", "The patient"),
}

TEMPLATES = {
    "discharge_pneumonia": """
DISCHARGE SUMMARY

Principal diagnosis: community-acquired pneumonia, {lobe} lobe.

{Pt} is a {age_sex} who presented with {days} days of productive cough, fever to {temp} C
and {SOB}. Oxygen saturation was {spo2}% on room air on arrival. {CXR} showed {lobe} lobe
consolidation. WBC was {wbc} x10^3/uL. Comorbidities include {comorb}. Treated with IV
ceftriaxone and {abx2}, then transitioned to oral {abx_po}. Blood cultures remained
negative. Fever resolved by hospital day {fever_day} and supplemental oxygen was weaned to
room air. Length of stay {los} days.

Plan: complete a {course}-day antibiotic course, incentive spirometry, primary care
follow-up in 1-2 weeks and repeat {CXR} in 6 weeks to document resolution. Return
precautions for worsening {SOB}, chest pain or recurrent fever were reviewed.
""",
    "discharge_chf": """
DISCHARGE SUMMARY

Principal diagnosis: acute on chronic systolic heart failure ({CHF} exacerbation).

{Pt} is a {age_sex} with {CHF} (EF {ef}%), {HTN} and {comorb}, admitted with progressive
{SOB}, orthopnea and {gain} kg weight gain over two weeks after dietary indiscretion. Exam
showed bibasilar crackles, elevated JVP and pitting edema of both legs. BNP was {bnp} pg/mL.
{CXR} demonstrated cardiomegaly with pulmonary vascular congestion and small bilateral
effusions. Troponin I was mildly elevated at {trop} ng/mL without dynamic change, consistent
with demand ischemia. Creatinine rose to {creat} mg/dL with diuresis. Treated with IV
furosemide, net negative {net} L; potassium was repleted. Discharge weight {wt} kg.

Plan: furosemide {furo} mg daily, carvedilol, sacubitril-valsartan, 2 g sodium diet, daily
weights, basic metabolic panel in one week and cardiology follow-up in 7-10 days.
""",
    "discharge_dka": """
DISCHARGE SUMMARY

Principal diagnosis: {DKA}, precipitated by {trigger}.

{Pt} is a {age_sex} with insulin-treated {T2DM} who presented with polyuria, polydipsia,
nausea and abdominal pain for {days} days. Initial glucose was {glu} mg/dL with an anion gap
of {gap}, bicarbonate {bicarb} mmol/L, potassium {k} mmol/L and positive serum ketones.
{HbA1c} was {a1c}%. Managed in the ICU with IV fluids, an insulin infusion and potassium
monitoring per protocol. The anion gap closed within {hours} hours and the patient was
transitioned to basal-bolus subcutaneous insulin. Diabetes education was completed and
glucometer technique reviewed.

Discharge medications: insulin glargine {glargine} units nightly, insulin lispro with meals
per correction scale, metformin resumed. Endocrinology follow-up in 2 weeks; sick-day rules
discussed.
""",
    "discharge_hip": """
DISCHARGE SUMMARY

Principal diagnosis: {side} intertrochanteric hip fracture after a mechanical fall.

{Pt} is a {age_sex} who tripped {fall_place} and was unable to bear weight. Radiographs
confirmed a displaced {side} intertrochanteric fracture. CT head was obtained because of a
minor head strike and showed no acute intracranial abnormality. Underwent cephalomedullary
nailing on hospital day {op_day}. Postoperative hemoglobin fell to {hgb} g/dL and the
patient {transfused}. Pain was controlled with acetaminophen and low-dose oxycodone. Worked
with physical therapy and was ambulating {feet} feet with a walker at discharge.
Enoxaparin for DVT prophylaxis for 28 days.

Disposition: {dispo}. Orthopedic follow-up in 2 weeks for wound check; bone density testing
recommended.
""",
    "discharge_copd": """
DISCHARGE SUMMARY

Principal diagnosis: acute exacerbation of {COPD}.

{Pt} is a {age_sex}, {packs} pack-year former smoker with {COPD} (FEV1 {fev1}% predicted),
presenting with increased {SOB}, wheeze and purulent sputum for {days} days. Oxygen
saturation was {spo2}% on room air. {CXR} showed hyperinflation without focal consolidation
or pneumothorax. WBC {wbc} x10^3/uL. Treated with nebulized albuterol and ipratropium,
prednisone 40 mg daily for 5 days and doxycycline. Noninvasive ventilation was not required.

Plan: tiotropium and budesonide-formoterol at home with inhaler technique reviewed,
pulmonary rehabilitation referral, smoking abstinence reinforced and pulmonology follow-up
in 4 weeks.
""",
    "progress_pneumonia": """
POST-DISCHARGE FOLLOW-UP

{age_sex} seen {days_after} days after hospitalization for {lobe} lobe community-acquired
pneumonia. Completed the antibiotic course. Cough is improving and now dry; no fevers at
home. Mild exertional {SOB} persists but improves daily. Lungs: faint crackles at the
{lobe} base, otherwise clear. SpO2 {spo2_fu}% on room air.

Assessment: resolving pneumonia. Plan: repeat {CXR} in 6 weeks to confirm resolution,
pneumococcal and influenza vaccination today, return for recurrent fever or worsening
breathing.
""",
    "progress_chf": """
HEART FAILURE CLINIC NOTE

{age_sex} with {CHF} (EF {ef}%) seen {days_after} days after discharge for a {CHF}
exacerbation. Weight today {wt} kg, stable on home scale. Reports {SOB} only with more than
one flight of stairs, sleeping on one pillow, no paroxysmal nocturnal dyspnea. Exam: no JVD,
clear lungs, trace ankle edema.

Assessment: chronic {CHF}, NYHA class II, euvolemic. Plan: continue furosemide {furo} mg
daily and sacubitril-valsartan, uptitrate carvedilol as tolerated, repeat basic metabolic
panel, echocardiogram in 3 months. Return sooner for weight gain over 2 kg in 3 days.
""",
    "progress_t2dm": """
PROGRESS NOTE

{age_sex} with {T2DM} for {years} years here for routine follow-up. Reports good adherence
to metformin {met} mg twice daily; fasting readings at home are {fbg_lo}-{fbg_hi} mg/dL.
Denies hypoglycemia, polyuria or visual changes. Diet has been inconsistent
recently.

Exam: BP {sbp}/{dbp}, BMI {bmi}. Monofilament sensation intact in both feet.

Assessment: {T2DM}, suboptimally controlled, latest {HbA1c} {a1c}%; {HTN}. Plan: add
{add_drug}, reinforce carbohydrate counting and 150 minutes of weekly exercise. Repeat
{HbA1c} in 3 months, retinal exam ordered, urine albumin-to-creatinine ratio today.
""",
    "progress_knee": """
ORTHOPEDIC CLINIC NOTE

{age_sex} with {side} knee pain for {weeks} weeks after a twisting injury while {activity}.
Reports intermittent locking, swelling after activity and no instability. Exam: small
effusion, joint line tenderness, positive McMurray test, Lachman stable, range of motion
0-{rom} degrees.

MRI of the {side} knee was reviewed with the patient and shows {mri_summary}.

Assessment: {assessment}. Plan: physical therapy for quadriceps strengthening, NSAIDs as
needed and activity modification. Arthroscopic partial meniscectomy discussed if mechanical
symptoms persist after 6 weeks of therapy. Return in 6 weeks.
""",
    "progress_headache": """
NEUROLOGY PROGRESS NOTE

{age_sex} seen for {weeks} weeks of daily bifrontal pressure-like headaches, worse at the
end of the day. No fever, weight loss, visual loss or focal weakness. Neurologic exam is
nonfocal and fundi show no papilledema. Because the headache pattern was new, a
non-contrast CT head was obtained and showed {ct_summary}.

Assessment: tension-type headache with a probable medication-overuse component from daily
{analgesic}. Plan: limit analgesics to 2 days per week, start amitriptyline 10 mg at night,
sleep hygiene and a headache diary. Return in 8 weeks, sooner for red-flag symptoms.
""",
    "progress_wellness": """
ANNUAL WELLNESS VISIT

{age_sex} presenting for routine preventive care with no acute complaints. Exercises {ex}
times per week, never smoker, alcohol {alcohol}. Vitals: BP {sbp}/{dbp}, HR {hr}, BMI {bmi}.
Physical exam is unremarkable.

Screening labs including hemoglobin and creatinine were within normal limits. Immunizations
updated today ({vaccine}). Counseled on diet, physical activity, sleep and sun protection.
Age-appropriate cancer screening discussed and ordered. Follow-up in one year.
""",
    "cxr_normal": """
EXAM: Chest X-ray, PA and lateral.

INDICATION: {indication}.

FINDINGS: The lungs are clear without focal consolidation, pleural effusion or
pneumothorax. The cardiomediastinal silhouette is within normal limits. No acute osseous
abnormality.

IMPRESSION: No acute cardiopulmonary process. Normal chest radiograph.
""",
    "cxr_consolidation": """
EXAM: Chest X-ray, PA and lateral.

INDICATION: {indication}.

FINDINGS: Dense airspace consolidation in the {lobe} lobe with air bronchograms. {effusion}
Heart size is normal. No pneumothorax.

IMPRESSION: {lobe_cap} lobe consolidation consistent with pneumonia in the appropriate
clinical setting. Recommend follow-up radiograph in 6-8 weeks to document resolution.
""",
    "cxr_cardiomegaly": """
EXAM: Chest X-ray, portable AP.

INDICATION: {indication}.

FINDINGS: The cardiac silhouette is enlarged with a cardiothoracic ratio of about {ctr}.
Cephalization of the pulmonary vessels and interstitial edema with Kerley B lines. Small
bilateral pleural effusions. No focal consolidation.

IMPRESSION: Cardiomegaly with pulmonary vascular congestion and mild interstitial edema,
consistent with {CHF}.
""",
    "cxr_copd": """
EXAM: Chest X-ray, PA and lateral.

INDICATION: {indication}.

FINDINGS: Hyperinflated lungs with flattened hemidiaphragms and increased retrosternal air
space. No focal consolidation, effusion or pneumothorax. Heart size is normal.

IMPRESSION: Hyperinflation consistent with {COPD}. No radiographic evidence of pneumonia.
""",
    "ct_head": """
EXAM: CT head without contrast.

INDICATION: {indication}.

FINDINGS: No acute intracranial hemorrhage, mass effect or midline shift. Gray-white
differentiation is preserved. {ct_extra} The calvarium is intact. The visualized paranasal
sinuses and mastoid air cells are clear.

IMPRESSION: No acute intracranial abnormality.{ct_imp_extra}
""",
    "mri_knee": """
EXAM: MRI {side} knee without contrast.

INDICATION: Knee pain and locking after a twisting injury.

FINDINGS: {meniscus} The anterior and posterior cruciate ligaments are intact. The medial
and lateral collateral ligaments are intact. {cartilage} Small joint effusion. No bone
marrow edema to suggest fracture.

IMPRESSION: {impression}
""",
}

IMAGING = {  # template -> (modality, study description)
    "cxr_normal": ("XR", "Chest X-ray 2 views"),
    "cxr_consolidation": ("XR", "Chest X-ray 2 views"),
    "cxr_cardiomegaly": ("XR", "Chest X-ray portable"),
    "cxr_copd": ("XR", "Chest X-ray 2 views"),
    "ct_head": ("CT", "CT head without contrast"),
    "mri_knee": ("MR", "MRI knee without contrast"),
}


@dataclass(frozen=True)
class LabSpec:
    loinc: str
    unit: str
    ref: str
    decimals: int
    ranges: dict[str, tuple[float, float]]  # flag -> value range


LABS = {
    "HbA1c": LabSpec("4548-4", "%", "4.0-5.6", 1, {"N": (4.6, 5.5), "H": (7.4, 11.8)}),
    "Glucose": LabSpec("2345-7", "mg/dL", "70-99", 0, {"N": (74, 97), "H": (150, 260)}),
    "Creatinine": LabSpec("2160-0", "mg/dL", "0.60-1.30", 2, {"N": (0.7, 1.2), "H": (1.5, 2.4)}),
    "Hemoglobin": LabSpec("718-7", "g/dL", "12.0-16.0", 1, {"N": (12.4, 15.4), "L": (7.2, 9.6)}),
    "WBC": LabSpec("6690-2", "10^3/uL", "4.0-11.0", 1, {"N": (4.8, 9.6), "H": (13.5, 21.0)}),
    "Troponin I": LabSpec("10839-9", "ng/mL", "0.00-0.04", 2, {"N": (0.0, 0.03), "H": (0.06, 0.4)}),
    "Potassium": LabSpec(
        "2823-3", "mmol/L", "3.5-5.1", 1, {"N": (3.7, 4.9), "H": (5.4, 6.2), "L": (2.8, 3.3)}
    ),
}  # fmt: skip
MALE_HGB = ("13.5-17.5", (13.8, 16.6))


def _pneumonia(rng: random.Random) -> dict[str, Any]:
    lobe = rng.choice(["right lower", "left lower", "right middle", "right upper"])
    wbc = round(rng.uniform(13.5, 21.0), 1)
    return {
        "lobe": lobe,
        "lobe_cap": lobe.capitalize(),
        "days": rng.randint(2, 6),
        "temp": round(rng.uniform(38.3, 39.6), 1),
        "spo2": rng.randint(88, 93),
        "spo2_fu": rng.randint(95, 98),
        "wbc": wbc,
        "comorb": rng.choice(["hypertension", "hyperlipidemia", "hypothyroidism", "obesity"]),
        "abx2": rng.choice(["azithromycin", "doxycycline"]),
        "abx_po": rng.choice(["amoxicillin-clavulanate", "cefpodoxime"]),
        "fever_day": rng.randint(2, 3),
        "los": rng.randint(3, 6),
        "course": rng.choice([5, 7]),
        "days_after": rng.randint(7, 14),
        "effusion": rng.choice(["No pleural effusion.", "Small adjacent parapneumonic effusion."]),
        "indication": "Fever, productive cough and shortness of breath",
        "labs": {"WBC": wbc},
    }


def _chf(rng: random.Random) -> dict[str, Any]:
    trop, creat = round(rng.uniform(0.06, 0.15), 2), round(rng.uniform(1.5, 2.2), 2)
    return {
        "ef": rng.choice([20, 25, 30, 35]),
        "comorb": rng.choice(["atrial fibrillation", "chronic kidney disease", "obesity"]),
        "gain": rng.randint(3, 6),
        "bnp": rng.randint(900, 3200),
        "trop": trop,
        "creat": creat,
        "net": rng.randint(3, 7),
        "wt": rng.randint(68, 112),
        "furo": rng.choice([40, 80]),
        "ctr": rng.choice(["0.58", "0.61", "0.64"]),
        "los": rng.randint(4, 7),
        "days_after": rng.randint(7, 12),
        "indication": "Shortness of breath and leg swelling",
        "labs": {"Troponin I": trop, "Creatinine": creat},
    }


def _dka(rng: random.Random) -> dict[str, Any]:
    glu, a1c, k = rng.randint(380, 620), round(rng.uniform(10.5, 13.5), 1), rng.uniform(5.4, 6.1)
    return {
        "trigger": rng.choice(
            ["missed insulin doses", "a urinary tract infection", "viral gastroenteritis"]
        ),
        "days": rng.randint(2, 4),
        "glu": glu,
        "gap": rng.randint(20, 28),
        "bicarb": rng.randint(6, 12),
        "k": round(k, 1),
        "a1c": a1c,
        "hours": rng.randint(12, 24),
        "glargine": rng.randint(18, 30),
        "los": rng.randint(2, 4),
        "labs": {"Glucose": glu, "HbA1c": a1c, "Potassium": round(k, 1)},
    }


def _hip(rng: random.Random) -> dict[str, Any]:
    hgb = round(rng.uniform(7.4, 9.4), 1)
    transfused = (
        "received one unit of packed red cells" if hgb < 8 else "was monitored without transfusion"
    )
    return {
        "side": rng.choice(["left", "right"]),
        "fall_place": rng.choice(
            ["over a rug in the kitchen", "on the bathroom floor", "on an icy front step"]
        ),
        "op_day": rng.randint(1, 2),
        "hgb": hgb,
        "transfused": transfused,
        "feet": rng.choice([30, 50, 80, 120]),
        "dispo": rng.choice(
            ["skilled nursing facility for rehabilitation", "home with home physical therapy"]
        ),
        "los": rng.randint(4, 7),
        "indication": "Fall with minor head strike",
        "ct_extra": "Ventricles and sulci are age-appropriate.",
        "ct_imp_extra": "",
        "labs": {"Hemoglobin": hgb},
    }


def _copd(rng: random.Random) -> dict[str, Any]:
    wbc = round(rng.uniform(6.0, 9.5), 1)
    return {
        "packs": rng.choice([30, 40, 45, 60]),
        "fev1": rng.randint(35, 60),
        "days": rng.randint(3, 7),
        "spo2": rng.randint(86, 91),
        "wbc": wbc,
        "los": rng.randint(3, 5),
        "indication": "Worsening dyspnea and wheeze",
        "labs": {"WBC": wbc},
    }


KNEE_FINDINGS = [
    {
        "meniscus": "Horizontal tear of the posterior horn of the medial meniscus extending to "
        "the inferior articular surface.",
        "cartilage": "Mild chondral thinning of the medial femoral condyle.",
        "impression": "Tear of the posterior horn of the medial meniscus. Mild medial "
        "compartment chondrosis.",
        "mri_summary": "a posterior horn medial meniscal tear with mild cartilage thinning",
        "assessment": "symptomatic medial meniscal tear",
    },
    {
        "meniscus": "Radial tear of the body of the lateral meniscus.",
        "cartilage": "Articular cartilage is preserved.",
        "impression": "Radial tear of the lateral meniscal body. No ligament injury.",
        "mri_summary": "a radial tear of the lateral meniscus with intact ligaments",
        "assessment": "lateral meniscal tear",
    },
]


def _knee(rng: random.Random) -> dict[str, Any]:
    return {
        "side": rng.choice(["left", "right"]),
        "weeks": rng.randint(3, 10),
        "activity": rng.choice(
            ["playing soccer", "pivoting at work", "hiking downhill", "playing basketball"]
        ),
        "rom": rng.choice([110, 120, 130]),
        **rng.choice(KNEE_FINDINGS),
        "labs": {},
    }


def _headache(rng: random.Random) -> dict[str, Any]:
    small_vessel = rng.random() < 0.5
    return {
        "weeks": rng.randint(4, 12),
        "analgesic": rng.choice(["ibuprofen", "acetaminophen", "a combination analgesic"]),
        "ct_summary": "mild chronic small vessel ischemic change without acute abnormality"
        if small_vessel
        else "no acute intracranial abnormality",
        "ct_extra": "Mild periventricular white matter hypodensities."
        if small_vessel
        else "Ventricles and sulci are normal in size.",
        "ct_imp_extra": " Mild chronic small vessel ischemic change." if small_vessel else "",
        "indication": "New daily headache",
        "labs": {},
    }


def _t2dm(rng: random.Random) -> dict[str, Any]:
    a1c, fbg_lo = round(rng.uniform(7.6, 9.8), 1), rng.randint(130, 150)
    return {
        "years": rng.randint(3, 15),
        "met": rng.choice([500, 1000]),
        "fbg_lo": fbg_lo,
        "fbg_hi": fbg_lo + rng.randint(40, 80),
        "sbp": rng.randint(128, 148),
        "dbp": rng.randint(78, 92),
        "bmi": round(rng.uniform(29.0, 36.0), 1),
        "a1c": a1c,
        "add_drug": rng.choice(
            [
                "empagliflozin 10 mg daily",
                "semaglutide 0.25 mg weekly with monthly titration",
                "sitagliptin 100 mg daily",
            ]
        ),
        "labs": {"HbA1c": a1c},
    }


def _wellness(rng: random.Random) -> dict[str, Any]:
    return {
        "ex": rng.randint(2, 5),
        "alcohol": rng.choice(["none", "1-2 drinks per week", "occasionally"]),
        "sbp": rng.randint(112, 128),
        "dbp": rng.randint(68, 80),
        "hr": rng.randint(58, 78),
        "bmi": round(rng.uniform(21.0, 27.0), 1),
        "vaccine": rng.choice(["influenza", "Tdap", "influenza and COVID-19"]),
        "indication": "Pre-employment screening",
        "labs": {},
    }


@dataclass(frozen=True)
class Story:
    make_ctx: Callable[[random.Random], dict[str, Any]]
    discharge: str | None  # template key; inpatient stories only
    title: str  # document title / note type
    progress: str | None  # template for a follow-up note; None if the story never has one
    imaging: tuple[str, ...]
    labs: tuple[tuple[str, str], ...]  # (test, flag)


STORIES = {
    "pneumonia": Story(
        _pneumonia,
        "discharge_pneumonia",
        "Community-acquired pneumonia",
        "progress_pneumonia",
        ("cxr_consolidation",),
        (("WBC", "H"),),
    ),
    "chf": Story(
        _chf,
        "discharge_chf",
        "Heart failure exacerbation",
        "progress_chf",
        ("cxr_cardiomegaly",),
        (("Troponin I", "H"), ("Potassium", "L"), ("Creatinine", "H")),
    ),
    "dka": Story(
        _dka,
        "discharge_dka",
        "Diabetic ketoacidosis",
        None,
        (),
        (("Glucose", "H"), ("HbA1c", "H"), ("Potassium", "H")),
    ),
    "hip": Story(
        _hip,
        "discharge_hip",
        "Hip fracture",
        None,
        ("ct_head",),
        (("Hemoglobin", "L"),),
    ),
    "copd": Story(
        _copd,
        "discharge_copd",
        "COPD exacerbation",
        None,
        ("cxr_copd",),
        (("WBC", "N"),),
    ),
    "knee": Story(_knee, None, "Orthopedic Clinic Note", "progress_knee", ("mri_knee",), ()),
    "t2dm": Story(
        _t2dm, None, "Progress Note", "progress_t2dm", (), (("HbA1c", "H"), ("Glucose", "H"))
    ),
    "headache": Story(
        _headache, None, "Neurology Progress Note", "progress_headache", ("ct_head",), ()
    ),
    "wellness": Story(
        _wellness,
        None,
        "Annual Wellness Visit",
        "progress_wellness",
        ("cxr_normal",),
        (("Hemoglobin", "N"), ("Creatinine", "N")),
    ),
}
NOTE_TYPES = {
    "pneumonia": "Follow-up Note",
    "chf": "Heart Failure Clinic Note",
    "knee": "Orthopedic Clinic Note",
    "t2dm": "Progress Note",
    "headache": "Neurology Progress Note",
    "wellness": "Annual Wellness Visit",
}


def render_text(template: str, ctx: dict[str, Any], style: str) -> str:
    """Fill a template, pick abbreviated or full terms, and tidy paragraphs."""
    tokens = {k: v[0 if style == "abbr" else 1] for k, v in ABBREVIATIONS.items()}
    text = template.format(**ctx, **tokens)
    paragraphs = [" ".join(p.split()) for p in text.strip().split("\n\n")]
    text = "\n".join(paragraphs)
    return re.sub(r"(^|[.!?:]\s+)([a-z])", lambda m: m[1] + m[2].upper(), text, flags=re.M)


# ---------------------------------------------------------------------------
# Cast: 18 canonical patients, planned by hand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatientSpec:
    story: str
    sex: str  # "male" | "female": drives the name and the lab reference range
    gender_style: str  # how ehr_export.json writes gender (see GENDER_RAW)
    in_json: bool = True
    labs_to: str = "csv"  # "csv" | "json"
    has_note: bool = False
    dob_years: tuple[int, int] = DOB_YEARS
    ambiguous_dob: bool = False  # day <= 12, so MM/DD vs DD/MM is ambiguous


GENDER_RAW = {  # style -> (male, female) as written in the raw file
    "word": ("male", "female"),
    "abbr": ("M", "F"),
    "title": ("Male", "Female"),
    "code": ("1", "2"),
    "U": ("U", "U"),
    "empty": ("", ""),
}
UNKNOWN_GENDER_STYLES = {"U", "empty", "missing"}

CAST = [
    PatientSpec("chf", "male", "abbr"),  # P01  7: nickname + 2nd MRN in CSV
    PatientSpec("pneumonia", "female", "abbr", has_note=True),  # P02  12: JSON+CSV+TXT
    PatientSpec("dka", "male", "word", labs_to="json", ambiguous_dob=True),  # P03  8 (A), 1
    PatientSpec("t2dm", "female", "none", in_json=False, has_note=True),  # P04  8 (B)
    PatientSpec("copd", "female", "title", labs_to="json"),  # P05  2: DOB in the future
    PatientSpec("hip", "male", "code"),  # P06  2: CT before DOB, FHIR failure
    PatientSpec("knee", "female", "code", labs_to="", has_note=True),  # P07  9: no DOB
    PatientSpec("headache", "male", "missing", labs_to="", has_note=True),  # P08  9: no gender
    PatientSpec("wellness", "female", "U"),  # P09  9: undated document, 1
    PatientSpec("pneumonia", "male", "empty"),  # P10  4, 1
    PatientSpec("chf", "male", "word", labs_to="json", has_note=True),  # P11  4, 1
    PatientSpec("copd", "female", "word"),  # P12  6: whitespace near-duplicate
    PatientSpec("dka", "female", "word"),  # P13  6: case near-duplicate, 1
    PatientSpec("hip", "female", "word", labs_to="json"),  # P14  10: title + suffix
    PatientSpec("t2dm", "male", "abbr", has_note=True),  # P15  10, 11
    PatientSpec("knee", "male", "title", labs_to="", has_note=True),  # P16  11: NBSP
    PatientSpec("wellness", "female", "word", has_note=True),  # P17  5, 9, 11: CRLF note
    PatientSpec("headache", "male", "word", labs_to="", has_note=True,
                dob_years=(1980, 2004)),  # P18  1: two-digit-year DOB, so born after 1979
]  # fmt: skip
NICKNAMES = [("Jonathan", "Jon"), ("William", "Bill"), ("Robert", "Bob"), ("Michael", "Mike")]


@dataclass
class Patient:
    key: str
    spec: PatientSpec
    given: str
    family: str
    dob: date  # generator truth; used for ages in the narrative
    mrn: int
    episode: date
    ctx: dict[str, Any]
    expected_dob: date | None = None  # what a correct cleaner should end up with
    nickname: str | None = None
    alias_mrn: int | None = None

    @property
    def expected_gender(self) -> str:
        if not self.spec.in_json or self.spec.gender_style in UNKNOWN_GENDER_STYLES:
            return "unknown"
        return self.spec.sex

    def age_on(self, d: date) -> int:
        return d.year - self.dob.year - ((d.month, d.day) < (self.dob.month, self.dob.day))


def mrn_str(n: int) -> str:
    return f"MRN-{n:06d}"


def build_patients(rng: random.Random, fake: Faker) -> list[Patient]:
    mrns = rng.sample(range(1000, 99999), len(CAST) + 1)
    formal, nickname = rng.choice(NICKNAMES)
    families: set[str] = set()
    patients = []
    for i, spec in enumerate(CAST):
        family = fake.last_name()
        while family in families:
            family = fake.last_name()
        families.add(family)
        given = fake.first_name_male() if spec.sex == "male" else fake.first_name_female()
        while given in (formal, nickname):
            given = fake.first_name_male() if spec.sex == "male" else fake.first_name_female()
        dob = random_date(rng, date(spec.dob_years[0], 1, 1), date(spec.dob_years[1], 12, 31))
        if spec.ambiguous_dob:
            dob = dob.replace(day=rng.choice([d for d in range(1, 13) if d != dob.month]))
        p = Patient(
            key=f"P{i + 1:02d}",
            spec=spec,
            given=given,
            family=family,
            dob=dob,
            mrn=mrns[i],
            episode=random_date(rng, FIRST_EPISODE, LAST_EPISODE),
            ctx=STORIES[spec.story].make_ctx(rng),
            expected_dob=dob,
        )
        patients.append(p)
    p01 = patients[0]
    p01.given, p01.nickname = formal, nickname
    p01.alias_mrn = mrns[-1]
    return patients


# ---------------------------------------------------------------------------
# Clean raw dataset
# ---------------------------------------------------------------------------


@dataclass
class Note:
    rid: str
    key: str
    when: datetime
    header: dict[str, str]  # field -> value; a missing field is simply absent
    labels: dict[str, str]  # field -> label as written, e.g. "D.O.B.:"
    order: list[str]
    body: str
    filename: str = ""
    crlf: bool = False

    def render(self) -> bytes:
        lines = [f"{self.labels[f]} {self.header[f]}" for f in self.order if f in self.header]
        lines += ["", *self.body.split("\n"), ""]
        return ("\r\n" if self.crlf else "\n").join(lines).encode("utf-8")


@dataclass
class Defect:
    id: str
    rid: str
    detail: str
    expected: str


@dataclass
class Dataset:
    patients: list[Patient]
    json_patients: list[dict[str, Any]] = field(default_factory=list)
    csv_rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    owner: dict[str, str] = field(default_factory=dict)  # rid -> patient key
    _n: int = 0
    _texts: int = 0

    def rid(self, key: str) -> str:
        self._n += 1
        rid = f"r{self._n:03d}"
        self.owner[rid] = key
        return rid

    def text_style(self) -> str:
        """Alternate abbreviated and full-term writing so both styles always appear."""
        self._texts += 1
        return "abbr" if self._texts % 2 else "full"

    def patient(self, key: str) -> Patient:
        return next(p for p in self.patients if p.key == key)

    def json_of(self, key: str) -> dict[str, Any]:
        return next(o for o in self.json_patients if o["_key"] == key)

    def rows_of(self, key: str) -> list[dict[str, Any]]:
        return [r for r in self.csv_rows if r["_key"] == key]

    def note_of(self, key: str) -> Note:
        return next(n for n in self.notes if n.key == key)

    def log(self, defect_id: str, rid: str, detail: str, expected: str) -> None:
        self.defects.append(Defect(defect_id, rid, detail, expected))


NOTE_LABELS = {
    "patient": ["Patient:", "PATIENT:", "Patient Name:"],
    "mrn": ["MRN:", "MRN #:", "Mrn:"],
    "dob": ["DOB:", "D.O.B.:", "Dob:"],
    "date": ["Date:", "DATE:", "Date of Service:"],
    "type": ["Type:", "TYPE:", "Note Type:"],
}


def json_patient(ds: Dataset, p: Patient, fake: Faker) -> dict[str, Any]:
    return {
        "_key": p.key,
        "_rid": ds.rid(p.key),
        "mrn": mrn_str(p.mrn),
        "name": f"{p.given} {p.family}",
        "dob": fmt_date(p.dob),
        "gender": p.spec.sex,
        "address": {
            "line": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "postalCode": fake.zipcode(),
        },
        "phone": fake.phone_number(),
        "documents": [],
        "labs": [],
        "imaging": [],
    }


def narrative_ctx(p: Patient, when: date, style: str) -> dict[str, Any]:
    age = p.age_on(when)
    gender = p.expected_gender
    if style == "abbr":
        sex = {"male": "M", "female": "F"}.get(gender, "pt")
        age_sex = f"{age} yo {sex}"
    else:
        age_sex = f"{age}-year-old " + {"male": "man", "female": "woman"}.get(gender, "patient")
    return {**p.ctx, "age_sex": age_sex}


def text_for(ds: Dataset, p: Patient, template: str, when: date) -> str:
    style = ds.text_style()
    return render_text(TEMPLATES[template], narrative_ctx(p, when, style), style)


def lab_value(p: Patient, test: str, flag: str, rng: random.Random) -> tuple[float, str]:
    spec = LABS[test]
    ref, lo_hi = spec.ref, spec.ranges[flag]
    if test == "Hemoglobin" and p.spec.sex == "male":
        ref = MALE_HGB[0]
        if flag == "N":
            lo_hi = MALE_HGB[1]
    value = p.ctx["labs"].get(test, rng.uniform(*lo_hi))
    return round(value, spec.decimals), ref


def fmt_value(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def add_records(ds: Dataset, p: Patient, rng: random.Random, fake: Faker) -> None:
    """Create one patient's clinical records in their planned sources, all clean."""
    story = STORIES[p.spec.story]
    obj = ds.json_of(p.key) if p.spec.in_json else None
    start = p.episode
    end = start + timedelta(days=p.ctx.get("los", 0))
    follow_up = end + timedelta(days=p.ctx.get("days_after", rng.randint(3, 10)))

    if story.discharge and obj is not None:
        t = at_time(end, rng)
        obj["documents"].append(
            {
                "_rid": ds.rid(p.key),
                "_date": t,
                "id": f"DOC-{rng.randint(100000, 999999)}",
                "type": "Discharge Summary",
                "date": json_ts(t),
                "author": f"Dr. {fake.last_name()}",
                "title": f"Discharge Summary - {story.title}",
                "text": text_for(ds, p, story.discharge, end),
            }
        )
    if p.spec.has_note:
        add_note(ds, p, story, at_time(follow_up, rng), rng)
    elif not story.discharge and obj is not None:
        t = at_time(follow_up, rng)
        obj["documents"].append(
            {
                "_rid": ds.rid(p.key),
                "_date": t,
                "id": f"DOC-{rng.randint(100000, 999999)}",
                "type": "Progress Note",
                "date": json_ts(t),
                "author": f"Dr. {fake.last_name()}",
                "title": story.title,
                "text": text_for(ds, p, story.progress, follow_up),
            }
        )
    if obj is not None:
        for template in story.imaging:
            t = at_time(start, rng)
            modality, study = IMAGING[template]
            obj["imaging"].append(
                {
                    "_rid": ds.rid(p.key),
                    "_date": t,
                    "id": f"IMG-{rng.randint(100000, 999999)}",
                    "modality": modality,
                    "study": study,
                    "date": json_ts(t),
                    "report": text_for(ds, p, template, start),
                }
            )
    for test, flag in story.labs:
        t = at_time(start, rng)
        value, ref = lab_value(p, test, flag, rng)
        spec = LABS[test]
        if p.spec.labs_to == "json" and obj is not None:
            obj["labs"].append(
                {
                    "_rid": ds.rid(p.key),
                    "_date": t,
                    "id": f"LAB-{rng.randint(100000, 999999)}",
                    "code": spec.loinc,
                    "test": test,
                    "value": value,
                    "unit": spec.unit,
                    "refRange": ref,
                    "flag": flag,
                    "collectedAt": json_ts(t),
                }
            )
        elif p.spec.labs_to == "csv":
            ds.csv_rows.append(
                {
                    "_rid": ds.rid(p.key),
                    "_key": p.key,
                    "_date": t,
                    "mrn": mrn_str(p.mrn),
                    "patient_name": f"{p.given} {p.family}",
                    "dob": fmt_date(p.dob),
                    "test": test,
                    "value": fmt_value(value, spec.decimals),
                    "unit": spec.unit,
                    "ref_range": ref,
                    "flag": flag,
                    "collected_at": csv_ts(t),
                }
            )


def add_note(ds: Dataset, p: Patient, story: Story, when: datetime, rng: random.Random) -> None:
    order = ["patient", "mrn", "dob", "date", "type"]
    if rng.random() < 0.4:  # a little header reordering, as scanned forms vary
        i = rng.randrange(len(order) - 1)
        order[i], order[i + 1] = order[i + 1], order[i]
    ds.notes.append(
        Note(
            rid=ds.rid(p.key),
            key=p.key,
            when=when,
            header={
                "patient": f"{p.given} {p.family}",
                "mrn": mrn_str(p.mrn),
                "dob": fmt_date(p.dob),
                "date": fmt_date(when.date()),
                "type": NOTE_TYPES[p.spec.story],
            },
            labels={f: rng.choice(v) for f, v in NOTE_LABELS.items()},
            order=order,
            body=text_for(ds, p, story.progress, when.date()),
        )
    )


def build_dataset(patients: list[Patient], rng: random.Random, fake: Faker) -> Dataset:
    ds = Dataset(patients=patients)
    ds.json_patients = [json_patient(ds, p, fake) for p in patients if p.spec.in_json]
    for p in patients:
        add_records(ds, p, rng, fake)
    ds.csv_rows.sort(key=lambda r: (r["_date"], r["_rid"]))
    ds.notes.sort(key=lambda n: (n.when, n.rid))
    for i, note in enumerate(ds.notes, start=1):
        note.filename = f"scan_{i:03d}.txt"
    return ds


# ---------------------------------------------------------------------------
# Defect injection: one function per defect family, every instance logged
# ---------------------------------------------------------------------------


def inject_date_formats(ds: Dataset, rng: random.Random) -> None:  # defect 1
    for key, style in (("P03", "us"), ("P09", "compact"), ("P10", "long"), ("P18", "dmy2")):
        obj, p = ds.json_of(key), ds.patient(key)
        obj["dob"] = fmt_date(p.dob, style)
        ds.log(
            "1",
            obj["_rid"],
            f"dob written as {DATE_STYLES[style]}: {q(obj['dob'])}",
            expected_date("dob", p.dob, style),
        )
    p10 = ds.patient("P10")
    for row in ds.rows_of("P10"):
        row["dob"] = fmt_date(p10.dob, "us")
        ds.log(
            "1",
            row["_rid"],
            f"dob written as MM/DD/YYYY: {q(row['dob'])}",
            expected_date("dob", p10.dob, "us"),
        )
    for key, style in (("P13", "us"), ("P09", "dmy2")):
        row = ds.rows_of(key)[0]
        row["collected_at"] = csv_ts(row["_date"], style)
        ds.log(
            "1",
            row["_rid"],
            f"collected_at written as {DATE_STYLES[style]} HH:MM: {q(row['collected_at'])}",
            expected_date("collected_at", row["_date"].date(), style),
        )
    lab = ds.json_of("P11")["labs"][0]
    lab["collectedAt"] = fmt_date(lab["_date"].date(), "compact")
    ds.log(
        "1",
        lab["_rid"],
        f"collectedAt written as YYYYMMDD with no time: {q(lab['collectedAt'])}",
        expected_date("collectedAt", lab["_date"].date(), "compact"),
    )
    dob_styles = ["us", "iso", "long", "us", "compact", "iso", "long", "us", "iso"]
    date_styles = ["long", "us", "iso", "dmy2", "us", "long", "iso", "dmy2", "us"]
    for note, dob_style, date_style in zip(ds.notes, dob_styles, date_styles, strict=True):
        p = ds.patient(note.key)
        for fld, d, style in (("dob", p.dob, dob_style), ("date", note.when.date(), date_style)):
            if fld not in note.header:
                continue
            note.header[fld] = fmt_date(d, style)
            if style != "iso":
                ds.log(
                    "1",
                    note.rid,
                    f"header {fld.upper()} written as {DATE_STYLES[style]}: {q(note.header[fld])}",
                    expected_date(fld, d, style),
                )


def inject_impossible_dates(ds: Dataset, rng: random.Random) -> None:  # defect 2
    p05, obj = ds.patient("P05"), ds.json_of("P05")
    future = date(rng.randint(2030, 2035), p05.dob.month, min(p05.dob.day, 28))
    obj["dob"] = fmt_date(future)
    p05.expected_dob = None
    ds.log(
        "2",
        obj["_rid"],
        f"dob is in the future: {q(obj['dob'])}",
        "dob -> null, audit `dob_in_future`; patient kept",
    )
    p06 = ds.patient("P06")
    study = ds.json_of("P06")["imaging"][0]
    study["_date"] = at_time(p06.dob - timedelta(days=rng.randint(400, 3000)), rng)
    study["date"] = json_ts(study["_date"])
    ds.log(
        "2",
        study["_rid"],
        f"imaging date {q(study['date'])} is before the patient's DOB {p06.dob.isoformat()}",
        "record kept, date -> null, audit `date_before_dob`",
    )


def inject_gender_variants(ds: Dataset, rng: random.Random) -> None:  # defects 3 and 9
    for obj in ds.json_patients:
        p = ds.patient(obj["_key"])
        style = p.spec.gender_style
        if style == "missing":
            del obj["gender"]
            ds.log("9", obj["_rid"], "gender field absent", "gender -> `unknown`")
            continue
        male, female = GENDER_RAW[style]
        obj["gender"] = male if p.spec.sex == "male" else female
        if style != "word":
            ds.log(
                "3",
                obj["_rid"],
                f"gender written as {q(obj['gender'])}"
                + (" (1=male, 2=female, ISO/IEC 5218)" if style == "code" else ""),
                f"gender -> `{p.expected_gender}`",
            )


def inject_mrn_variants(ds: Dataset, rng: random.Random) -> None:  # defect 4
    p02 = ds.patient("P02")
    for row in ds.rows_of("P02"):
        row["mrn"] = f" {p02.mrn:06d} "
        ds.log("4", row["_rid"], "MRN padded with spaces, no prefix", f"mrn -> {mrn_str(p02.mrn)}")
    note = ds.note_of("P02")
    note.header["mrn"] = f"mrn-{p02.mrn:06d}"
    ds.log("4", note.rid, "MRN lower-case prefix", f"mrn -> {mrn_str(p02.mrn)}")
    for key, raw in (("P10", "MRN{n}"), ("P11", "{n}")):
        obj, p = ds.json_of(key), ds.patient(key)
        obj["mrn"] = raw.format(n=p.mrn)
        ds.log("4", obj["_rid"], "MRN not zero-padded", f"mrn -> {mrn_str(p.mrn)}")


def inject_exact_duplicate(ds: Dataset, rng: random.Random) -> None:  # defect 5
    original = next(r for r in ds.rows_of("P17") if r["test"] == "Hemoglobin")
    dup = {**original, "_rid": ds.rid("P17")}
    ds.csv_rows.append(dup)
    ds.duplicates.append(
        {"type": "exact", "defect_id": "5", "rids": [original["_rid"], dup["_rid"]]}
    )
    ds.log(
        "5",
        dup["_rid"],
        "lab row re-sent at the end of the file, identical in every column",
        "one copy dropped, audit `duplicate_dropped`",
    )


def inject_near_duplicates(ds: Dataset, rng: random.Random) -> None:  # defect 6
    study = ds.json_of("P12")["imaging"][0]
    ws_copy = {
        **study,
        "_rid": ds.rid("P12"),
        "id": f"IMG-{rng.randint(100000, 999999)}",
        "report": study["report"].replace(". ", ".  ") + "  \n\n",
    }
    ds.json_of("P12")["imaging"].append(ws_copy)
    doc = ds.json_of("P13")["documents"][0]
    case_copy = {
        **doc,
        "_rid": ds.rid("P13"),
        "id": f"DOC-{rng.randint(100000, 999999)}",
        "title": doc["title"].upper(),
        "text": doc["text"].upper(),
    }
    ds.json_of("P13")["documents"].append(case_copy)
    for original, dup, how in (
        (study, ws_copy, "imaging report re-exported with doubled spaces and trailing whitespace"),
        (doc, case_copy, "discharge summary re-exported in upper case with a new id"),
    ):
        ds.duplicates.append(
            {"type": "near", "defect_id": "6", "rids": [original["_rid"], dup["_rid"]]}
        )
        ds.log(
            "6",
            dup["_rid"],
            f"{how}; same date and patient",
            "dropped as near-duplicate (whitespace-collapsed, case-folded text match), "
            "audit `duplicate_dropped`",
        )


def inject_split_identity(ds: Dataset, rng: random.Random) -> None:  # defect 7
    p01 = ds.patient("P01")
    assert p01.nickname and p01.alias_mrn
    for row in ds.rows_of("P01"):
        row["mrn"] = mrn_str(p01.alias_mrn)
        row["patient_name"] = f"{p01.nickname} {p01.family}"
        ds.log(
            "7",
            row["_rid"],
            f"nickname and a second MRN; JSON has {q(p01.given + ' ' + p01.family)} "
            f"{mrn_str(p01.mrn)}, same DOB",
            f"merged into one patient with source_ids [{mrn_str(p01.mrn)}, "
            f"{mrn_str(p01.alias_mrn)}]",
        )


def inject_mrn_conflict(ds: Dataset, rng: random.Random) -> None:  # defect 8
    p03, p04 = ds.patient("P03"), ds.patient("P04")
    shared = ds.json_of("P03")["mrn"]
    p04.mrn = p03.mrn
    rows, note = ds.rows_of("P04"), ds.note_of("P04")
    for row in rows:
        row["mrn"] = shared
    note.header["mrn"] = shared
    for rid in [*(r["_rid"] for r in rows), note.rid]:
        ds.log(
            "8",
            rid,
            f"MRN {q(shared)} also belongs to {q(p03.given + ' ' + p03.family)} in JSON "
            "(different name and DOB)",
            "NOT merged: two patients; audit `mrn_conflict`",
        )


def inject_missing_fields(ds: Dataset, rng: random.Random) -> None:  # defect 9
    obj = ds.json_of("P07")
    del obj["dob"]
    ds.patient("P07").expected_dob = None
    ds.log("9", obj["_rid"], "dob field absent", "dob -> null")
    note = ds.note_of("P07")
    del note.header["dob"]
    ds.log("9", note.rid, "DOB header line absent", "dob stays null (no other source)")
    doc = ds.json_of("P09")["documents"][0]
    del doc["date"]
    ds.log("9", doc["_rid"], "document has no date field", "record kept, date -> null")
    row = next(r for r in ds.rows_of("P17") if r["test"] == "Creatinine")
    row["flag"] = ""
    ds.log(
        "9", row["_rid"], "flag blank on a normal result", "flag -> null (or `N` from ref range)"
    )


def inject_name_noise(ds: Dataset, rng: random.Random) -> None:  # defect 10
    p02 = ds.patient("P02")
    for row in ds.rows_of("P02"):
        row["patient_name"] = f"{p02.family.upper()}, {p02.given.upper()}"
        ds.log("10", row["_rid"], "name as FAMILY, GIVEN in capitals", _name(p02))
    for key, fmt in (("P14", "Dr. {g} {f} Jr."), ("P15", "{gl}  {fl}")):
        obj, p = ds.json_of(key), ds.patient(key)
        obj["name"] = fmt.format(g=p.given, f=p.family, gl=p.given.lower(), fl=p.family.lower())
        detail = "title and suffix" if key == "P14" else "lower case with a double space"
        ds.log("10", obj["_rid"], f"name with {detail}: {q(obj['name'])}", _name(p))


def _name(p: Patient) -> str:
    return f"given `{p.given}`, family `{p.family}`"


def inject_whitespace(ds: Dataset, rng: random.Random) -> None:  # defect 11
    p16, obj = ds.patient("P16"), ds.json_of("P16")
    obj["name"] = f"{p16.given}{NBSP}{p16.family}"
    ds.log("11", obj["_rid"], "non-breaking space (U+00A0) inside name", _name(p16))
    p02, note = ds.patient("P02"), ds.note_of("P02")
    note.header["patient"] = f"{p02.given}{NBSP}{p02.family}"
    ds.log("11", note.rid, "non-breaking space (U+00A0) inside header name", _name(p02))
    row = next(r for r in ds.rows_of("P15") if r["test"] == "HbA1c")
    row["test"], row["value"] = "HbA1c\t", "\t" + row["value"]
    ds.log("11", row["_rid"], "stray tabs in test and value", "test -> `HbA1c`, value parsed")
    note = ds.note_of("P17")
    note.crlf = True
    ds.log("11", note.rid, "whole file uses CRLF line endings", "parsed like LF files")
    note = ds.note_of("P07")
    note.labels = {f: label.replace(":", " :") for f, label in note.labels.items()}
    note.header = {f: f"  {v} " for f, v in note.header.items()}
    note.body = re.sub(r"(\w) (\w)", lambda m: m[1] + "  " + m[2], note.body, count=12)
    ds.log(
        "11",
        note.rid,
        "OCR spacing: space before colons, padded header values, doubled spaces in text",
        "header fields parsed and stripped",
    )


def inject_cross_source(ds: Dataset, rng: random.Random) -> None:  # defect 12
    obj = ds.json_of("P02")
    rids = [obj["_rid"], *(r["_rid"] for r in ds.rows_of("P02")), ds.note_of("P02").rid]
    for rid in rids:
        ds.log(
            "12",
            rid,
            "same patient in JSON, CSV and a TXT note with different MRN and name spellings",
            "all three linked to one canonical patient",
        )


def inject_fhir_failure(ds: Dataset, rng: random.Random) -> None:
    template = ds.rows_of("P06")[0]
    t = template["_date"] + timedelta(minutes=10)
    row = {
        **template,
        "_rid": ds.rid("P06"),
        "_date": t,
        "test": "",
        "value": "",
        "unit": "",
        "ref_range": "",
        "flag": "",
        "collected_at": csv_ts(t),
    }
    ds.csv_rows.append(row)
    ds.log(
        "FHIR",
        row["_rid"],
        "lab row with empty test name and no value",
        "ingested, then fails FHIR Observation validation (no code, no value) "
        "and lands in the validation report",
    )


INJECTIONS = [
    inject_missing_fields,  # first, so later injections never log a field that is then removed
    inject_date_formats,
    inject_impossible_dates,
    inject_gender_variants,
    inject_mrn_variants,
    inject_exact_duplicate,
    inject_near_duplicates,
    inject_split_identity,
    inject_mrn_conflict,
    inject_name_noise,
    inject_whitespace,
    inject_cross_source,
    inject_fhir_failure,
]

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "mrn", "patient_name", "dob", "test", "value", "unit", "ref_range", "flag", "collected_at",
]  # fmt: skip
RECORD_ARRAYS = ("documents", "labs", "imaging")


def schema_keys(i: int) -> dict[str, str]:
    """Field names for the i-th JSON patient: realistic, occasionally inconsistent."""
    return {
        "mrn": "MRN" if i % 4 == 3 else "mrn",
        "name": "patientName" if i % 5 == 4 else "name",
        "dob": ["dob", "birthDate", "DOB"][i % 3],
        "gender": "sex" if i % 2 else "gender",
        "address": "address",
        "phone": "phoneNumber" if i % 3 == 1 else "phone",
    }


def strip_hidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_hidden(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [strip_hidden(v) for v in value]
    return value


def export_json(ds: Dataset) -> list[dict[str, Any]]:
    out = []
    for i, obj in enumerate(ds.json_patients):
        keys = schema_keys(i)
        patient = {keys[k]: copy.deepcopy(obj[k]) for k in keys if k in obj}
        if i % 4 == 2:  # some patients carry a one-line address
            a = obj["address"]
            patient["address"] = f"{a['line']}, {a['city']}, {a['state']} {a['postalCode']}"
        for arr in RECORD_ARRAYS:
            patient[arr] = strip_hidden(obj[arr])
        out.append(patient)
    return out


@dataclass
class Index:
    location: dict[str, str] = field(default_factory=dict)  # rid -> location
    identity: dict[str, tuple[str, str]] = field(default_factory=dict)  # rid -> (mrn, name)
    demographics: set[str] = field(default_factory=set)  # rids of JSON patient objects


def build_index(ds: Dataset) -> Index:
    idx = Index()
    for i, obj in enumerate(ds.json_patients):
        ident = (obj["mrn"], obj["name"])
        idx.location[obj["_rid"]] = f"ehr_export.json#/{i}"
        idx.demographics.add(obj["_rid"])
        idx.identity[obj["_rid"]] = ident
        for arr in RECORD_ARRAYS:
            for j, rec in enumerate(obj[arr]):
                idx.location[rec["_rid"]] = f"ehr_export.json#/{i}/{arr}/{j}"
                idx.identity[rec["_rid"]] = ident
    for n, row in enumerate(ds.csv_rows, start=1):
        idx.location[row["_rid"]] = f"lab_results.csv#row={n}"
        idx.identity[row["_rid"]] = (row["mrn"], row["patient_name"])
    for note in ds.notes:
        idx.location[note.rid] = f"notes/{note.filename}"
        idx.identity[note.rid] = (note.header.get("mrn", ""), note.header.get("patient", ""))
    return idx


def sorted_defects(ds: Dataset) -> list[Defect]:
    return sorted(ds.defects, key=lambda d: int(d.id) if d.id.isdigit() else 99)


def normalize_mrn(raw: str) -> str:
    return mrn_str(int(re.sub(r"\D", "", raw)))


def ground_truth(ds: Dataset, idx: Index, seed: int) -> dict[str, Any]:
    patients = []
    for p in ds.patients:
        rids = [rid for rid, key in ds.owner.items() if key == p.key]
        demographics = [idx.location[r] for r in rids if r in idx.demographics]
        records = [idx.location[r] for r in rids if r not in idx.demographics]
        mrns = sorted({idx.identity[r][0] for r in rids})
        patients.append(
            {
                "key": p.key,
                "given_name": p.given,
                "family_name": p.family,
                "name": f"{p.given} {p.family}",
                "dob": p.expected_dob.isoformat() if p.expected_dob else None,
                "gender": p.expected_gender,
                "story": p.spec.story,
                "mrn_variants": mrns,
                "mrns_normalized": sorted({normalize_mrn(m) for m in mrns}),
                "name_variants": sorted({idx.identity[r][1] for r in rids}),
                "demographics": demographics,
                "records": sorted(records),
            }
        )
    p03 = ds.json_of("P03")
    return {
        "_comment": "Answer key for tests only. The ingestion code must never read this file.",
        "seed": seed,
        "patients": patients,
        "duplicates": [
            {
                "type": d["type"],
                "defect_id": d["defect_id"],
                "records": [idx.location[r] for r in d["rids"]],
            }
            for d in ds.duplicates
        ],
        "mrn_conflict": {
            "raw_mrn": p03["mrn"],
            "patients": ["P03", "P04"],
            "note": "Different names and DOBs on one MRN; must NOT be merged.",
        },
        "fhir_failure": next(idx.location[d.rid] for d in ds.defects if d.id == "FHIR"),
        "defects": [
            {
                "n": n,
                "id": d.id,
                "defect": DEFECT_NAMES[d.id],
                "detail": d.detail,
                "location": idx.location[d.rid],
                "patient": ds.owner[d.rid],
                "expected": d.expected,
            }
            for n, d in enumerate(sorted_defects(ds), start=1)
        ],
    }


def defects_markdown(ds: Dataset, idx: Index, seed: int) -> str:
    lines = [
        "# Injected defects",
        "",
        f"Generated by `scripts/generate_data.py --seed {seed}`. One row per injected instance.",
        "Raw values are JSON-quoted so spaces, tabs and non-breaking spaces (`\\u00a0`) show.",
        "Locations: `ehr_export.json#/<patient>/<array>/<i>`, `lab_results.csv#row=<n>`",
        "(1-based, header excluded), `notes/<file>`. The machine-readable answer key is",
        "`ground_truth.json` (tests only; never read by ingestion).",
        "",
        "| # | ID | Defect | Where | Expected cleaning result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for n, d in enumerate(sorted_defects(ds), start=1):
        mrn, name = idx.identity[d.rid]
        where = f"`{idx.location[d.rid]}` (MRN `{q(mrn)}`, name `{q(name)}`)"
        lines.append(
            f"| {n} | {d.id} | {DEFECT_NAMES[d.id]}: {d.detail} | {where} | {d.expected} |"
        )
    lines += [
        "",
        "## Format variants (not numbered defects)",
        "",
        "- `ehr_export.json` field names vary by patient: `mrn`/`MRN`, `name`/`patientName`,",
        "  `dob`/`birthDate`/`DOB`, `gender`/`sex`, `phone`/`phoneNumber`; some addresses are a",
        "  single string instead of an object.",
        "- Note header labels vary (`Patient:`/`PATIENT:`/`Patient Name:`, `DOB:`/`D.O.B.:`,",
        "  `Date:`/`Date of Service:`, ...) and adjacent header lines are sometimes swapped.",
        "- Clinical text alternates abbreviations (CXR, SOB, CHF, T2DM, HbA1c) and full terms.",
        "- Patient P06 has one imaging record dated before 2023 on purpose (defect 2).",
        "",
    ]
    return "\n".join(lines)


def export_csv(ds: Dataset) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(ds.csv_rows)
    return buf.getvalue()


def write_outputs(ds: Dataset, out: Path, seed: int) -> None:
    notes_dir = out / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for stale in notes_dir.glob("*.txt"):
        stale.unlink()
    idx = build_index(ds)
    raw_json = json.dumps(export_json(ds), indent=2, ensure_ascii=False) + "\n"
    (out / "ehr_export.json").write_bytes(raw_json.encode("utf-8"))
    (out / "lab_results.csv").write_bytes(export_csv(ds).encode("utf-8"))
    for note in ds.notes:
        (notes_dir / note.filename).write_bytes(note.render())
    (out / "DEFECTS.md").write_bytes(defects_markdown(ds, idx, seed).encode("utf-8"))
    truth = json.dumps(ground_truth(ds, idx, seed), indent=2) + "\n"
    (out / "ground_truth.json").write_bytes(truth.encode("utf-8"))


def summarize(ds: Dataset) -> dict[str, Any]:
    counts = {arr: sum(len(o[arr]) for o in ds.json_patients) for arr in RECORD_ARRAYS}
    total = sum(counts.values()) + len(ds.csv_rows) + len(ds.notes)
    return {
        "patients": len(ds.patients),
        "json_patients": len(ds.json_patients),
        "json_records": counts,
        "csv_rows": len(ds.csv_rows),
        "notes": len(ds.notes),
        "total_records": total,
        "defects": dict(Counter(d.id for d in sorted_defects(ds))),
    }


def generate(seed: int = 42, out: Path = DEFAULT_OUT) -> dict[str, Any]:
    """Build the dataset for `seed`, write every output file into `out`, return counts."""
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    ds = build_dataset(build_patients(rng, fake), rng, fake)
    for inject in INJECTIONS:
        inject(ds, rng)
    write_outputs(ds, Path(out), seed)
    return summarize(ds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    s = generate(args.seed, args.out)
    j = s["json_records"]
    print(f"Wrote synthetic EHR data to {args.out} (seed {args.seed})")
    print(f"  patients (canonical)  {s['patients']}")
    print(
        f"  ehr_export.json       {s['json_patients']} patients, {j['documents']} documents, "
        f"{j['labs']} labs, {j['imaging']} imaging"
    )
    print(f"  lab_results.csv       {s['csv_rows']} rows")
    print(f"  notes/*.txt           {s['notes']} notes")
    print(f"  total records         {s['total_records']}")
    print("  defects injected      " + ", ".join(f"{k}: {v}" for k, v in s["defects"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
