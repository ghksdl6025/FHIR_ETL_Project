import os
import unittest
from datetime import date
from unittest.mock import patch

with patch.dict(os.environ, {
    "PSEUDONYM_KEY": "test-only-pseudonym-key-32-characters",
    "COHORT_REFERENCE_DATE": "2026-08-28",
}), patch("dotenv.load_dotenv"):
    import task1


class TransformTests(unittest.TestCase):
    def test_age_at_eighteenth_birthday(self):
        reference = date(2026, 8, 28)
        self.assertEqual(task1.age_on("2008-08-28", reference), 18)
        self.assertEqual(task1.age_on("2008-08-29", reference), 18)

    def test_invalid_or_future_birth_date(self):
        for birth_date in (None, "", "invalid", "2000-02-30", "2027-01-01"):
            with self.subTest(birth_date=birth_date):
                self.assertIsNone(task1.age_on(birth_date, date(2026, 8, 28)))

    def test_patient_keeps_only_analysis_fields(self):
        patient = task1.transform_patient({
            "id": "patient-1", "birthDate": "2008-08-28",
            "name": [{"text": "Synthetic Patient"}],
        })
        self.assertEqual(patient, {
            "id": task1.pseudonymize_id("Patient", "patient-1"),
            "gender": "unknown", "age": 18,
        })

    def test_ineligible_patient_is_skipped(self):
        for patient in (
            {"id": "child", "birthDate": "2008-08-29"},
            {"birthDate": "2000-01-01"},
            {"id": "missing-birth-date"},
        ):
            with self.subTest(patient=patient):
                self.assertIsNone(task1.transform_patient(patient))

    def test_pseudonym_is_stable_and_scoped_to_resource_and_key(self):
        pseudonym = task1.pseudonymize_id("Patient", "patient-1")
        self.assertRegex(pseudonym, r"^[0-9a-f]{64}$")
        self.assertEqual(pseudonym, task1.pseudonymize_id("Patient", "patient-1"))
        self.assertNotEqual(pseudonym, task1.pseudonymize_id("Condition", "patient-1"))
        with patch.object(task1, "PSEUDONYM_KEY", b"another-test-only-key-32-characters"):
            self.assertNotEqual(pseudonym, task1.pseudonymize_id("Patient", "patient-1"))

    def test_missing_resource_identity_has_no_pseudonym(self):
        self.assertIsNone(task1.pseudonymize_id("Patient", None))
        self.assertIsNone(task1.pseudonymize_id(None, "patient-1"))

    def test_condition_links_to_same_pseudonymized_patient(self):
        condition = task1.transform_condition({
            "id": "condition-1",
            "subject": {"reference": "https://example.test/Patient/patient-1/"},
            "code": {"coding": [{"code": "other"}, {"system": "urn:test", "code": "I10"}]},
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
        })
        self.assertEqual(condition, {
            "id": task1.pseudonymize_id("Condition", "condition-1"),
            "subject": task1.pseudonymize_id("Patient", "patient-1"),
            "system": "urn:test", "code": "I10",
            "clinicalStatus": "active", "verificationStatus": "confirmed",
        })

    def test_non_target_condition_is_skipped(self):
        self.assertIsNone(task1.transform_condition({
            "id": "condition-1", "subject": {"reference": "Patient/patient-1"},
            "code": {"coding": [{"code": "other"}]},
        }))

    def test_blood_pressure_panel_becomes_two_linked_measurements(self):
        rows = task1.transform_observation({
            "id": "observation-1", "subject": {"reference": "Patient/patient-1"},
            "status": "final", "effectiveDateTime": "2026-08-12T10:30:00Z",
            "component": [
                {"code": {"coding": [{"code": code, "system": "http://loinc.org"}]},
                 "valueQuantity": {"value": value, "unit": "mmHg"}}
                for code, value in (("8480-6", 120), ("8462-4", 80))
            ],
        })
        self.assertEqual(rows, [
            {
                "id": task1.pseudonymize_id("Observation", "observation-1"),
                "component_index": index,
                "subject": task1.pseudonymize_id("Patient", "patient-1"),
                "status": "final", "effective_month": "2026-08",
                "system": "http://loinc.org", "code": code,
                "value": value, "unit": "mmHg",
            }
            for index, (code, value) in enumerate((("8480-6", 120), ("8462-4", 80)))
        ])

    def test_standalone_observation_with_missing_quantity(self):
        rows = task1.transform_observation({
            "id": "observation-1", "subject": {"reference": "Patient/patient-1"},
            "code": {"coding": [{"code": "8480-6"}]},
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_index"], 0)
        self.assertIsNone(rows[0]["value"])
        self.assertIsNone(rows[0]["effective_month"])

    def test_irrelevant_or_unlinked_observation_is_skipped(self):
        for observation in (
            {},
            {"id": "observation-1", "code": {"coding": [{"code": "8480-6"}]}},
            {"id": "observation-1", "subject": {"reference": "Patient/patient-1"},
             "code": {"coding": [{"code": "other"}]}},
        ):
            with self.subTest(observation=observation):
                self.assertEqual(task1.transform_observation(observation), [])

    def test_invalid_observation_date_has_no_month(self):
        for value in (None, "", "invalid", "2026-02-30"):
            with self.subTest(value=value):
                self.assertIsNone(task1.generalize_month(value))
