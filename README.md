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
and pushes to `main`. It installs dependencies, runs unit and database/API
integration tests on Python 3.13, then builds the Docker image without running
the public FHIR extraction. A temporary PostgreSQL 18 service is created for
each test job. The existing `Tests and Docker build` required check covers
these steps. Pull requests do not publish images; successful `main` pushes
also publish the built image to GHCR as described below.
With Docker running, the same build can be checked locally:

```text
docker build --tag fhir-etl:ci .
```

### Database and API integration tests

The integration tests transform synthetic FHIR resources, compute analysis
results, store them in PostgreSQL, and query a real local HTTP API. They check
patient/observation filtering, statistics and anomaly joins, repeated loads
without duplicate rows, input validation, and `/health` returning `503` when
its database connection fails. They use the existing dependencies only.

Start a disposable database, separate from the application Compose services:

```text
docker run --detach --rm --name fhir-ci-postgres --publish 127.0.0.1:55432:5432 --env POSTGRES_DB=fhir_ci_test --env POSTGRES_USER=postgres --env POSTGRES_PASSWORD=ci-test-only postgres:18-alpine
docker exec fhir-ci-postgres pg_isready -U postgres -d fhir_ci_test
```

Wait until `pg_isready` reports `accepting connections`, then in PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:ci-test-only@127.0.0.1:55432/fhir_ci_test"
python -m unittest discover -s tests/integration -v
```

The database name must be `fhir_ci_test`; missing configuration fails the test
run rather than skipping it. Tests ignore `.env`, use a unique schema that is
removed afterward, and bind the test API to a free loopback port. The unit-test
command above runs only unit tests; integration tests require the separate
command. Do not add `__init__.py` to `tests/integration`, which would include
these database-dependent tests in unit-test discovery.

Stop and remove the disposable database when finished:

```text
docker stop fhir-ci-postgres
```

No application database volumes are mounted by these commands. Deployment is
not configured. Branch protection is managed in GitHub repository settings.

### Publishing images to GHCR

On a push to `main`, successful tests and the Docker build are followed by a
separate `Publish image to GHCR` job. It loads the image from that same run's
artifact rather than rebuilding it, authenticates with `GITHUB_TOKEN`, and
pushes a tag containing the full commit SHA:

```text
ghcr.io/ghksdl6025/fhir_etl_project:<full-commit-sha>
```

Only the publish job has `packages: write` permission. Pull requests skip the
image artifact upload and publish job. No personal access token or new secret
is needed for this workflow. Source and revision labels link the image to its
GitHub repository and commit. The temporary image artifact is retained for one
day; this does not delete the separately published GHCR image.

After merging this workflow, check both jobs on the `main` run. The publish
job's summary shows the complete image reference; the package should appear
under the repository's **Packages** section. A new GHCR package is private by
default, so the first publish does not make it publicly downloadable. If a
package with this name already exists and publishing is denied, grant this
repository write access under the package's **Manage Actions access** settings.
See [GitHub's Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

To retrieve a version later (authenticate first if the package is private):

```text
docker pull ghcr.io/ghksdl6025/fhir_etl_project:<full-commit-sha>
```

Use the SHA tag for version selection; this workflow does not create a mutable
`latest` tag. SHA tags identify the source commit, but rerunning the same commit
can replace that tag because base images and transitive dependencies may change.
Use the image digest printed by `docker push` when an exact image is required.
Publishing stores the image only; server deployment and rollback are not yet
configured. The image's default command runs ETL, so API deployment also needs
the API command and database configuration shown in `docker-compose.yml`.

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

