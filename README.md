# Small FHIR data pipeline and platform
A small end-to-end pipeline that follows the ETL process. 1. Extracts a cohort from a public FHIR server. 2. pseudonymizes and analyzes the data. 3. stores the results in PostgreSQL. 4. serves them through a FastAPI service.

## Quick start

Requirements for Windows User:
- Docker Desktop
- WSL2 and Hyper V enabled [Installation Guide](https://docs.docker.com/desktop/setup/install/windows-install/)

1) From CLI or Cmd:
```bat
copy .env.example .env
docker compose up --build
```

After startup:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json

2) Expected service state:
- `db`: running and healthy
- `etl`: Loading the database once the container started
- `api`: starts after the ETL completes

3) To stop:
```bat
docker compose down
```

## Tests and CI

With Python 3.13 installed, run from the repository root:

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The unit tests cover age boundaries, pseudonymization and patient references,
blood pressure transformations, descriptive statistics, and IQR edge cases.
They use synthetic data, a fixed reference date, and a test-only pseudonym key.
They do not read `.env`, call the public FHIR server, or connect to PostgreSQL.
No additional test dependencies are required.

The GitHub Actions workflow in `.github/workflows/ci.yml` runs on pull requests
and pushes to `main`. It installs dependencies, runs the unit tests on Python
3.13, then builds the Docker image without starting the ETL or publishing it.
With Docker running, the same build can be checked locally:

```text
docker build --tag fhir-etl:ci .
```

Database/API integration tests, deployment, and branch protection are not yet
configured. To block merging on CI failures, add `Tests and Docker build` as a
required status check in the repository's branch protection or ruleset after
the first workflow run.

## Cohort and resource

The data source is from https://hapi.fhir.org/baseR4

The cohort contains patients who:
- have an active and confirmed __Condition__ containing code `I10`, Hypertension (High blood pressure)
- are at least 18 years old on the reference date, _28.08.2026_
- have related blood pressure observations dated on or before that date

Extracted resources are:
- Patient
- Condition
- Observation

Used blood pressure codes:
- `85354-9`: blood pressure panel
- `8480-6`: systolic blood pressure
- `8462-4`: diastolic blood pressure

Hypertension was selected because blood pressure data supports both descriptive analysis and anomaly detection. The Condition search uses the value `I10` without restricting the coding system.

### Data handling and pseudonymization

In this project, the server responses are cached in a local Docker volume for reruns, while PostgreSQL stores only the fields needed for analysis.

| Resource | Fields stored in PostgreSQL |
|---|---|
| Patient | `id`, `gender`, `age` |
| Condition | `id`, `subject`, `system`, `code`, `clinical_status`, `verification_status` |
| Observation | `id`, `component_index`, `subject`, `status`, `effective_month`, `system`, `code`, `value`, `unit` |

Resource IDs and patient references are pseudonymized with HMAC-SHA256 using `PSEUDONYM_KEY`, which can be found in `.env` file.

## Architecture
<p align="center">
  <img  src="img/architecture.jpg">
</p>

The ETL uses request timeouts, bounded pagination, retries with backoff, and a cache. Database loads use primary keys and `ON CONFLICT`, so reruns are idempotent.

## API

### From Swagger UI

The easiest way to look around the API is with Swagger UI:
1. Open http://localhost:8000/docs
2. Select an API to call
3. Click __Try it out__
4. Enter filter values.
5. Click __Execute__

### Examples

All endpoints use `GET`, so they can also be opened directly in a web browser.

Check whether the API and database are working:

```text
http://localhost:8000/health
```

Fetch the first 10 patients:

```text
http://localhost:8000/patients?limit=10&offset=0
```

Fetch the next 10 patients:

```text
http://localhost:8000/patients?limit=10&offset=10
```

You can control how many patients to retrieve using `limit` and how many patients are skipped to retrieve with `offset`.

Fetch one patient:

```text
http://localhost:8000/patients/{patient_id}
```

:warning: Use the pseudonymized `id` returned by the patient list.

Fetch up to 20 systolic blood pressure measurements:

```text
http://localhost:8000/observations?code=8480-6&limit=20
```

Fetch measurements for one patient:

```text
http://localhost:8000/observations?patient_id={patient_id}&limit=20
```

Fetch the descriptive analysis:

```text
http://localhost:8000/descriptive
```

| Endpoint | Description |
|---|---|
| `GET /health` | Checks API and database availability |
| `GET /patients` | Lists pseudonymized patients |
| `GET /patients/{patient_id}` | Fetches one patient |
| `GET /observations` | Lists blood pressure measurements |
| `GET /descriptive` | Returns descriptive analysis |
| `GET /anomalies` | Returns summary and observations flagged by the IQR analysis |

The API returns `400` for unsupported observation codes, `404` for missing patients, `422` for invalid parameters, and `503` when the database is unavailable.

## Analysis

### Descriptive statistics
You can find the descriptive statistics for systolic and diastolic blood pressure by gender and age band (`18-39`, `40-59`, and `60+`). Results include count, mean, median, standard deviation, minimum, and maximum. The descriptive statistics can be shown using `/descriptive` api.

### Anomaly detection

Anomaly detection is based on the statistical outlier detection. Here interquartile range (IQR) has been used:

```text
IQR = Q3 - Q1
lower bound = Q1 - 1.5 × IQR
upper bound = Q3 + 1.5 × IQR
```

Values outside these bounds are flagged for review. 

The IQR result summary and details with individual observations can be found using `/anomalies` api.

