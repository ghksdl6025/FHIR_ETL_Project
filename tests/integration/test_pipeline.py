import os
import socket
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import requests
import uvicorn

with patch.dict(os.environ, {
    "PSEUDONYM_KEY": "test-only-pseudonym-key-32-characters",
    "COHORT_REFERENCE_DATE": "2026-08-28",
}), patch("dotenv.load_dotenv"):
    import api
    import task1

from analysis import descriptive_analysis, iqr_anomaly_analysis
from database import connect, create_schema, load_analysis_results, load_resources


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database_url = os.environ.get("TEST_DATABASE_URL")
        if not database_url or conninfo_to_dict(database_url).get("dbname") != "fhir_ci_test":
            raise RuntimeError("Set TEST_DATABASE_URL to a disposable fhir_ci_test database")

        admin = psycopg.connect(database_url, autocommit=True, connect_timeout=5)
        cls.addClassCleanup(admin.close)
        schema = f"fhir_test_{uuid4().hex}"
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cls.addClassCleanup(
            admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )
        cls.database_url = make_conninfo(
            database_url, options=f"-csearch_path={schema}", connect_timeout=5
        )
        environment = patch.dict(os.environ, {"DATABASE_URL": cls.database_url})
        environment.start()
        cls.addClassCleanup(environment.stop)

        cls.patients = [task1.transform_patient({
            "id": "synthetic-patient", "birthDate": "1980-01-01", "gender": "female",
        })]
        cls.patient_id = cls.patients[0]["id"]
        cls.conditions = [task1.transform_condition({
            "id": "synthetic-condition", "subject": {"reference": "Patient/synthetic-patient"},
            "code": {"coding": [{"code": "I10", "system": "urn:test"}]},
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
        })]
        cls.observations = []
        for index, systolic in enumerate((0, 100, 100, 100, 100, 100, 100, 200)):
            cls.observations.extend(task1.transform_observation({
                "id": f"synthetic-observation-{index}",
                "subject": {"reference": "Patient/synthetic-patient"},
                "status": "final", "effectiveDateTime": "2026-08-12T10:30:00Z",
                "component": [
                    {"code": {"coding": [{"code": code, "system": "http://loinc.org"}]},
                     "valueQuantity": {"value": value, "unit": "mmHg"}}
                    for code, value in (("8480-6", systolic), ("8462-4", 80))
                ],
            }))
        cls.descriptive = descriptive_analysis(cls.patients, cls.observations)
        cls.summaries, cls.anomalies = iqr_anomaly_analysis(cls.observations)
        cls.load_fixture()

        cls.http = requests.Session()
        cls.http.trust_env = False
        cls.addClassCleanup(cls.http.close)
        listener = socket.socket()
        cls.addClassCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        cls.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(
            api.app, log_level="error", access_log=False, timeout_graceful_shutdown=2
        ))
        cls.thread = threading.Thread(
            target=cls.server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        cls.thread.start()
        cls.addClassCleanup(cls.stop_api)
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Test API did not start within 5 seconds")

    @classmethod
    def load_fixture(cls):
        with connect() as connection:
            create_schema(connection)
            load_resources(connection, cls.patients, cls.conditions, cls.observations)
            load_analysis_results(connection, cls.descriptive, cls.summaries, cls.anomalies)

    @classmethod
    def stop_api(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        if cls.thread.is_alive():
            raise RuntimeError("Test API did not stop within 5 seconds")

    def test_health_queries_database(self):
        response = self.http.get(f"{self.base_url}/health", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_patient_list_detail_and_pagination(self):
        response = self.http.get(f"{self.base_url}/patients", params={"limit": 1}, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "items": self.patients, "count": 1, "limit": 1, "offset": 0,
        })
        response = self.http.get(f"{self.base_url}/patients/{self.patient_id}", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.patients[0])
        response = self.http.get(f"{self.base_url}/patients", params={"offset": 1}, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_observation_filters_preserve_patient_links(self):
        response = self.http.get(f"{self.base_url}/observations", params={
            "code": "8480-6", "patient_id": self.patient_id,
        }, timeout=5)
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 8)
        self.assertEqual(sorted(row["value"] for row in rows),
                         [0, 100, 100, 100, 100, 100, 100, 200])
        for row in rows:
            self.assertEqual(row["subject"], self.patient_id)
            self.assertEqual(row["code"], "8480-6")
            self.assertEqual(row["effective_month"], "2026-08")
        response = self.http.get(f"{self.base_url}/observations", params={
            "patient_id": "0" * 64,
        }, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_api_returns_stored_statistics_and_joined_anomalies(self):
        response = self.http.get(f"{self.base_url}/descriptive", timeout=5)
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 4)
        self.assertEqual({(row["dimension"], row["group_name"]) for row in rows},
                         {("age_band", "40-59"), ("gender", "female")})
        for row in rows:
            self.assertEqual(row["sample_count"], 8)
            self.assertEqual(row["mean"], 100 if row["code"] == "8480-6" else 80)
        response = self.http.get(f"{self.base_url}/anomalies", timeout=5)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(len(result["summary"]), 2)
        self.assertEqual([(row["value"], row["direction"]) for row in result["detail"]],
                         [(0, "low"), (200, "high")])
        for row in result["detail"]:
            self.assertEqual(row["subject"], self.patient_id)
            self.assertEqual(row["measurement"], "systolic")
            self.assertEqual((row["lower_bound"], row["upper_bound"]), (100, 100))

    def test_reloading_same_data_does_not_duplicate_rows(self):
        self.load_fixture()
        with connect() as connection:
            for table, expected in (
                ("patients", 1), ("conditions", 1), ("observations", 16),
                ("descriptive_results", 4), ("iqr_summaries", 2), ("iqr_anomalies", 2),
            ):
                with self.subTest(table=table):
                    count = connection.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                    self.assertEqual(count, expected)

    def test_invalid_api_requests_return_documented_status_codes(self):
        for path, expected in (
            ("/patients/" + "0" * 64, 404), ("/patients/not-a-pseudonym", 422),
            ("/patients?limit=0", 422), ("/patients?offset=-1", 422),
            ("/observations?code=unsupported", 400),
            ("/observations?patient_id=invalid", 422), ("/anomalies?limit=101", 422),
        ):
            with self.subTest(path=path):
                response = self.http.get(self.base_url + path, timeout=5)
                self.assertEqual(response.status_code, expected)

    def test_health_returns_503_when_database_connection_fails(self):
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            database_url = make_conninfo(
                self.database_url, host="127.0.0.1",
                port=unavailable.getsockname()[1], connect_timeout=1,
            )
            with patch.dict(os.environ, {"DATABASE_URL": database_url}):
                response = self.http.get(f"{self.base_url}/health", timeout=5)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Database unavailable"})
        self.assertEqual(self.http.get(f"{self.base_url}/health", timeout=5).status_code, 200)
