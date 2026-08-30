import requests
import json
from pathlib import Path
import os
from datetime import date
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import hashlib
import hmac
from dotenv import load_dotenv
from analysis import descriptive_analysis, iqr_anomaly_analysis
from database import connect, create_schema, load_resources, load_analysis_results

load_dotenv()

PSEUDONYM_KEY = os.environ.get("PSEUDONYM_KEY", "").encode()

if len(PSEUDONYM_KEY) <32:
    raise RuntimeError(
        "PSEUDONYM Key must be over 32 characters"
    )

base_url = 'https://hapi.fhir.org/baseR4'

CACHE_DIR = Path("data/cache")

COHORT_REFERENCE_DATE = date.fromisoformat(os.getenv("COHORT_REFERENCE_DATE", "2026-08-28"))
retry_policy = Retry(
    total=3,
    backoff_factor= 1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods = ("GET",),
    respect_retry_after_header=True
)

session = requests.Session()
session.headers.update({
    "Accept": "application/fhir+json",
    "User-Agent": "fhir-integration-practice/1.0"})
session.mount(
    "https://", HTTPAdapter(max_retries=retry_policy)
)

blood_pressure_codes = {"8480-6", "8462-4"}

def find_next_link(bundle):
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None

def fetch_pages(resource_type, params=None, max_pages=5, cache_name = None):
    cache_path = CACHE_DIR / cache_name if cache_name else None

    if cache_path and cache_path.exists():
        print(f"Using cache: {cache_path}")
        with cache_path.open(encoding='utf-8') as file:
            return json.load(file)

    url = f"{base_url}/{resource_type}"
    rows = []
    page = 0

    while url and page < max_pages:
        response = session.get(url, params=params if page == 0 else None, timeout=(5,30))
        response.raise_for_status()
        bundle = response.json()

        for entry in bundle.get("entry", []):
            resource = entry.get("resource")
            if resource:
                rows.append(resource)
        url = find_next_link(bundle)
        page += 1

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")

        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(rows, file, indent=2)

        temporary_path.replace(cache_path)
        print(f"Cached {len(rows)} resources: {cache_path}")

    return rows

def has_code(concept, expected_code):
    return any(
        coding.get("code") == expected_code for coding in (concept or {}).get("coding", [])
    )

def age_on(birth_date, reference_date):
    try:
        born = date.fromisoformat(birth_date)
    except (TypeError, ValueError):
        return None

    if born > reference_date:
        return None

    return (
        reference_date.year
        - born.year
        - (
            (reference_date.month, reference_date.day) < (born.month, born.day)
        )
    )

def reference_id(reference):
    if not reference:
        return None

    return reference.rstrip("/").split("/")[-1]

def pseudonymize_id(resource_type, resource_id):
    if not resource_type or not resource_id:
        return None

    canonical_id = f"{resource_type}/{resource_id}".encode()

    return hmac.new(
        PSEUDONYM_KEY,
        canonical_id,
        hashlib.sha256
    ).hexdigest()

def transform_patient(patient):
    patient_id = patient.get("id")
    age = age_on(patient.get("birthDate"), COHORT_REFERENCE_DATE)
    if not patient_id or age is None or age < 18:
        return None

    return {
        "id": pseudonymize_id(
            "Patient", patient_id
        ),
        "gender": patient.get("gender") or "unknown",
        "age": age
    }


def first_coding_code(concept):
    return next(
        (
            coding.get("code") for coding in (concept or {}).get("coding", []) if coding.get("code")
        ), None
    )

def transform_condition(condition):
    condition_id = condition.get("id")
    patient_id = reference_id(
        (condition.get("subject") or {}).get("reference")
    )

    target_code = next(
        (
            coding for coding in (condition.get("code") or {}).get("coding", []) if coding.get("code") == "I10"
        ), None
    )

    if not condition_id or not patient_id or not target_code:
        return None

    return {
        "id" : pseudonymize_id("Condition", condition_id),
        "subject":pseudonymize_id(
            "Patient", patient_id
        ),
        "system":target_code.get("system"),
        "code": target_code.get("code"),
        "clinicalStatus": first_coding_code(condition.get("clinicalStatus")),
        "verificationStatus":first_coding_code(condition.get("verificationStatus"))
    }

def generalize_month(date_time):
    try:
        return date.fromisoformat(date_time[:10]).strftime("%Y-%m")
    except (TypeError, ValueError):
        return None

def transform_observation(observation):
    observation_id = observation.get("id")
    patient_id = reference_id(
        (observation.get("subject") or {}).get("reference")
    )

    if not observation_id or not patient_id:
        return []

    sources = observation.get("component") or [observation]
    rows = []

    for component_index, source in enumerate(sources):
        coding = next(
            (coding for coding in (source.get("code") or {}).get("coding", []) if coding.get("code") in blood_pressure_codes
            ), None
        )

        if not coding:
            continue

        quantity = source.get("valueQuantity") or {}

        rows.append({
            "id": pseudonymize_id(
                "Observation", observation_id
            ),
            "component_index": component_index,
            "subject": pseudonymize_id(
                "Patient", patient_id
            ),
            "status": observation.get("status"),
            "effective_month": generalize_month(observation.get("effectiveDateTime")),
            "system": coding.get("system"),
            "code": coding.get("code"),
            "value": quantity.get("value"),
            "unit": quantity.get("unit")
        })

    return rows
if __name__ == "__main__":
    print("\n ========== 1) Extract ==========")
    conditions = fetch_pages(
        "Condition",
        {
            "code": "I10",
            "_count": 200
        },
        max_pages =1,
        cache_name = "conditions_i10.json"
    )

    eligible_conditions = [
        condition for condition in conditions if has_code(condition.get("clinicalStatus"), "active") and has_code(condition.get("verificationStatus"), "confirmed")
    ]

    patient_refs = {
        (condition.get("subject") or {}).get("reference") for condition in eligible_conditions
    }
    patient_refs.discard(None)

    patient_ids = sorted(
        reference.rstrip('/').split('/')[-1] for reference in patient_refs
    )

    patients = fetch_pages(
        "Patient",
        {"_id" : ','.join(patient_ids),
         "_count" : 100
        },
        max_pages= 2,
        cache_name="patients_i10.json"
    )

    patient_ages = {
        patient["id"]: age for patient in patients if patient.get("id")
        and (
            age := age_on(
                patient.get("birthDate"),
                COHORT_REFERENCE_DATE
            )
        ) is not None
    }

    adult_patient_ids = {
        patient_id for patient_id, age in patient_ages.items() if age >=18
    }

    adult_patients = [
        patient for patient in patients if patient.get("id") in adult_patient_ids
    ]

    adult_patient_refs = {
        f"Patient/{patient_id}" for patient_id in adult_patient_ids
    }

    adult_conditions = [
        condition for condition in eligible_conditions if (condition.get("subject") or {}).get("reference") in adult_patient_refs
    ]

    blood_pressure_observations = fetch_pages(
        "Observation",
        {
            "subject": ",".join(sorted(adult_patient_refs)),
            "combo-code": "85354-9,8480-6,8462-4",
            "date": f"le{COHORT_REFERENCE_DATE.isoformat()}",
            "_count": 200
        },
        max_pages = 5,
        cache_name = (
            f"observations_bp_i10_adults_"
            f"{COHORT_REFERENCE_DATE.isoformat()}.json"
        )
    )

    print(f"  Conditions: {len(conditions)} fetched, "
        f"{len(eligible_conditions)} eligible, "
        f"{len(adult_conditions)} adult-linked"
    )
    print(f"  Patients: {len(patients)} fetched, "
        f"{len(adult_patients)} adults"
    )
    print(f"  Blood pressure resources: "
        f"{len(blood_pressure_observations)}"
    )

    print("\n ========== 2) Transform and Pseudonymize ==========")

    transformed_patients = [
        transformed
        for patient in adult_patients
        if (
            transformed := transform_patient(patient)
        ) is not None
    ]

    transformed_conditions = [
        transformed
        for condition in adult_conditions
        if (
            transformed := transform_condition(condition)
        ) is not None
    ]

    transformed_observations = [
        row
        for observation in blood_pressure_observations
        for row in transform_observation(observation)
    ]

    transformed_patient_ids = {
        patient["id"]
        for patient in transformed_patients
    }

    assert {
        condition["subject"]
        for condition in transformed_conditions
    } <= transformed_patient_ids

    assert {
        observation["subject"]
        for observation in transformed_observations
    } <= transformed_patient_ids

    assert len({
        (observation["id"],
         observation["component_index"]
        )
        for observation in transformed_observations
    }) == len(transformed_observations)

    print(f"  Transformed: {len(transformed_patients)} patients, "
        f"{len(transformed_conditions)} conditions, "
        f"{len(transformed_observations)} measurements"
    )
    print("Referential integrity: OK")

    print("\n ========== 3) Analyze ==========")

    descriptive_results = descriptive_analysis(
        transformed_patients,
        transformed_observations
    )

    iqr_summaries, iqr_anomalies = iqr_anomaly_analysis(
        transformed_observations
    )

    print(
        f"  Results: {len(descriptive_results)} descriptive groups, "
        f"{len(iqr_summaries)} IQR summaries, "
        f"{len(iqr_anomalies)} anomalies"
    )

    print("\n ========== 4) LOAD ==========")

    with connect() as connection:
        create_schema(connection)

        load_resources(
            connection,
            transformed_patients,
            transformed_conditions,
            transformed_observations
        )

        load_analysis_results(
            connection,
            descriptive_results,
            iqr_summaries,
            iqr_anomalies
        )

    print("  Database load complete")
    print("\nETL completed successfully")