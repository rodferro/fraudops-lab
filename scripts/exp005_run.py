#!/usr/bin/env python3
"""Bounded EXP-005 runtime runner. Raw features/labels stay in ignored artifacts."""
import argparse
import concurrent.futures as cf
import collections
import hashlib
import json
import math
from pathlib import Path
import subprocess
import shutil
import threading
import time
import urllib.error
import urllib.request

import numpy as np

from exp003_verify import ROOT, utc, write_json
from exp004_verify import ObservabilityExperiment, HTTP, SELECT
from exp005_data import SCENARIOS, THRESHOLD, digest, feedback, generate


def percentiles(values):
    return {f"p{p}": float(np.percentile(values, p)) if values else None for p in [50, 95, 99]}


class Run(ObservabilityExperiment):
    def __init__(self, args):
        super().__init__(args)
        self.reuse_traffic = args.reuse_traffic

    def resource_state(self, label="app=fraudops-api"):
        result = []
        for p in self.get("pods", "-l", label)["items"]:
            name = p["metadata"]["name"]
            row = {"pod": name, "uid": p["metadata"]["uid"], "ready": self.ready(p),
                   "resources": p["spec"]["containers"][0]["resources"],
                   "statuses": p["status"].get("containerStatuses", [])}
            try:
                text = self.kubectl("exec", name, "--", "sh", "-c",
                    "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat")
                lines = text.splitlines()
                row.update(memory_current=int(lines[0]), memory_peak=int(lines[1]),
                           cpu={k: int(v) for k, v in (s.split() for s in lines[2:])})
            except Exception as e:
                row["measurement_error"] = str(e)
            result.append(row)
        return result

    def access(self, name):
        counts = {}
        for p in self.pods():
            pod = p["metadata"]["name"]
            try:
                lines = self.kubectl("exec", pod, "--", "cat", "/tmp/access/requests.log").splitlines()
                counts[pod] = sum(line.startswith(f"exp005-{name}-") and " 200 " in line for line in lines)
            except Exception:
                counts[pod] = None
        return counts

    def server(self, start, end):
        # Scrape-based estimates. Extrapolated increases are explicitly not exact counts.
        window = int(math.ceil(end-start)) + 2
        result = {"window_seconds": window, "method": "Prometheus increase, interpolated histogram quantiles"}
        for kind, metric, selector in [("http", "http_server_requests_seconds", HTTP),
                                        ("onnx", "fraud_onnx_inference_duration_seconds", SELECT)]:
            result[kind] = {}
            expressions = {"count_estimate": f"sum(increase({metric}_count{{{selector}}}[{window}s]))"}
            for p in [50, 95, 99]:
                expressions[f"p{p}_ms"] = (f"1000*histogram_quantile({p/100},sum by(le)"
                    f"(increase({metric}_bucket{{{selector}}}[{window}s])))")
            for key, query in expressions.items():
                data = self.api(self.prom, "/api/v1/query", query=query, time=end)["data"]["result"]
                value = float(data[0]["value"][1]) if data else None
                result[kind][key] = value if value is not None and math.isfinite(value) else None
        return result

    def raw_metrics(self, url, name):
        with urllib.request.urlopen(url + "/actuator/prometheus", timeout=10) as r:
            (self.out / name).write_bytes(r.read())

    def phase(self, name, url=None, cpu=False, deletion=False):
        url = url or self.bridge
        settings = SCENARIOS[name]
        rows = generate(name)
        cohort_hash = digest(rows)
        label = "app=fraudops-exp005-cpu" if cpu else "app=fraudops-api"
        before = self.resource_state(label)
        bridge_before = self.resource_state("app=fraudops-client") if not cpu else []
        if cpu:
            self.raw_metrics(url, name + "-before.prom")
        stop = threading.Event()
        timeline, observer_errors, action = [], [], {}
        started_wall, started = time.time(), time.monotonic()

        def observe():
            while not stop.is_set():
                try:
                    sample = self.snapshot()
                    sample["elapsed_seconds"] = time.monotonic()-started
                    timeline.append(sample)
                    with (self.out / f"{name}-timeline.jsonl").open("a") as f:
                        f.write(json.dumps(sample) + "\n")
                except Exception as e:
                    observer_errors.append(str(e))
                stop.wait(1)

        def mutate():
            if stop.wait(8):
                return
            try:
                victim = next(p["metadata"]["name"] for p in self.pods() if self.ready(p))
                action.update(victim=victim, access_before_delete=self.access(name), at=utc(),
                              elapsed_seconds=time.monotonic()-started)
                self.kubectl("delete", "pod", victim, "--wait=false")
            except Exception as e:
                action["error"] = str(e)

        observer = threading.Thread(target=observe)
        mutator = threading.Thread(target=mutate) if deletion else None
        observer.start()
        if mutator:
            mutator.start()

        def request(row, i):
            row.update(request_scheduled_at=started_wall+i/settings["rps"], request_started_at=utc(),
                       request_start_elapsed=time.monotonic()-started)
            tick = time.monotonic()
            row["schedule_lag_ms"] = max(0, (tick-started-i/settings["rps"])*1000)
            row.update(status=None, ok=False, http_completed=False)
            try:
                # Explicit feature allowlist: no private label, ID, feedback, or timestamp in JSON.
                req = urllib.request.Request(url + "/api/v1/predict", method="POST",
                    data=json.dumps(row["features"], allow_nan=False).encode(),
                    headers={"Content-Type": "application/json", "X-EXP003-Request": row["transaction_id"]})
                with urllib.request.urlopen(req, timeout=5) as response:
                    row.update(status=response.status, http_completed=True)
                    body = json.load(response)
                row.update(probability=body["fraud_probability"], decision=body["decision"],
                           model_version=body["model_version"])
                if row["model_version"] != "1.0.0-exp000" or not 0 <= row["probability"] <= 1:
                    raise ValueError("Invalid probability/model version")
                expected = "review" if row["probability"] >= THRESHOLD else "approve"
                if row["decision"] != expected:
                    raise ValueError("Policy mismatch")
                row["ok"] = row["status"] == 200
            except urllib.error.HTTPError as e:
                row.update(status=e.code, http_completed=True, error=f"HTTP {e.code}")
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
            row.update(latency_ms=(time.monotonic()-tick)*1000, request_finished_at=utc())

        try:
            # At most 2*C outstanding tasks, C active requests; backpressure is measured.
            pending = set()
            with cf.ThreadPoolExecutor(max_workers=settings["concurrency"]) as pool:
                for i, row in enumerate(rows):
                    delay = started+i/settings["rps"]-time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    while len(pending) >= 2*settings["concurrency"]:
                        _, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
                    if time.monotonic()-started > 180:
                        for remaining in rows[i:]:
                            remaining.update(ok=False, status=None, http_completed=False,
                                             error="phase_deadline_not_dispatched")
                        break
                    pending.add(pool.submit(request, row, i))
                for future in pending:
                    future.result()
        finally:
            ended, ended_wall = time.monotonic(), time.time()
            stop.set()
            observer.join(timeout=20)
            if mutator:
                mutator.join(timeout=20)
        # Observation can outlast traffic by a few seconds; excluded from client throughput.
        after = self.resource_state(label)
        bridge_after = self.resource_state("app=fraudops-client") if not cpu else []
        if cpu:
            self.raw_metrics(url, name + "-after.prom")
        else:
            time.sleep(3)  # ensure final traffic has been scraped; query evaluated at measurement end
        cutoffs = feedback(rows, settings["seed"], selected=name == "concept")
        with (self.out / f"{name}-transactions.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row, allow_nan=False) + "\n")
        ok = [r for r in rows if r["ok"]]
        result = {"settings": settings, "cohort_sha256": cohort_hash, "started_at": started_wall,
            "ended_at": ended_wall, "duration_seconds": ended-started, "attempted": len(rows),
            "dispatched": sum("request_started_at" in r for r in rows),
            "http_completed": sum(r["http_completed"] for r in rows), "successful": len(ok),
            "errors": len(rows)-len(ok), "status_counts": dict(collections.Counter(str(r["status"]) for r in rows)),
            "throughput_rps": len(ok)/(ended-started),
            "client_latency_ms": percentiles([r["latency_ms"] for r in rows if "latency_ms" in r]),
            "success_latency_ms": percentiles([r["latency_ms"] for r in ok]),
            "schedule_lag_ms": percentiles([r["schedule_lag_ms"] for r in rows if "schedule_lag_ms" in r]),
            "before": before, "after": after, "bridge_before": bridge_before, "bridge_after": bridge_after,
            "observer_errors": observer_errors, "action": action, "cutoffs": cutoffs,
            "server": None if cpu else self.server(started_wall, ended_wall),
            "per_pod_successes": {} if cpu else self.access(name),
            "ready_counts": dict(collections.Counter(sum(p["ready"] for p in s["pods"]) for s in timeline))}
        if deletion and action.get("at"):
            initial = {p["uid"] for p in timeline[0]["pods"]}
            t0 = action["elapsed_seconds"]
            new = next((s for s in timeline if s["elapsed_seconds"] >= t0 and
                        any(p["uid"] not in initial for p in s["pods"])), None)
            restored = next((s for s in timeline if s["elapsed_seconds"] >= t0 and
                            sum(p["ready"] for p in s["pods"]) == 3 and
                            any(p["uid"] not in initial and p["ready"] for p in s["pods"])), None)
            targets = next((s for s in timeline if restored and s["elapsed_seconds"] >= restored["elapsed_seconds"]
                            and self.targets_match(s)), None)
            result["recovery"] = {
                "first_replacement_seconds": new["elapsed_seconds"]-t0 if new else None,
                "ready_restored_seconds": restored["elapsed_seconds"]-t0 if restored else None,
                "prometheus_targets_restored_seconds": targets["elapsed_seconds"]-t0 if targets else None,
                "observation_interval": "approximately 1s plus kubectl/Prometheus calls"}
            until = restored["elapsed_seconds"] if restored else ended-started
            during = [r for r in rows if t0 <= r.get("request_start_elapsed", -1) <= until]
            result["recovery"].update(requests_during=len(during), errors_during=sum(not r["ok"] for r in during),
                                      latency_during_ms=percentiles([r["latency_ms"] for r in during]))
            if not restored:
                raise AssertionError("Desired ready count not restored during bounded deletion workload")
        write_json(self.out / f"{name}-operations.json", result)
        self.summary["scenarios"][name] = result
        write_json(self.out / "summary.json", self.summary)
        print(json.dumps({"phase": name, "success": len(ok), "errors": result["errors"],
                          "rps": result["throughput_rps"], "p95_ms": result["client_latency_ms"]["p95"]}), flush=True)

    def apply(self, obj):
        subprocess.run(self.k + ["apply", "-f", "-"], input=json.dumps(obj), text=True,
                       check=True, capture_output=True, timeout=25)

    def cpu_ready(self, limit):
        pods = self.get("pods", "-l", "app=fraudops-exp005-cpu")["items"]
        return len(pods) == 1 and self.ready(pods[0]) and pods[0]["spec"]["containers"][0]["resources"]["limits"]["cpu"] == limit

    def cpu_forward(self):
        self.stack_forward("deployment/fraudops-exp005-cpu", 18083, 8080, "/actuator/health/readiness")
        # Same bounded warmup on both resource settings; excluded from measurement.
        for _ in range(100):
            record = self.predict("http://127.0.0.1:18083", "cpu-warmup")
            assert record["ok"]

    def run(self):
        original = self.get("deployment", "fraudops-api")
        if self.reuse_traffic:
            source = Path(self.reuse_traffic).resolve()
            previous = json.loads((source / "summary.json").read_text())
            assert not previous["complete"] and "cleanup_error" not in previous
            assert set(previous["scenarios"]) == set(SCENARIOS)-{"cpu_limited"}
            assert previous["main_original_spec"] == original["spec"]
            assert previous["model_sha256"] == hashlib.sha256((ROOT / "fraud_model.onnx").read_bytes()).hexdigest()
            assert previous["metadata_sha256"] == hashlib.sha256((ROOT / "model_metadata.json").read_bytes()).hexdigest()
            self.summary.update(previous)
            self.summary["prior_attempt"] = {"run_directory": source.name,
                "failure": self.summary.pop("failure"), "finished_at": self.summary.pop("finished_at")}
            self.summary["resumed_at"] = utc()
            # Copy captures; leave the failed run untouched and never overwrite evidence.
            for path in source.iterdir():
                if path.suffix in {".jsonl", ".prom"} or path.name.endswith("-operations.json"):
                    shutil.copy2(path, self.out / path.name)
        self.summary["main_original_spec"] = original["spec"]
        self.summary["model_sha256"] = hashlib.sha256((ROOT / "fraud_model.onnx").read_bytes()).hexdigest()
        self.summary["metadata_sha256"] = hashlib.sha256((ROOT / "model_metadata.json").read_bytes()).hexdigest()
        assert len(self.pods()) == 3 and all(self.ready(p) for p in self.pods())
        self.stack_forward("service/prometheus", 19090, 9090, "/-/ready")
        self.forward("pod/fraudops-client", 18081, 8081)
        self.summary["parity_before"] = self.parity(self.bridge, "exp005-parity-before")
        for name in ["baseline", "burst", "recovery", "covariate", "concept", "deletion"]:
            if name not in self.summary["scenarios"]:
                self.phase(name, deletion=name == "deletion")
        template = json.loads(json.dumps(original["spec"]["template"]))
        template["metadata"] = {"labels": {"app": "fraudops-exp005-cpu"}}
        api = template["spec"]["containers"][0]
        api["resources"]["requests"]["cpu"] = "50m"
        # A 100m cold start exceeds the inherited ~120s startup budget on this host.
        # Extend only the temporary clone's probe budget, never the primary API's.
        api["startupProbe"]["failureThreshold"] = 180
        initial_limit = "100m" if self.reuse_traffic else "1"
        api["resources"]["limits"]["cpu"] = initial_limit
        api["env"] = [e for e in api.get("env", []) if e["name"] != "FRAUD_TRACE_EXPORT_ENABLED"]
        api["env"].append({"name": "FRAUD_TRACE_EXPORT_ENABLED", "value": "false"})
        clone = {"apiVersion": "apps/v1", "kind": "Deployment",
                 "metadata": {"name": "fraudops-exp005-cpu", "namespace": "fraudops-exp003"},
                 "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "fraudops-exp005-cpu"}},
                          "template": template}}
        write_json(self.out / "cpu-deployment.json", clone)
        self.apply(clone)
        self.wait_until(lambda: self.cpu_ready(initial_limit), timeout=420)
        self.cpu_forward()
        if not self.reuse_traffic:
            self.phase("cpu_normal", "http://127.0.0.1:18083", cpu=True)
            process, log = self.forwards.pop()
            process.terminate()
            process.wait(timeout=10)
            log.close()
            self.kubectl("set", "resources", "deployment/fraudops-exp005-cpu", "--limits=cpu=100m,memory=512Mi")
            self.wait_until(lambda: self.cpu_ready("100m"), timeout=420)
            self.cpu_forward()
        self.phase("cpu_limited", "http://127.0.0.1:18083", cpu=True)
        self.summary["parity_after"] = self.parity(self.bridge, "exp005-parity-after")
        self.summary["main_spec_unchanged"] = original["spec"] == self.get("deployment", "fraudops-api")["spec"]
        assert self.summary["main_spec_unchanged"]
        self.summary["complete"] = True

    def close(self):
        try:
            # Restore the temporary limit explicitly, then remove the idle test deployment.
            exists = self.kubectl("get", "deployment", "fraudops-exp005-cpu", "--ignore-not-found", "-o", "name")
            if exists.strip():
                self.kubectl("set", "resources", "deployment/fraudops-exp005-cpu", "--limits=cpu=1,memory=512Mi")
                self.summary["cpu_restored_limit"] = self.get("deployment", "fraudops-exp005-cpu")["spec"]["template"]["spec"]["containers"][0]["resources"]
                self.kubectl("delete", "deployment", "fraudops-exp005-cpu", "--wait=false")
            self.summary["final_resources"] = self.resource_state()
        except Exception as e:
            self.summary["cleanup_error"] = str(e)
        for process, log in self.forwards:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            log.close()
        self.summary["finished_at"] = utc()
        write_json(self.out / "summary.json", self.summary)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--kubeconfig", default="artifacts/EXP-003/kubeconfig")
    p.set_defaults(api_port=18080, client_port=18081)
    p.add_argument("--reuse-traffic", help="resume the seven completed phases of an interrupted CPU-startup run into a new directory")
    args = p.parse_args()
    run = Run(args)
    try:
        run.run()
    except BaseException as error:
        run.summary["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        run.close()
