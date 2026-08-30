import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from psycopg.rows import dict_row
from database import connect

load_dotenv()

app = FastAPI(title="FHIR Research API")

@app.get("/health")
def health():
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        ) from exc

    return {"status": "ok"}

@app.get("/patients")
def get_patients(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0)
):
    with connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, gender, age
                FROM patients
                ORDER BY id
                LIMIT %s OFFSET %s
                """,
                (limit, offset)
            )
            patients = cursor.fetchall()

    return {
        "items": patients,
        "count": len(patients),
        "limit": limit,
        "offset": offset
    }

@app.get("/patients/{patient_id}")
def get_patient(
    patient_id: str = Path(pattern=r"^[0-9a-f]{64}$")
):
    with connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, gender, age
                FROM patients
                WHERE id = %s
                """,
                (patient_id,)
            )
            patient = cursor.fetchone()

    if patient is None:
        raise HTTPException(
            status_code=404,
            detail="Patient not found"
        )

    return patient

@app.get("/observations")
def get_observations(
    code: str | None = Query(
        default=None,
    ),
    patient_id: str | None = Query(
        default=None,
        pattern=r"^[0-9a-f]{64}$"
    ),
    limit: int = Query(default=50, ge=1, le=100)
):
    if code is not None and code not in {"8480-6", "8462-4"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported observation code"
        )

    with connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    component_index,
                    subject,
                    status,
                    effective_month,
                    system,
                    code,
                    value,
                    unit
                FROM observations
                WHERE code = COALESCE(%s, code)
                  AND subject = COALESCE(%s, subject)
                ORDER BY id, component_index
                LIMIT %s
                """,
                (code, patient_id, limit)
            )
            return cursor.fetchall()


@app.get("/descriptive")
def get_descriptive():
    with connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM descriptive_results
                ORDER BY dimension, group_name, code
                """
            )

            return cursor.fetchall()

@app.get("/anomalies")
def get_anomalies(
    limit: int = Query(default=50, ge=1, le=100)
):
    with connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:

            cursor.execute(
                """
                SELECT *
                FROM iqr_summaries
                ORDER BY code
                """
            )
            iqr_summary = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    a.observation_id AS id,
                    a.component_index,
                    o.subject,
                    o.effective_month,
                    o.code,
                    s.measurement,
                    o.value,
                    o.unit,
                    a.lower_bound,
                    a.upper_bound,
                    a.direction
                FROM iqr_anomalies AS a
                JOIN observations AS o
                  ON o.id = a.observation_id
                 AND o.component_index = a.component_index
                JOIN iqr_summaries AS s
                  ON s.code = o.code
                ORDER BY o.code, o.value
                LIMIT %s
                """,
                (limit,)
            )
            iqr_detail = cursor.fetchall()
        
    return {
        "summary": iqr_summary,
        "detail": iqr_detail
    }