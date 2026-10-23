#!/usr/bin/env python3
"""Derive a compact, reproducible summary from EXP-004 raw evidence."""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path


def observed_range(values):
    available = [value for value in values if math.isfinite(value) and value >= 0]
    return [min(available), max(available)] if available else None


def analyze(directory):
    directory = Path(directory)
    summary = json.loads((directory / "summary.json").read_text())
    requests = [json.loads(line) for line in (directory / "requests.jsonl").read_text().splitlines()]
    result = {"complete": summary["complete"], "sampled_predictions": len(requests),
        "sampled_prediction_errors": sum(not r["ok"] for r in requests),
        "request_counts": dict(collections.Counter(r["phase"] for r in requests)),
        "load_client": json.loads((directory / "load-client.json").read_text())}
    histograms = json.loads((directory / "load-server-histograms.json").read_text())
    result["load_server_histogram_ms"] = {name: {q: round(float(rows[0]["value"][1]) * 1000, 6)
        for q, rows in values.items() if q != "count_increase_estimate"}
        for name, values in histograms["metrics"].items()}
    for phase in ["recovery", "rollout"]:
        samples = [json.loads(line) for line in (directory / f"{phase}-timeline.jsonl").read_text().splitlines()]
        action = summary["scenarios"][phase]["action_at_elapsed_seconds"]
        original = {p["uid"] for p in samples[0]["pods"]}
        transitions = {}
        for sample in samples:
            if sample["elapsed_seconds"] < action:
                continue
            elapsed = round(sample["elapsed_seconds"] - action, 3)
            targets = {t["labels"]["pod"] for t in sample["prometheus_targets"] if t["health"] == "up"}
            for pod in sample["pods"]:
                item = transitions.setdefault(pod["name"], {"image": pod["image"],
                    "original": pod["uid"] in original, "first_observed_seconds": elapsed,
                    "max_restarts": 0, "termination_states": []})
                for flag in ["ready", "deleting"]:
                    if pod[flag]:
                        item.setdefault(f"first_{flag}_seconds", elapsed)
                if pod["name"] in targets:
                    item.setdefault("first_healthy_target_seconds", elapsed)
                for container in pod["containers"]:
                    item["max_restarts"] = max(item["max_restarts"], container["restartCount"])
                    for status in [container.get("state", {}), container.get("lastState", {})]:
                        if "terminated" in status and status["terminated"] not in item["termination_states"]:
                            item["termination_states"].append(status["terminated"])
        rows = [r for r in requests if r["phase"] == phase]
        dates = [dt.datetime.fromisoformat(r["at"]) for r in rows]
        result[phase] = {"pods": transitions,
            "minimum_ready_pods": min(sum(p["ready"] for p in s["pods"]) for s in samples),
            "maximum_total_pods": max(len(s["pods"]) for s in samples),
            "scraped_healthy_count_range": [min(s["prometheus_up_sum"] for s in samples), max(s["prometheus_up_sum"] for s in samples)],
            "max_status_sample_gap_seconds": round(max(b["elapsed_seconds"] - a["elapsed_seconds"] for a, b in zip(samples, samples[1:])), 3),
            "max_request_start_gap_seconds": round(max((b-a).total_seconds() for a,b in zip(dates,dates[1:])), 3),
            "converged_observed_seconds": summary["scenarios"][phase]["restored_observed_seconds"]}
        if phase == "recovery":
            restored = next(s for s in samples if s["elapsed_seconds"] > action
                and sum(p["ready"] for p in s["pods"]) == 3
                and any(p["uid"] not in original for p in s["pods"]))
            result[phase]["three_ready_restored_seconds"] = round(restored["elapsed_seconds"]-action, 3)
    memory = collections.defaultdict(list)
    peaks = collections.defaultdict(list)
    cpu = collections.defaultdict(list)
    heap = collections.defaultdict(list)
    gc = collections.defaultdict(list)
    for line in (directory / "load-resources.jsonl").read_text().splitlines():
        sample = json.loads(line)
        for pod, raw in sample["pods"].items():
            if isinstance(raw, str):
                values = raw.splitlines()
                memory[pod].append(int(values[0]))
                peaks[pod].append(int(values[1]))
        for row in sample["process_cpu_usage"]:
            cpu[row["metric"]["pod"]].append(float(row["value"][1]))
        for metric, destination, predicate in [("jvm_memory_used_bytes", heap, lambda m:m.get("area")=="heap"),
                ("jvm_gc_pause_seconds_count", gc, lambda m:True)]:
            totals = collections.Counter()
            for row in sample[metric]:
                if predicate(row["metric"]):
                    totals[row["metric"]["pod"]] += float(row["value"][1])
            for pod, value in totals.items():
                destination[pod].append(value)
    result["resources"] = {pod: {"sampled_cgroup_memory_current_bytes": [min(values), max(values)],
        "cgroup_memory_peak_bytes": max(peaks[pod]),
        "sampled_jvm_heap_bytes": observed_range(heap[pod]),
        "sampled_process_cpu_fraction": observed_range(cpu[pod]),
        "gc_pause_count_delta_between_samples": gc[pod][-1]-gc[pod][0] if gc[pod] else None}
        for pod, values in memory.items()}
    result["alerts"] = json.loads((directory / "alerts-firing.json").read_text())["data"]["alerts"]
    result["alerts_resolved"] = json.loads((directory / "alerts-resolved.json").read_text())["data"]["alerts"] == []
    (directory / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    print(json.dumps(analyze(parser.parse_args().directory), indent=2))
