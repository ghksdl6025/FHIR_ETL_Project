import unittest

from analysis import age_band, descriptive_analysis, iqr_anomaly_analysis


class AnalysisTests(unittest.TestCase):
    def test_age_band_boundaries(self):
        for age, expected in ((18, "18-39"), (39, "18-39"), (40, "40-59"),
                              (59, "40-59"), (60, "60+")):
            with self.subTest(age=age):
                self.assertEqual(age_band(age), expected)

    def test_descriptive_statistics_by_age_gender_and_code(self):
        results = descriptive_analysis(
            [{"id": "patient-1", "age": 40, "gender": "female"}],
            [{"subject": "patient-1", "code": code, "value": value}
             for code, value in (("8480-6", 120), ("8480-6", 140), ("8462-4", 80))],
        )
        self.assertEqual(len(results), 4)
        for result in results:
            with self.subTest(dimension=result["dimension"], code=result["code"]):
                self.assertEqual(result["group"],
                                 "40-59" if result["dimension"] == "age_band" else "female")
                if result["code"] == "8480-6":
                    self.assertEqual(result["measurement"], "systolic")
                    self.assertEqual(result["count"], 2)
                    self.assertEqual(result["mean"], 130)
                    self.assertEqual(result["median"], 130)
                    self.assertEqual(result["std_dev"], 14.14)
                    self.assertEqual((result["minimum"], result["maximum"]), (120, 140))
                else:
                    self.assertEqual(result["measurement"], "diastolic")
                    self.assertEqual(result["count"], 1)
                    self.assertEqual(result["mean"], 80)
                    self.assertIsNone(result["std_dev"])

    def test_descriptive_analysis_ignores_unusable_measurements(self):
        self.assertEqual(descriptive_analysis(
            [{"id": "patient-1", "age": 40, "gender": "female"}],
            [
                {"subject": "missing", "code": "8480-6", "value": 120},
                {"subject": "patient-1", "code": "other", "value": 120},
                {"subject": "patient-1", "code": "8480-6", "value": None},
                {"subject": "patient-1", "code": "8480-6", "value": "120"},
            ],
        ), [])

    def test_empty_analysis(self):
        self.assertEqual(descriptive_analysis([], []), [])
        self.assertEqual(iqr_anomaly_analysis([]), ([], []))

    def test_iqr_requires_four_numeric_measurements(self):
        summaries, anomalies = iqr_anomaly_analysis([
            {"code": "8480-6", "value": value}
            for value in (100, 110, 120, None, "130", True)
        ])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["count"], 3)
        self.assertEqual(summaries[0]["status"], "insufficient_data")
        self.assertIsNone(summaries[0]["q1"])
        self.assertIsNone(summaries[0]["anomaly_count"])
        self.assertEqual(anomalies, [])

    def test_iqr_flags_high_and_low_outliers(self):
        summaries, anomalies = iqr_anomaly_analysis([
            {"id": f"observation-{index}", "component_index": 0,
             "subject": "patient-1", "effective_month": "2026-08",
             "code": "8480-6", "value": value, "unit": "mmHg"}
            for index, value in enumerate((0, 100, 100, 100, 100, 100, 100, 200))
        ])
        self.assertEqual(summaries[0]["status"], "complete")
        self.assertEqual(summaries[0]["count"], 8)
        self.assertEqual((summaries[0]["q1"], summaries[0]["q3"]), (100, 100))
        self.assertEqual(summaries[0]["iqr"], 0)
        self.assertEqual(summaries[0]["anomaly_count"], 2)
        self.assertEqual([(row["id"], row["direction"]) for row in anomalies],
                         [("observation-0", "low"), ("observation-7", "high")])
        for row in anomalies:
            self.assertEqual((row["lower_bound"], row["upper_bound"]), (100, 100))

    def test_iqr_values_on_bounds_are_not_outliers(self):
        summaries, anomalies = iqr_anomaly_analysis([
            {"code": "8480-6", "value": value}
            for value in (70, 100, 100, 110, 110, 120, 120, 150)
        ])
        self.assertEqual((summaries[0]["lower_bound"], summaries[0]["upper_bound"]),
                         (70, 150))
        self.assertEqual(summaries[0]["anomaly_count"], 0)
        self.assertEqual(anomalies, [])

    def test_iqr_odd_sample_and_separate_measurement_codes(self):
        summaries, anomalies = iqr_anomaly_analysis([
            {"code": code, "value": value}
            for code in ("8480-6", "8462-4", "other")
            for value in (100, 110, 120, 130, 140)
        ])
        self.assertEqual({row["code"] for row in summaries}, {"8480-6", "8462-4"})
        for row in summaries:
            self.assertEqual(row["count"], 5)
            self.assertEqual((row["q1"], row["q3"]), (105, 135))
            self.assertEqual((row["lower_bound"], row["upper_bound"]), (60, 180))
        self.assertEqual(anomalies, [])
