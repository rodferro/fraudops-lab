"""Check experimental semantics that can silently produce misleading results."""
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

import numpy as np

from exp005_data import FEATURES, digest, epoch, feedback, generate
from exp005_analyze import bins, js, metrics, psi, quality, prometheus_histogram


class ExperimentSemanticsTest(unittest.TestCase):
    def test_reproducibility_and_private_contract(self):
        a, b = generate("baseline", 100), generate("baseline", 100)
        self.assertEqual(digest(a), digest(b))
        self.assertNotEqual(digest(a), digest(generate("burst", 100)))
        self.assertEqual(len({r["transaction_id"] for r in a}), 100)
        self.assertEqual(set(a[0]["features"]), set(FEATURES))
        self.assertNotIn("ground_truth", a[0]["features"])

    def test_concept_preserves_features_and_cpu_comparison_is_paired(self):
        base, concept = generate("baseline", 2000, 123), generate("concept", 2000, 123)
        self.assertEqual([r["features"] for r in base], [r["features"] for r in concept])
        self.assertNotEqual([r["ground_truth"] for r in base], [r["ground_truth"] for r in concept])
        normal, limited = generate("cpu_normal", 100), generate("cpu_limited", 100)
        self.assertEqual([r["features"] for r in normal], [r["features"] for r in limited])
        self.assertEqual([r["ground_truth"] for r in normal], [r["ground_truth"] for r in limited])

    def test_feedback_masks_enforce_time_cutoff(self):
        rows = generate("concept", 1000)
        for i, row in enumerate(rows):
            row["decision"] = "review" if i % 2 else "approve"
        cutoffs = feedback(rows, 12, selected=True)
        self.assertTrue(any(r["feedback_label"] is None for r in rows))
        for row in rows:
            for h, cutoff in cutoffs.items():
                expected = row["feedback_arrival_time"] is not None and epoch(row["feedback_arrival_time"]) <= epoch(cutoff)
                self.assertEqual(row["feedback_available"][h], expected)
            available = list(row["feedback_available"].values())
            self.assertEqual(available, sorted(available))
        self.assertTrue(any(r["feedback_label"] is not None and not r["feedback_available"]["1"] for r in rows))

    def test_label_mask_does_not_rerank_review_budget(self):
        scores = np.array([.9, .8, .04, .01])
        labels = np.array([1, 0, 1, 0])
        result = metrics(scores, labels, np.array([False, True, True, True]))
        self.assertEqual(result["labeled_n"], 3)
        self.assertEqual(result["label_coverage"], .75)
        self.assertEqual(result["precision"], 0)
        self.assertEqual(result["recall"], 0)
        self.assertEqual(result["capture"]["0.01"]["selected_labeled_n"], 0)
        self.assertIsNone(result["capture"]["0.01"]["precision_on_known_selected"])
        self.assertEqual(result["capture"]["0.01"]["capture_rate"], 0)
        approved = metrics(scores, labels, np.ones(4, bool), scores < .06729906797409058)
        self.assertEqual(approved["group_n"], 2)
        self.assertIsNone(approved["precision"])
        self.assertEqual(approved["recall"], 0)

    def test_undefined_auc_and_empty_precision(self):
        for labels in [np.zeros(3), np.ones(3)]:
            result = metrics(np.array([.01, .02, .03]), labels, np.ones(3, bool))
            self.assertIsNone(result["roc_auc"])
            self.assertIsNone(result["pr_auc_average_precision"])
            self.assertIsNone(result["precision"])
        result = metrics(np.array([.1]), np.array([1]), np.array([False]))
        self.assertIsNone(result["label_coverage"] if result["group_n"] == 0 else result["prevalence"])

    def test_drift_bins_cover_tails_and_smoothing_is_finite(self):
        a, b = np.array([0., 0., 1., np.nan]), np.array([100., 200., 300., np.nan])
        edges = bins(a)
        self.assertEqual(psi(a, a, edges), 0)
        self.assertTrue(np.isfinite(psi(a, b, edges)))
        self.assertGreater(psi(a, b, edges), 0)
        self.assertEqual(js(a, a, [0, 1]), 0)
        self.assertAlmostEqual(js(a, b, [0, 1]), js(b, a, [0, 1]))
        self.assertTrue(0 <= js(a, b, [0, 1]) <= 1)

    def test_evaluation_cannot_see_future_feedback(self):
        rows = generate("baseline", 4)
        for i, row in enumerate(rows):
            row.update(ok=True, probability=[.9, .8, .04, .01][i], feedback_label=i % 2,
                       feedback_arrival_time="2027-01-01T00:00:00+00:00", feedback_policy="test",
                       feedback_available={h: False for h in ["1", "12", "48", "168"]})
        with patch("exp005_analyze.BOOTSTRAPS", 2):
            result = quality(rows, np.random.default_rng(1))
        self.assertEqual(result["evaluations"]["eventual"]["all"]["labeled_n"], 4)
        for cutoff in ["1h", "12h", "48h", "168h"]:
            m = result["evaluations"][cutoff]["all"]
            self.assertEqual(m["labeled_n"], 0)
            self.assertEqual(m["label_coverage"], 0)
            self.assertIsNone(m["roc_auc"])
            self.assertIsNone(m["pr_auc_average_precision"])
            self.assertIsNone(m["precision"])

    def test_server_histogram_delta_excludes_prior_requests_and_other_routes(self):
        def capture(count, total, lower):
            prefix = "http_server_requests_seconds"
            return (f'{prefix}_count{{uri="/api/v1/predict"}} {count}\n'
                    f'{prefix}_sum{{uri="/api/v1/predict"}} {total}\n'
                    f'{prefix}_bucket{{uri="/api/v1/predict",le="0.001"}} {lower}\n'
                    f'{prefix}_bucket{{uri="/api/v1/predict",le="0.01"}} {count}\n'
                    f'{prefix}_bucket{{uri="/api/v1/predict",le="+Inf"}} {count}\n'
                    f'{prefix}_count{{uri="/actuator/prometheus"}} 100000\n')
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder)/"before", Path(folder)/"after"
            before.write_text(capture(10, .01, 8))
            after.write_text(capture(14, .03, 10))
            result = prometheus_histogram(before, after, "http_server_requests_seconds", True)
        self.assertEqual(result["count"], 4)
        self.assertAlmostEqual(result["mean_ms"], 5)
        self.assertAlmostEqual(result["p50_ms"], 1)
        self.assertAlmostEqual(result["p95_ms"], 9.1)


if __name__ == "__main__":
    unittest.main()
