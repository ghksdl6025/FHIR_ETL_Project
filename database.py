import os
import psycopg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    gender TEXT NOT NULL,
    age INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS conditions (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL REFERENCES patients(id),
    system TEXT,
    code TEXT NOT NULL,
    clinical_status TEXT NOT NULL,
    verification_status TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT NOT NULL,
    component_index INTEGER NOT NULL,
    subject TEXT NOT NULL REFERENCES patients(id),
    status TEXT,
    effective_month VARCHAR(7),
    system TEXT,
    code TEXT NOT NULL,
    value DOUBLE PRECISION,
    unit TEXT,
    PRIMARY KEY (id, component_index));

CREATE TABLE IF NOT EXISTS descriptive_results (
    dimension TEXT NOT NULL,
    group_name TEXT NOT NULL,
    code TEXT NOT NULL,
    measurement TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    mean DOUBLE PRECISION,
    median DOUBLE PRECISION,
    std_dev DOUBLE PRECISION,
    minimum DOUBLE PRECISION,
    maximum DOUBLE PRECISION,
    PRIMARY KEY (dimension, group_name, code)
);

CREATE TABLE IF NOT EXISTS iqr_summaries (
    code TEXT NOT NULL,
    measurement TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    q1 DOUBLE PRECISION,
    q3 DOUBLE PRECISION,
    iqr DOUBLE PRECISION,
    lower_bound DOUBLE PRECISION,
    upper_bound DOUBLE PRECISION,
    anomaly_count INTEGER,
    status TEXT NOT NULL,
    PRIMARY KEY (code)
);

CREATE TABLE IF NOT EXISTS iqr_anomalies (
    observation_id TEXT NOT NULL,
    component_index INTEGER NOT NULL,
    lower_bound DOUBLE PRECISION NOT NULL,
    upper_bound DOUBLE PRECISION NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('low', 'high')),
    PRIMARY KEY (observation_id, component_index),
    FOREIGN KEY (observation_id, component_index)
        REFERENCES observations(id, component_index)
);
"""

def connect():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg.connect(database_url)


def create_schema(connection):
    connection.execute(SCHEMA_SQL)

def load_resources(connection, patients, conditions, observations):
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO patients (id, gender, age)
            VALUES (%(id)s, %(gender)s, %(age)s)
            ON CONFLICT (id) DO UPDATE SET
                gender = EXCLUDED.gender,
                age = EXCLUDED.age
            """,
            patients
        )

        cursor.executemany(
            """
            INSERT INTO conditions (
                id,
                subject,
                system,
                code,
                clinical_status,
                verification_status
            )
            VALUES (
                %(id)s,
                %(subject)s,
                %(system)s,
                %(code)s,
                %(clinicalStatus)s,
                %(verificationStatus)s
            )
            ON CONFLICT (id) DO UPDATE SET
                subject = EXCLUDED.subject,
                system = EXCLUDED.system,
                code = EXCLUDED.code,
                clinical_status = EXCLUDED.clinical_status,
                verification_status = EXCLUDED.verification_status
            """,
            conditions
        )

        cursor.executemany(
            """
            INSERT INTO observations (
                id,
                component_index,
                subject,
                status,
                effective_month,
                system,
                code,
                value,
                unit
            )
            VALUES (
                %(id)s,
                %(component_index)s,
                %(subject)s,
                %(status)s,
                %(effective_month)s,
                %(system)s,
                %(code)s,
                %(value)s,
                %(unit)s
            )
            ON CONFLICT (id, component_index) DO UPDATE SET
                subject = EXCLUDED.subject,
                status = EXCLUDED.status,
                effective_month = EXCLUDED.effective_month,
                system = EXCLUDED.system,
                code = EXCLUDED.code,
                value = EXCLUDED.value,
                unit = EXCLUDED.unit
            """,
            observations
        )

def load_analysis_results(
    connection,
    descriptive_results,
    iqr_summaries,
    iqr_anomalies
):
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO descriptive_results (
                dimension,
                group_name,
                code,
                measurement,
                sample_count,
                mean,
                median,
                std_dev,
                minimum,
                maximum
            )
            VALUES (
                %(dimension)s,
                %(group)s,
                %(code)s,
                %(measurement)s,
                %(count)s,
                %(mean)s,
                %(median)s,
                %(std_dev)s,
                %(minimum)s,
                %(maximum)s
            )
            ON CONFLICT (dimension, group_name, code) DO UPDATE SET
                measurement = EXCLUDED.measurement,
                sample_count = EXCLUDED.sample_count,
                mean = EXCLUDED.mean,
                median = EXCLUDED.median,
                std_dev = EXCLUDED.std_dev,
                minimum = EXCLUDED.minimum,
                maximum = EXCLUDED.maximum
            """,
            descriptive_results
        )

        cursor.executemany(
            """
            INSERT INTO iqr_summaries (
                code,
                measurement,
                sample_count,
                q1,
                q3,
                iqr,
                lower_bound,
                upper_bound,
                anomaly_count,
                status
            )
            VALUES (
                %(code)s,
                %(measurement)s,
                %(count)s,
                %(q1)s,
                %(q3)s,
                %(iqr)s,
                %(lower_bound)s,
                %(upper_bound)s,
                %(anomaly_count)s,
                %(status)s
            )
            ON CONFLICT (code) DO UPDATE SET
                measurement = EXCLUDED.measurement,
                sample_count = EXCLUDED.sample_count,
                q1 = EXCLUDED.q1,
                q3 = EXCLUDED.q3,
                iqr = EXCLUDED.iqr,
                lower_bound = EXCLUDED.lower_bound,
                upper_bound = EXCLUDED.upper_bound,
                anomaly_count = EXCLUDED.anomaly_count,
                status = EXCLUDED.status
            """,
            iqr_summaries
        )

        cursor.executemany(
            """
            INSERT INTO iqr_anomalies (
                observation_id,
                component_index,
                lower_bound,
                upper_bound,
                direction
            )
            VALUES (
                %(id)s,
                %(component_index)s,
                %(lower_bound)s,
                %(upper_bound)s,
                %(direction)s
            )
            ON CONFLICT (
                observation_id,
                component_index
            ) DO UPDATE SET
                lower_bound = EXCLUDED.lower_bound,
                upper_bound = EXCLUDED.upper_bound,
                direction = EXCLUDED.direction
            """,
            iqr_anomalies
        )