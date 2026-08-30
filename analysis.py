from collections import defaultdict
from statistics import mean, median, stdev

blood_pressure_types = {
    "8480-6": "systolic",
    "8462-4": "diastolic"
}

def age_band(age):
    if age < 40:
        return "18-39"

    if age < 60:
        return "40-59"
    return "60+"

def descriptive_analysis(patients, observations):
    patients_by_id = {
        patient["id"]: patient for patient in patients
    }

    grouped_values = defaultdict(list)

    for observation in observations:
        patient = patients_by_id.get(observation["subject"])
        value = observation.get("value")
        code = observation.get("code")

        if (patient is None or not isinstance(value, (int, float)) or code not in blood_pressure_types):
            continue

        dimensions = {
            "age_band": age_band(patient["age"]),
            "gender": patient["gender"]
        }

        for dimension, group in dimensions.items():
            grouped_values[(dimension, group, code)].append(value)

    results = []

    for (dimension, group, code), values in sorted(grouped_values.items()):
        results.append({
            "dimension": dimension,
            "group": group,
            "code": code,
            "measurement": blood_pressure_types[code],
            "count": len(values),
            "mean": round(mean(values), 2),
            "median": round(median(values), 2),
            "std_dev": (round(stdev(values), 2) if len(values) > 1 else None),
            "minimum": min(values),
            "maximum": max(values)
        })

    return results

def iqr_anomaly_analysis(observations):
    grouped_observations = defaultdict(list)

    # 1. Categorize by codes, e.g., diastolic and systolic
    for observation in observations:
        code = observation.get("code")
        value = observation.get("value")

        if (
            code in blood_pressure_types
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            grouped_observations[code].append(observation)

    summaries = []
    anomalies = []

    # 2. Calculate IQR by codes
    for code, rows in sorted(
        grouped_observations.items()
    ):
        values = sorted(
            row["value"]
            for row in rows
        )

        result_summary = {
            "code": code,
            "measurement": blood_pressure_types[code],
            "method": "IQR",
            "count": len(values),
            "q1": None,
            "q3": None,
            "iqr": None,
            "lower_bound": None,
            "upper_bound": None,
            "anomaly_count": None,
            "status": "insufficient_data"
        }

        if len(values) < 4:
            summaries.append(result_summary)
            continue

        midpoint = len(values) // 2

        q1 = median(values[:midpoint])
        q3 = median(values[-midpoint:])
        iqr = q3 - q1

        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        flagged_rows = [
            row
            for row in rows
            if (
                row["value"] < lower_bound
                or row["value"] > upper_bound
            )
        ]

        result_summary.update({
            "q1": round(q1, 2),
            "q3": round(q3, 2),
            "iqr": round(iqr, 2),
            "lower_bound": round(lower_bound, 2),
            "upper_bound": round(upper_bound, 2),
            "anomaly_count": len(flagged_rows),
            "status": "complete"
        })

        summaries.append(result_summary)

        for row in flagged_rows:
            anomalies.append({
                "id": row["id"],
                "component_index": row["component_index"],
                "subject": row["subject"],
                "effective_month": row["effective_month"],
                "code": code,
                "measurement": blood_pressure_types[code],
                "value": row["value"],
                "lower_bound": round(lower_bound, 2),
                "upper_bound": round(upper_bound, 2),
                "direction": (
                    "low"
                    if row["value"] < lower_bound
                    else "high"
                )
            })

    return summaries, anomalies