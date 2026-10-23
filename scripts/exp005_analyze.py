#!/usr/bin/env python3
"""Offline aggregation: no model training, network access, or transaction export."""
import argparse
import json
import math
from pathlib import Path
import re

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from exp005_data import CORE, CATEGORIES, CUTOFF_HOURS, FEATURES, NUMERIC, THRESHOLD, epoch, digest, generate

BOOTSTRAPS = 200
BOOTSTRAP_SEED = 5100


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def interval(values):
    finite = [v for v in values if v is not None and math.isfinite(v)]
    return {"low": float(np.percentile(finite, 2.5)) if finite else None,
            "high": float(np.percentile(finite, 97.5)) if finite else None,
            "valid_resamples": len(finite), "resamples": len(values)}


def ratio(a, b):
    return float(a/b) if b else None


def metrics(scores, labels, available, group=None):
    """Top-budget policy is fixed on the entire scored cohort, before label masking.

    Label availability restricts the evaluation denominator, not the selection policy.
    Groups are conditional diagnostics, never estimates of population performance.
    """
    group = np.ones(len(scores), bool) if group is None else group
    known = available & group
    y, s = labels[known], scores[known]
    pred = scores >= THRESHOLD
    tp = int(np.sum(known & pred & (labels == 1)))
    review_known = int(np.sum(known & pred))
    positives = int(y.sum())
    both = len(np.unique(y)) == 2
    result = {"scored_cohort_n": len(scores), "group_n": int(group.sum()), "labeled_n": len(y),
              "positive_labels": positives, "label_coverage": ratio(len(y), group.sum()),
              "prevalence": ratio(positives, len(y)),
              "roc_auc": float(roc_auc_score(y, s)) if both else None,
              "pr_auc_average_precision": float(average_precision_score(y, s)) if both else None,
              "auc_undefined_reason": None if both else "fewer than two label classes",
              "review_count": int(np.sum(pred & group)), "review_rate": ratio(np.sum(pred & group), group.sum()),
              "reviewed_labeled_n": review_known, "true_positives": tp,
              "precision": ratio(tp, review_known), "recall": ratio(tp, positives), "capture": {}}
    ranked = np.argsort(-scores, kind="stable")
    for budget in [.01, .02, .05]:
        selected = np.zeros(len(scores), bool)
        selected[ranked[:math.ceil(len(scores)*budget)]] = True
        selected_known = selected & known
        captured = int(np.sum(selected_known & (labels == 1)))
        result["capture"][str(budget)] = {"cohort_budget_n": int(selected.sum()),
            "group_selected_n": int(np.sum(selected & group)), "selected_labeled_n": int(selected_known.sum()),
            "captured_positive_labels": captured, "capture_rate": ratio(captured, positives),
            "precision_on_known_selected": ratio(captured, selected_known.sum())}
    return result


def quality(rows, rng):
    scored = [r for r in rows if r["ok"]]
    scores = np.array([r["probability"] for r in scored])
    truth = np.array([r["ground_truth"] for r in scored])
    observed = np.array([r["feedback_label"] if r["feedback_label"] is not None else 0 for r in scored])
    eventual = np.array([r["feedback_label"] is not None for r in scored])
    result = {"generated_n": len(rows), "scored_n": len(scored), "api_failures_excluded": len(rows)-len(scored),
              "true_prevalence_all_generated": sum(r["ground_truth"] for r in rows)/len(rows),
              "feedback_policy": rows[0]["feedback_policy"], "evaluations": {}}
    evaluations = {"oracle": (truth, np.ones(len(scored), bool)), "eventual": (observed, eventual)}
    for cutoff in CUTOFF_HOURS:
        # Recompute from times, and assert simulator's persisted mask agrees.
        end = max(epoch(r["event_time"]) for r in rows)
        mask = np.array([r["feedback_arrival_time"] is not None and
                         epoch(r["feedback_arrival_time"]) <= end+cutoff*3600 for r in scored])
        assert np.array_equal(mask, [r["feedback_available"][str(cutoff)] for r in scored])
        evaluations[f"{cutoff}h"] = (observed, mask)
    boot_indices = [rng.integers(0, len(scored), len(scored)) for _ in range(BOOTSTRAPS)]
    for name, (labels, available) in evaluations.items():
        all_metrics = metrics(scores, labels, available)
        sampled = [metrics(scores[idx], labels[idx], available[idx]) for idx in boot_indices]
        fields = ["roc_auc", "pr_auc_average_precision", "precision", "recall", "label_coverage", "prevalence"]
        all_metrics["ci95"] = {f: interval([m[f] for m in sampled]) for f in fields}
        all_metrics["capture_ci95"] = {b: interval([m["capture"][b]["capture_rate"] for m in sampled])
                                       for b in ["0.01", "0.02", "0.05"]}
        result["evaluations"][name] = {"all": all_metrics,
            "reviewed": metrics(scores, labels, available, scores >= THRESHOLD),
            "approved": metrics(scores, labels, available, scores < THRESHOLD)}
    audit = {}
    for key, mask in {"fraud": truth == 1, "nonfraud": truth == 0,
                      "reviewed": scores >= THRESHOLD, "approved": scores < THRESHOLD}.items():
        audit[key] = {"n": int(mask.sum()), "eventual_coverage": ratio(np.sum(mask & eventual), mask.sum())}
    audit["observed_false_positives"] = int(np.sum(eventual & (truth == 0) & (observed == 1)))
    audit["observed_false_negatives"] = int(np.sum(eventual & (truth == 1) & (observed == 0)))
    audit["false_positive_rate"] = ratio(audit["observed_false_positives"], np.sum(eventual & (truth == 0)))
    audit["false_negative_rate"] = ratio(audit["observed_false_negatives"], np.sum(eventual & (truth == 1)))
    result["oracle_feedback_audit"] = audit
    return result


def bins(reference):
    finite = reference[np.isfinite(reference)]
    internal = np.unique(np.quantile(finite, np.arange(.1, 1, .1)))
    return np.r_[-np.inf, internal, np.inf]


def probabilities(counts):
    return (counts+.5)/(sum(counts)+.5*len(counts))


def psi(a, b, edges):
    p = probabilities(np.histogram(a[np.isfinite(a)], edges)[0])
    q = probabilities(np.histogram(b[np.isfinite(b)], edges)[0])
    return float(np.sum((q-p)*np.log(q/p)))


def js(a, b, categories):
    def counts(x):
        # Include a missing bin and a reserved unknown bin; no categories learned from scenarios.
        return np.array([np.sum(x == c) for c in categories] +
                        [np.isnan(x).sum(), np.sum(np.isfinite(x) & ~np.isin(x, categories))])
    p, q = probabilities(counts(a)), probabilities(counts(b))
    middle = (p+q)/2
    return float(.5*np.sum(p*np.log2(p/middle)) + .5*np.sum(q*np.log2(q/middle)))


def matrix(rows, f):
    return np.array([r["features"][f] if r["features"][f] is not None else np.nan for r in rows])


def drift(base, rows, rng, self_comparison=False):
    result = {"baseline_n": len(base), "scenario_n": len(rows), "numeric_psi": {},
              "categorical_js_bits": {}, "missing": {}}
    ia = [rng.integers(0, len(base), len(base)) for _ in range(BOOTSTRAPS)]
    ib = [rng.integers(0, len(rows), len(rows)) for _ in range(BOOTSTRAPS)]
    for f in FEATURES:
        a, b = matrix(base, f), matrix(rows, f)
        difference = float(np.isnan(b).mean()-np.isnan(a).mean())
        result["missing"][f] = {"baseline_rate": float(np.isnan(a).mean()), "scenario_rate": float(np.isnan(b).mean()),
            "delta": difference, "delta_ci95": None if self_comparison else interval(
                [float(np.isnan(b[j]).mean()-np.isnan(a[i]).mean()) for i, j in zip(ia, ib)])}
        if f in NUMERIC:
            edges = bins(a)
            result["numeric_psi"][f] = {"value": psi(a, b, edges),
                "baseline_edges": [float(v) if np.isfinite(v) else str(v) for v in edges],
                "ci95": None if self_comparison else interval([psi(a[i], b[j], edges) for i, j in zip(ia, ib)])}
        else:
            result["categorical_js_bits"][f] = {"value": js(a, b, CATEGORIES[f]),
                "ci95": None if self_comparison else interval([js(a[i], b[j], CATEGORIES[f]) for i, j in zip(ia, ib)])}
    a = np.array([r["probability"] for r in base if r["ok"]])
    b = np.array([r["probability"] for r in rows if r["ok"]])
    edges = bins(a)
    result["scores"] = {"baseline_scored_n": len(a), "scored_n": len(b), "psi": psi(a, b, edges),
        "baseline_edges": [float(v) if np.isfinite(v) else str(v) for v in edges],
        "mean": float(b.mean()), "quantiles": {str(p): float(np.percentile(b, p)) for p in [5, 50, 95, 99]},
        "review_count": int(np.sum(b >= THRESHOLD)), "review_rate": float(np.mean(b >= THRESHOLD)),
        "approval_rate": float(np.mean(b < THRESHOLD))}
    score_samples = [b[rng.integers(0, len(b), len(b))] for _ in range(BOOTSTRAPS)]
    result["scores"]["review_rate_ci95"] = interval([float(np.mean(s >= THRESHOLD)) for s in score_samples])
    result["scores"]["mean_ci95"] = interval([float(s.mean()) for s in score_samples])
    if not self_comparison:
        result["scores"]["psi_ci95"] = interval([psi(a[rng.integers(0, len(a), len(a))], s, edges) for s in score_samples])
    return result


def resource_deltas(phase, before="before", after="after"):
    initial = {p["uid"]: p for p in phase[before]}
    rows = []
    for end in phase[after]:
        row = {"pod": end["pod"], "memory_current_bytes": end.get("memory_current"),
               "memory_peak_lifetime_bytes": end.get("memory_peak"), "ready": end["ready"],
               "restart_counts": [s.get("restartCount") for s in end["statuses"]],
               "termination_reasons": [s.get("lastState", {}).get("terminated", {}).get("reason") for s in end["statuses"]]}
        start = initial.get(end["uid"])
        if start and "cpu" in start and "cpu" in end:
            delta = {k: end["cpu"][k]-start["cpu"][k] for k in end["cpu"] if k in start["cpu"]}
            row["cpu_delta"] = delta
            row["throttled_period_fraction"] = ratio(delta.get("nr_throttled", 0), delta.get("nr_periods", 0))
            row["cpu_core_equivalent"] = delta.get("usage_usec", 0)/1e6/phase["duration_seconds"]
        else:
            row["cpu_delta"] = None
            row["reason"] = "new UID or unavailable cgroup; no cross-pod subtraction"
        rows.append(row)
    return rows


def prometheus_histogram(before, after, metric, http=False):
    def read(path):
        values = {}
        for line in path.read_text().splitlines():
            if not line.startswith(metric + "_") or (http and 'uri="/api/v1/predict"' not in line):
                continue
            key, value = line.rsplit(" ", 1)
            values[key] = float(value)
        return values
    a, b = read(before), read(after)
    count, total, buckets = 0, 0, {}
    for key, value in b.items():
        delta = value-a.get(key, 0)
        if key.startswith(metric + "_count"):
            count += delta
        elif key.startswith(metric + "_sum"):
            total += delta
        elif key.startswith(metric + "_bucket"):
            le = float(re.search(r'le="([^"]+)"', key)[1])
            buckets[le] = buckets.get(le, 0)+delta
    def quantile(q):
        previous_bound, previous_count = 0, 0
        for bound, cumulative in sorted(buckets.items()):
            if cumulative >= q*count and cumulative > previous_count:
                if not math.isfinite(bound):
                    return previous_bound*1000
                return (previous_bound+(bound-previous_bound)*(q*count-previous_count)/(cumulative-previous_count))*1000
            previous_bound, previous_count = bound, cumulative
        return None
    return {"count": count, "mean_ms": ratio(total*1000, count),
            "p50_ms": quantile(.5) if count else None, "p95_ms": quantile(.95) if count else None,
            "p99_ms": quantile(.99) if count else None,
            "method": "same-pod histogram counter differences; interpolated quantiles"}


def plot(out, ops, drift_results, qualities):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9})
    fig, axes = plt.subplots(3, 3, figsize=(16, 12), constrained_layout=True)
    phases = list(ops)
    short = [p.replace("covariate", "covar.").replace("cpu_", "CPU ") for p in phases]
    x = np.arange(len(phases))
    axes[0, 0].bar(x, [ops[p]["throughput_rps"] for p in phases])
    axes[0, 0].set(title="Successful throughput", ylabel="requests / second", xticks=x, xticklabels=short)
    for label, values in [("client", [ops[p]["client_latency_ms"]["p95"] for p in phases]),
                          ("Spring HTTP", [ops[p]["server"]["http"]["p95_ms"] for p in phases]),
                          ("ONNX wrapper", [ops[p]["server"]["onnx"]["p95_ms"] for p in phases])]:
        axes[0, 1].plot(x, values, "o-", label=label)
    axes[0, 1].set(title="Latency p95 (histogram estimates for server)", ylabel="milliseconds (log)",
                   yscale="log", xticks=x, xticklabels=short)
    axes[0, 1].legend()
    axes[0, 2].bar(x, [max([r.get("throttled_period_fraction") or 0 for r in ops[p]["resource_deltas"]], default=0)*100 for p in phases])
    axes[0, 2].set(title="API CPU throttling (maximum across pods)", ylabel="% periods throttled", xticks=x, xticklabels=short)
    data = np.array([[drift_results[s]["numeric_psi"][f]["value"] for s in CORE] for f in NUMERIC])
    im = axes[1, 0].imshow(data, aspect="auto", cmap="YlOrRd")
    axes[1, 0].set(title="Numeric drift: PSI vs baseline", xticks=range(4), xticklabels=CORE,
                   yticks=range(5), yticklabels=NUMERIC)
    fig.colorbar(im, ax=axes[1, 0], shrink=.7)
    for i in range(5):
        for j in range(4):
            axes[1, 0].text(j, i, f"{data[i,j]:.3f}", ha="center", va="center")
    for f in CATEGORIES:
        axes[1, 1].plot(CORE, [drift_results[s]["categorical_js_bits"][f]["value"] for s in CORE], "o-", label=f)
    axes[1, 1].set(title="Categorical drift", ylabel="JS divergence (bits)")
    axes[1, 1].legend(fontsize=7)
    axes[1, 2].bar(CORE, [100*drift_results[s]["scores"]["review_rate"] for s in CORE])
    axes[1, 2].set(title="Fixed-threshold review volume", ylabel="% scored transactions")
    for axis, metric, title in [(axes[2, 0], "roc_auc", "Quality: ROC-AUC"),
                                 (axes[2, 1], "pr_auc_average_precision", "Quality: PR-AUC (average precision)")]:
        for mode in ["oracle", "eventual", "12h"]:
            vals = [qualities[s]["evaluations"][mode]["all"][metric] for s in CORE]
            axis.plot(CORE, [np.nan if v is None else v for v in vals], "o-", label=mode)
        axis.set(title=title, ylabel="score")
        axis.legend()
    for s in CORE:
        axes[2, 2].plot(CUTOFF_HOURS, [100*qualities[s]["evaluations"][f"{h}h"]["all"]["label_coverage"] for h in CUTOFF_HOURS], "o-", label=s)
    axes[2, 2].set(title="Delayed label availability", xlabel="hours after last simulated event (log)",
                   ylabel="% scored cohort labeled", xscale="log")
    axes[2, 2].legend()
    for axis in axes[0]:
        axis.tick_params(axis="x", labelrotation=45)
    fig.suptitle("EXP-005 — local synthetic traffic, operational failures, and delayed model health", fontsize=15)
    fig.savefig(out / "overview.png", dpi=140)
    plt.close(fig)


def analyze(source, out):
    out.mkdir(parents=True, exist_ok=False)
    summary = json.loads((source / "summary.json").read_text())
    assert summary["complete"], "Do not publish an incomplete run as acceptance evidence"
    assert "cleanup_error" not in summary, "Resolve and document cleanup failures before publishing"
    audit, identifiers = {}, set()
    for name, phase in summary["scenarios"].items():
        cohort = [json.loads(line) for line in (source / f"{name}-transactions.jsonl").read_text().splitlines()]
        actual, expected = digest(cohort), digest(generate(name))
        assert actual == expected == phase["cohort_sha256"], f"Cohort fingerprint mismatch: {name}"
        assert len(cohort) == phase["attempted"] == phase["settings"]["n"]
        assert sum(r["ok"] for r in cohort) == phase["successful"]
        for row in cohort:
            assert row["transaction_id"] not in identifiers, "Duplicate simulated transaction ID"
            identifiers.add(row["transaction_id"])
        audit[name] = {"n": len(cohort), "regenerated_sha256": expected, "captured_sha256": actual,
                       "matches_regeneration": True, "successful": phase["successful"], "errors": phase["errors"]}
    save(out / "cohort-audit.json", audit)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    rows = {s: [json.loads(line) for line in (source / f"{s}-transactions.jsonl").read_text().splitlines()] for s in CORE}
    drift_results = {s: drift(rows["baseline"], rows[s], rng, s == "baseline") for s in CORE}
    qualities = {s: quality(rows[s], rng) for s in CORE}
    ops = summary["scenarios"]
    for name, phase in ops.items():
        phase["resource_deltas"] = resource_deltas(phase)
        phase["bridge_resource_deltas"] = resource_deltas(phase, "bridge_before", "bridge_after")
        if name.startswith("cpu_"):
            phase["server"] = {kind: prometheus_histogram(source / (name+"-before.prom"), source / (name+"-after.prom"), metric, kind == "http")
                for kind, metric in [("http", "http_server_requests_seconds"), ("onnx", "fraud_onnx_inference_duration_seconds")]}
    save(out / "operations.json", summary)
    save(out / "drift.json", drift_results)
    save(out / "quality.json", qualities)
    save(out / "analysis-method.json", {"bootstrap_resamples": BOOTSTRAPS, "bootstrap_seed": BOOTSTRAP_SEED,
        "interval": "95% percentile iid row bootstrap; paired feedback masks; fixed baseline bins",
        "smoothing": "0.5 added to every histogram/category count, then normalized",
        "categorical_js": "divergence in bits, not square-root distance",
        "pr_auc": "sklearn average_precision_score; undefined for fewer than two classes",
        "review_threshold": THRESHOLD, "raw_transactions_exported": False})
    plot(out, ops, drift_results, qualities)
    print(f"Aggregates and overview: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.source, args.output)
