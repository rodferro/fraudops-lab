#!/usr/bin/env python3
"""EXP-004 runtime acceptance, using only stdlib and the isolated kind context.

Requires exp004.sh build/images/deploy. Deletes one API pod, rolls to exp004-r1,
and temporarily starts a bounded alert-demo Job. Saves API responses as evidence.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from exp003_verify import Experiment, FIXTURE, ROOT, utc, write_json

SELECT = 'job="fraudops-api",app="fraudops-api"'
HTTP = SELECT + ',uri="/api/v1/predict"'


class ObservabilityExperiment(Experiment):
    def __init__(self, args):
        super().__init__(args)
        self.prom = "http://127.0.0.1:19090"
        self.grafana = "http://127.0.0.1:13000"
        self.jaeger = "http://127.0.0.1:16686"
        self.started = time.time()
        self.trace_since = self.started

    def api(self, base, path, **parameters):
        url = base + path
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)

    def query(self, expression):
        result = self.api(self.prom, "/api/v1/query", query=expression)
        assert result["status"] == "success", result
        return result["data"]["result"]

    def value(self, expression):
        rows = self.query(expression)
        return float(rows[0]["value"][1]) if rows else None

    def targets(self):
        return self.api(self.prom, "/api/v1/targets", state="active")["data"]["activeTargets"]

    def target_names(self, targets):
        return {t["labels"]["pod"] for t in targets
                if t["labels"].get("app") == "fraudops-api" and t["health"] == "up"}

    def targets_match(self, sample):
        ready = {p["name"] for p in sample["pods"] if p["ready"]}
        return self.target_names(sample["prometheus_targets"]) == ready

    def snapshot(self):
        sample = super().snapshot()
        sample["prometheus_targets"] = [{"labels": t["labels"], "health": t["health"],
            "lastError": t["lastError"], "lastScrape": t["lastScrape"]} for t in self.targets()]
        sample["prometheus_up_sum"] = self.value(f"sum(up{{{SELECT}}})")
        return sample

    def wait_until(self, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(1)
        raise TimeoutError("Condition not observed within timeout")

    def stack_forward(self, resource, port, target, health):
        log = (self.out / f"forward-{port}.log").open("w")
        process = subprocess.Popen(self.k + ["port-forward", "--address=127.0.0.1",
            resource, f"{port}:{target}"], stdout=log, stderr=subprocess.STDOUT)
        self.forwards.append((process, log))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Port-forward exited: {port}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{health}", timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"Port {port} not ready")

    def counters(self):
        expressions = {
            "http": f"sum(http_server_requests_seconds_count{{{HTTP}}})",
            "inference_success": f'sum(fraud_inference_total{{{SELECT},outcome="success"}})',
            "inference_error": f'sum(fraud_inference_total{{{SELECT},outcome="error"}})',
            "onnx_observations": f"sum(fraud_onnx_inference_duration_seconds_count{{{SELECT}}})",
            "approve": f'sum(fraud_decisions_total{{{SELECT},decision="approve"}})',
            "review": f'sum(fraud_decisions_total{{{SELECT},decision="review"}})'}
        return {name: self.value(expr) for name, expr in expressions.items()}

    def trace(self, phase):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.api(self.jaeger, "/api/traces", service="fraudops-api",
                operation="http post /api/v1/predict",
                limit=100, start=int(self.trace_since * 1_000_000), end=int(time.time() * 1_000_000))
            for trace in result.get("data", []):
                children = [s for s in trace["spans"] if s["operationName"] == "onnx.inference"]
                for child in children:
                    refs = [r for r in child.get("references", []) if r["refType"] == "CHILD_OF"]
                    parent = next((s for s in trace["spans"]
                        if refs and s["spanID"] == refs[0]["spanID"]), None)
                    if not parent:
                        continue
                    tags = {t["key"]: t["value"] for t in parent.get("tags", [])}
                    if tags.get("http.url", "").endswith("/api/v1/predict") or tags.get("http.route") == "/api/v1/predict":
                        assert child["traceID"] == parent["traceID"]
                        assert parent["startTime"] <= child["startTime"]
                        assert child["startTime"] + child["duration"] <= parent["startTime"] + parent["duration"]
                        http_spans = [s for s in trace["spans"] if any(
                            t["key"] == "span.kind" and t["value"] == "server" for t in s.get("tags", []))]
                        assert len(http_spans) == 1, "Duplicate HTTP server spans"
                        # No transaction features were attached by our instrumentation.
                        forbidden = set(FIXTURE["cases"][0]["features"]) | {"fraud_probability", "account_id", "request_id"}
                        for span in trace["spans"]:
                            assert not forbidden.intersection(t["key"] for t in span.get("tags", []))
                        write_json(self.out / f"{phase}-trace.json", trace)
                        return {"trace_id": trace["traceID"], "http_span_id": parent["spanID"],
                            "onnx_span_id": child["spanID"], "span_count": len(trace["spans"])}
            time.sleep(1)
        raise AssertionError("No retrievable HTTP -> ONNX trace")

    def grafana_queries(self, phase):
        definition = self.api(self.grafana, "/api/dashboards/uid/fraudops-api-overview")
        assert definition["dashboard"]["title"] == "FraudOps API Overview"
        write_json(self.out / f"{phase}-grafana-dashboard.json", definition)
        results = {}
        for panel in definition["dashboard"]["panels"]:
            queries = []
            for target in panel.get("targets", []):
                result = self.api(self.grafana,
                    "/api/datasources/proxy/uid/prometheus/api/v1/query_range",
                    query=target["expr"], start=self.started, end=time.time(), step=2)
                assert result["status"] == "success", result
                queries.append({"expression": target["expr"], "result": result})
            results[panel["title"]] = queries
        write_json(self.out / f"{phase}-grafana-panel-data.json", results)
        for title, queries in results.items():
            if "HTTP latency" in title or "ONNX call latency" in title:
                for query in queries:
                    assert any(math.isfinite(float(value)) for series in query["result"]["data"]["result"]
                        for _, value in series.get("values", [])), title
        return {"dashboard_uid": definition["dashboard"]["uid"], "panels_queried": len(results)}

    def resource_samples(self, stop, output):
        while not stop.is_set():
            sample = {"at": utc(), "pods": {}}
            for pod in self.pods():
                if not self.ready(pod):
                    continue
                name = pod["metadata"]["name"]
                try:
                    sample["pods"][name] = self.kubectl("exec", name, "--", "sh", "-c",
                        "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat")
                except Exception as error:
                    sample["pods"][name] = {"unavailable": str(error)}
            for metric in ["jvm_memory_used_bytes", "jvm_gc_pause_seconds_count", "jvm_threads_live_threads", "process_cpu_usage", "process_cpu_time_ns_total"]:
                sample[metric] = self.query(f"{metric}{{{SELECT}}}")
            with output.open("a") as file:
                file.write(json.dumps(sample) + "\n")
            stop.wait(1)

    def alerts(self):
        self.kubectl("apply", "-f", str(ROOT / "k8s/observability/demo.yaml"))
        try:
            self.kubectl("wait", "--for=condition=Ready", "pod", "-l", "app=fraudops-alert-demo", "--timeout=90s")
            self.stack_forward("service/fraudops-alert-demo", 18082, 8080, "/actuator/health/readiness")
            self.wait_until(lambda: any(t["labels"].get("app") == "fraudops-alert-demo" and t["health"] == "up" for t in self.targets()))
            history = []
            outcomes = collections.Counter()
            for iteration in range(70):
                for endpoint, expected in [("failure", 500), ("latency", 200)]:
                    request = urllib.request.Request("http://127.0.0.1:18082/internal/observability/demo/" + endpoint,
                        method="POST", data=b"")
                    try:
                        with urllib.request.urlopen(request, timeout=5) as response:
                            status = response.status
                            response.read()
                    except urllib.error.HTTPError as error:
                        status = error.code
                        error.read()
                    outcomes[f"{endpoint}:{status}"] += 1
                    assert status == expected, f"Unexpected demo outcome {endpoint}: {status}"
                alerts = self.api(self.prom, "/api/v1/alerts")
                history.append({"at": utc(), "alerts": alerts})
                firing = {a["labels"]["alertname"] for a in alerts["data"]["alerts"]
                    if a["state"] == "firing" and a["labels"].get("app") == "fraudops-alert-demo"}
                if {"FraudOpsElevated5xxRatio", "FraudOpsHighP95Latency"} <= firing:
                    write_json(self.out / "alerts-firing.json", alerts)
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("Both alert rules did not fire during bounded demo")
            write_json(self.out / "alerts-history.json", history)
            write_json(self.out / "alert-demo-targets.json", self.targets())
            write_json(self.out / "alert-demo-inference-errors.json", self.query(
                'fraud_inference_total{app="fraudops-alert-demo",outcome="error"}'))
            return {"outcomes": dict(outcomes), "firing_alerts": sorted(firing),
                "lifetime_seconds": 180, "request_budget": 160}
        finally:
            self.kubectl("delete", "-f", str(ROOT / "k8s/observability/demo.yaml"), "--ignore-not-found", "--wait=false")

    def run(self):
        self.stack_forward("service/prometheus", 19090, 9090, "/-/ready")
        self.stack_forward("service/grafana", 13000, 3000, "/api/health")
        self.stack_forward("service/jaeger", 16686, 16686, "/api/services")
        self.forward("service/fraudops-api", int(self.local.rsplit(":", 1)[1]), 8080)
        self.forward("pod/fraudops-client", int(self.bridge.rsplit(":", 1)[1]), 8081)
        self.kubectl("rollout", "status", "deployment/fraudops-api", "--timeout=180s")
        # rollout status can succeed while old terminating Pods still exist.
        def initial_ready():
            pods = self.pods()
            return len(pods) == 3 and all(self.ready(p) and
                p["spec"]["containers"][0]["image"] == "fraudops-api:exp004" for p in pods)
        self.wait_until(initial_ready)
        self.wait_until(lambda: self.targets_match(self.snapshot()))
        write_json(self.out / "initial-targets.json", self.targets())
        write_json(self.out / "versions.json", json.loads(self.kubectl("version", "-o", "json")))
        write_json(self.out / "initial-state.json", self.snapshot())
        self.summary["parity"] = self.parity(self.bridge, "parity")
        time.sleep(5)  # allow every pod's post-parity scrape to arrive
        before = self.counters()
        expected = collections.Counter()
        for index in range(100):
            result = self.predict(self.bridge, "normal", FIXTURE["cases"][index % 2 * 2])
            assert result["ok"], result
            expected[result["response"]["decision"]] += 1
        time.sleep(5)
        after = self.counters()
        delta = {name: after[name] - (before[name] or 0) for name in after}
        assert delta["http"] == delta["inference_success"] == delta["onnx_observations"] == 100, delta
        assert delta["inference_error"] == 0 and delta["approve"] == expected["approve"] and delta["review"] == expected["review"], delta
        self.summary["scenarios"]["normal"] = {"before": before, "after": after, "delta": delta, "response_decisions": dict(expected)}
        self.summary["normal_trace"] = self.trace("normal")
        self.summary["normal_grafana"] = self.grafana_queries("normal")
        print("Normal workload, exact counter deltas, trace and Grafana queries passed", flush=True)

        time.sleep(6)
        load_start = time.time()
        stop = threading.Event()
        resource_thread = threading.Thread(target=self.resource_samples,
            args=(stop, self.out / "load-resources.jsonl"))
        resource_thread.start()
        try:
            command = ["python3", str(ROOT / "scripts/load_test.py"), "--base-url", self.bridge,
                "--requests", "2000", "--concurrency", "16", "--warmup-requests", "200"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (self.out / "load-stderr.txt").write_text(result.stderr)
            assert result.returncode == 0, result.stderr
            load = json.loads(result.stdout)
            write_json(self.out / "load-client.json", load)
            time.sleep(5)
        finally:
            stop.set()
            resource_thread.join(timeout=30)
        window = math.ceil(time.time() - load_start) + 2
        server = {}
        for name, metric, selector in [("http", "http_server_requests_seconds", HTTP),
                ("onnx", "fraud_onnx_inference_duration_seconds", SELECT)]:
            server[name] = {str(q): self.query(f"histogram_quantile({q}, sum by (le) (increase({metric}_bucket{{{selector}}}[{window}s])))") for q in [.5, .95, .99]}
            server[name]["count_increase_estimate"] = self.query(f"sum(increase({metric}_count{{{selector}}}[{window}s]))")
        write_json(self.out / "load-server-histograms.json", {"window_seconds": window, "metrics": server})
        self.summary["scenarios"]["load"] = {"client": load, "server_window_seconds": window, "server": server}
        assert load["error_count"] == 0, load
        print("Fixed 2,000-request load and resource sampling completed", flush=True)

        initial = self.snapshot()
        uids = {p["uid"] for p in initial["pods"]}
        victim = initial["pods"][0]["name"]
        def recovered(sample):
            return (len(sample["pods"]) == 3 and sum(p["ready"] for p in sample["pods"]) == 3
                and all(p["name"] != victim for p in sample["pods"])
                and sample["deployment"].get("availableReplicas", 0) == 3 and self.targets_match(sample))
        self.trace_since = time.time()
        self.action("recovery", ["delete", "pod", victim, "--wait=false"], recovered, uids)
        self.summary["recovery_trace"] = self.trace("recovery")
        self.summary["recovery_grafana"] = self.grafana_queries("recovery")
        print("Pod replacement and Prometheus rediscovery passed", flush=True)

        def rolled(sample):
            return (len(sample["pods"]) == 3 and all(p["ready"] and p["image"] == "fraudops-api:exp004-r1" for p in sample["pods"])
                and sample["deployment"].get("availableReplicas", 0) == 3 and self.targets_match(sample))
        self.trace_since = time.time()
        self.action("rollout", ["set", "image", "deployment/fraudops-api", "api=fraudops-api:exp004-r1"], rolled)
        (self.out / "rollout-status.txt").write_text(self.kubectl("rollout", "status", "deployment/fraudops-api", "--timeout=180s"))
        self.summary["final_parity"] = self.parity(self.bridge, "revision-parity")
        self.summary["rollout_trace"] = self.trace("rollout")
        self.summary["rollout_grafana"] = self.grafana_queries("rollout")
        final = self.snapshot()
        write_json(self.out / "final-state.json", final)
        base_ids = {c["imageID"] for p in initial["pods"] for c in p["containers"]}
        final_ids = {c["imageID"] for p in final["pods"] for c in p["containers"]}
        assert base_ids.isdisjoint(final_ids), "Runtime image IDs did not change"
        self.summary["image_ids"] = {"base": sorted(base_ids), "revision": sorted(final_ids)}
        for pod in final["pods"]:
            logs = self.kubectl("logs", pod["name"])
            (self.out / f"{pod['name']}.log").write_text(logs)
            assert "image=exp004-r1" in logs
        print("Real image rollout, telemetry continuity and parity passed", flush=True)

        self.summary["scenarios"]["alerts"] = self.alerts()
        self.wait_until(lambda: not self.api(self.prom, "/api/v1/alerts")["data"]["alerts"], timeout=60)
        write_json(self.out / "alerts-resolved.json", self.api(self.prom, "/api/v1/alerts"))
        self.summary["alerts_resolved"] = True
        self.trace_since = time.time()
        assert self.predict(self.bridge, "post-alerts")["ok"]
        self.summary["final_trace"] = self.trace("final")
        write_json(self.out / "final-targets.json", self.targets())
        write_json(self.out / "final-pods.json", self.get("pods"))
        self.summary["complete"] = True
        print("Both alerts fired and resolved; EXP-004 runtime checks passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--api-port", type=int, default=18080)
    parser.add_argument("--client-port", type=int, default=18081)
    args = parser.parse_args()
    experiment = ObservabilityExperiment(args)
    try:
        experiment.run()
    except Exception as error:
        experiment.summary["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        experiment.finish()
        print(json.dumps(experiment.summary, indent=2))


if __name__ == "__main__":
    main()
