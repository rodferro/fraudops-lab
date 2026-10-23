#!/usr/bin/env python3
"""Destructive-to-test-pods EXP-003 scenarios; stdlib only, isolated kind context.

Requires exp003.sh create/images/deploy. Leaves the deployment at three r1 pods.
Writes raw evidence before asserting acceptance; no request retries or invented data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "src/test/resources/parity-cases.json").read_text())
BASE = "fraudops-api:exp002"
REVISION = "fraudops-api:exp003-r1"


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class Experiment:
    def __init__(self, args):
        self.out = Path(args.output).resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.k = ["kubectl", "--kubeconfig", str(Path(args.kubeconfig).resolve()),
                  "--context", "kind-fraudops-exp003", "--namespace", "fraudops-exp003",
                  "--request-timeout=10s"]
        self.local = f"http://127.0.0.1:{args.api_port}"
        self.bridge = f"http://127.0.0.1:{args.client_port}"
        self.forwards = []
        self.summary = {"started_at": utc(), "complete": False, "scenarios": {}}
        self.counter = 0

    def kubectl(self, *args):
        result = subprocess.run(self.k + list(args), text=True, capture_output=True, timeout=25)
        if result.returncode:
            raise RuntimeError(f"kubectl {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def get(self, resource, *args):
        return json.loads(self.kubectl("get", resource, *args, "-o", "json"))

    def pods(self):
        return self.get("pods", "-l", "app=fraudops-api")["items"]

    @staticmethod
    def ready(pod):
        return not pod["metadata"].get("deletionTimestamp") and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in pod["status"].get("conditions", []))

    def snapshot(self):
        pods = self.pods()
        deploy = self.get("deployment", "fraudops-api")
        endpoints = self.get("endpointslices", "-l", "kubernetes.io/service-name=fraudops-api")
        return {"at": utc(), "pods": [{
            "name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
            "created": p["metadata"]["creationTimestamp"],
            "deleting": p["metadata"].get("deletionTimestamp"),
            "phase": p["status"]["phase"], "ready": self.ready(p),
            "conditions": p["status"].get("conditions", []),
            "image": p["spec"]["containers"][0]["image"],
            "containers": p["status"].get("containerStatuses", [])} for p in pods],
            "deployment": deploy["status"],
            "endpoints": [e for s in endpoints["items"] for e in s.get("endpoints", [])]}

    def predict(self, url, phase, case=None):
        case = case or FIXTURE["cases"][0]
        self.counter += 1
        identifier = f"{phase}-{self.counter}"
        record = {"at": utc(), "id": identifier, "phase": phase, "case": case["name"]}
        start = time.monotonic()
        try:
            request = urllib.request.Request(url + "/api/v1/predict",
                data=json.dumps(case["features"]).encode(),
                headers={"Content-Type": "application/json", "X-EXP003-Request": identifier},
                method="POST")
            with urllib.request.urlopen(request, timeout=8) as response:
                body = json.load(response)
                record.update(status=response.status, response=body)
            expected_decision = "review" if case["fraud_probability"] >= 0.06729906797409058 else "approve"
            if body["model_version"] != FIXTURE["model_version"]:
                raise ValueError("Model version changed")
            if not math.isclose(body["fraud_probability"], case["fraud_probability"],
                    abs_tol=FIXTURE["absolute_tolerance"], rel_tol=FIXTURE["relative_tolerance"]):
                raise ValueError("Fixture probability mismatch")
            if body["decision"] != expected_decision:
                raise ValueError("Policy decision mismatch")
            record["ok"] = True
        except Exception as error:
            record.update(ok=False, error=str(error))
        record["duration_ms"] = round((time.monotonic() - start) * 1000, 3)
        with (self.out / "requests.jsonl").open("a") as output:
            output.write(json.dumps(record) + "\n")
        return record

    def parity(self, url, phase):
        records = [self.predict(url, phase, case) for case in FIXTURE["cases"]]
        assert all(r["ok"] for r in records), records
        return records

    def forward(self, resource, port, target):
        log = (self.out / f"forward-{port}.log").open("w")
        process = subprocess.Popen(self.k + ["port-forward", "--address=127.0.0.1",
            resource, f"{port}:{target}"], stdout=log, stderr=subprocess.STDOUT)
        self.forwards.append((process, log))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Port-forward exited: see forward-{port}.log")
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/actuator/health/readiness",
                    headers={"X-EXP003-Request": "forward-startup"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError(f"Port-forward on {port} not ready")

    def access_logs(self, phase):
        counts = {}
        for pod in self.pods():
            name = pod["metadata"]["name"]
            content = self.kubectl("exec", name, "--", "cat", "/tmp/access/requests.log")
            (self.out / f"{phase}-{name}-access.log").write_text(content)
            counts[name] = sum(line.startswith("scale-") and " 200 " in line
                               for line in content.splitlines())
        return counts

    def action(self, phase, command, done, replacement_uids=None):
        stop = threading.Event()
        records = []
        samples = []
        observer_errors = []
        started = time.monotonic()

        def traffic():
            while not stop.is_set():
                records.append(self.predict(self.bridge, phase))
                stop.wait(0.1)

        def observe():
            while not stop.is_set():
                try:
                    sample = self.snapshot()
                    sample["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    samples.append(sample)
                    with (self.out / f"{phase}-timeline.jsonl").open("a") as output:
                        output.write(json.dumps(sample) + "\n")
                except Exception as error:
                    observer_errors.append(str(error))
                stop.wait(0.5)

        threads = [threading.Thread(target=traffic), threading.Thread(target=observe)]
        for thread in threads:
            thread.start()
        try:
            time.sleep(2)  # establishes pre-action traffic and observation
            action_at = time.monotonic() - started
            (self.out / f"{phase}-command.txt").write_text(self.kubectl(*command))
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if samples and samples[-1]["elapsed_seconds"] >= action_at and done(samples[-1]):
                    break
                time.sleep(0.2)
            else:
                raise TimeoutError(f"{phase} did not converge in 180s")
            time.sleep(3)  # sample traffic after convergence, too
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=30)
        post = [s for s in samples if s["elapsed_seconds"] >= action_at]
        result = {"action_at_elapsed_seconds": round(action_at, 3),
            "requests": len(records), "errors": sum(not r["ok"] for r in records),
            "error_samples": [r for r in records if not r["ok"]][:10],
            "observer_errors": observer_errors,
            "restored_observed_seconds": round(next(s["elapsed_seconds"] for s in post if done(s)) - action_at, 3),
            "minimum_observed_ready_pods": min(sum(p["ready"] for p in s["pods"]) for s in post),
            "sample_interval_seconds": "0.5 plus three kubectl API calls; see timestamps"}
        if replacement_uids is not None:
            appeared = next(s["elapsed_seconds"] for s in post
                if any(p["uid"] not in replacement_uids for p in s["pods"]))
            result["replacement_first_observed_seconds"] = round(appeared - action_at, 3)
        self.summary["scenarios"][phase] = result
        write_json(self.out / "summary.json", self.summary)
        assert not observer_errors, observer_errors
        return result

    def run(self):
        write_json(self.out / "versions.json", {
            "kubectl": json.loads(self.kubectl("version", "-o", "json")),
            "nodes": self.get("nodes"), "fixture_model_version": FIXTURE["model_version"]})
        pods = self.pods()
        assert len(pods) == 1 and self.ready(pods[0]), "Deploy one ready base pod first"
        assert pods[0]["spec"]["containers"][0]["image"] == BASE, "Reapply k8s before a new run"
        self.forward("service/fraudops-api", int(self.local.rsplit(":", 1)[1]), 8080)
        self.forward("pod/fraudops-client", int(self.bridge.rsplit(":", 1)[1]), 8081)
        self.summary["initial_parity"] = self.parity(self.local, "initial-local")
        self.summary["initial_clusterip_parity"] = self.parity(self.bridge, "initial-service")
        write_json(self.out / "initial-state.json", self.snapshot())

        self.kubectl("scale", "deployment/fraudops-api", "--replicas=3")
        self.kubectl("rollout", "status", "deployment/fraudops-api", "--timeout=180s")
        scaled = self.snapshot()
        assert sum(p["ready"] for p in scaled["pods"]) == 3
        ready_endpoints = {e["targetRef"]["name"] for e in scaled["endpoints"]
                           if e.get("conditions", {}).get("ready")}
        assert ready_endpoints == {p["name"] for p in scaled["pods"]}
        write_json(self.out / "scaled-state.json", scaled)
        self.summary["scaled_parity"] = self.parity(self.bridge, "scaled-parity")
        records = [self.predict(self.bridge, "scale") for _ in range(120)]
        counts = self.access_logs("scale")
        self.summary["scenarios"]["scaling"] = {"ready_replicas": 3,
            "requests": len(records), "errors": sum(not r["ok"] for r in records),
            "successful_requests_by_pod": counts, "ready_endpoints": sorted(ready_endpoints)}
        assert all(r["ok"] for r in records), "Scaling prediction failures"
        assert len(counts) == 3 and all(c > 0 for c in counts.values()), counts
        assert sum(counts.values()) == 120, "Missing or duplicated access-log evidence"

        # Delete the pod selected by the direct service port-forward deliberately.
        # That connection is NOT used for recovery/rollout availability evidence.
        victim = pods[0]["metadata"]["name"]
        original_uids = {p["uid"] for p in scaled["pods"]}
        self.summary["deleted_pod"] = victim
        def recovered(s):
            return (sum(p["ready"] for p in s["pods"]) == 3
                and not any(p["name"] == victim for p in s["pods"])
                and s["deployment"].get("availableReplicas", 0) == 3)
        self.action("recovery", ["delete", "pod", victim, "--wait=false"], recovered, original_uids)
        self.summary["post_recovery_parity"] = self.parity(self.bridge, "recovered-parity")
        write_json(self.out / "recovered-state.json", self.snapshot())

        def rolled(s):
            return (len(s["pods"]) == 3 and all(p["ready"] and p["image"] == REVISION for p in s["pods"])
                and s["deployment"].get("updatedReplicas", 0) == 3
                and s["deployment"].get("availableReplicas", 0) == 3)
        self.action("rollout", ["set", "image", "deployment/fraudops-api", f"api={REVISION}"], rolled)
        (self.out / "rollout-status.txt").write_text(self.kubectl(
            "rollout", "status", "deployment/fraudops-api", "--timeout=180s"))
        (self.out / "rollout-history.txt").write_text(self.kubectl("rollout", "history", "deployment/fraudops-api"))
        self.summary["final_parity"] = self.parity(self.bridge, "revision-parity")
        final = self.snapshot()
        write_json(self.out / "final-state.json", final)
        write_json(self.out / "final-pods.json", self.get("pods"))
        base_ids = {c["imageID"] for p in scaled["pods"] for c in p["containers"]}
        final_ids = {c["imageID"] for p in final["pods"] for c in p["containers"]}
        assert base_ids.isdisjoint(final_ids), "Image IDs did not change"
        self.summary["image_ids"] = {"base": sorted(base_ids), "revision": sorted(final_ids)}
        for pod in final["pods"]:
            logs = self.kubectl("logs", pod["name"])
            (self.out / f"{pod['name']}.log").write_text(logs)
            assert "image=exp003-r1" in logs, "Baked revision config was not active"
        self.summary["complete"] = True

    def finish(self):
        self.summary["finished_at"] = utc()
        write_json(self.out / "summary.json", self.summary)
        for resource in ["events", "deployment", "replicasets", "endpointslices"]:
            try:
                write_json(self.out / f"final-{resource}.json", self.get(resource))
            except Exception as error:
                (self.out / f"{resource}-error.txt").write_text(str(error))
        for process, log in self.forwards:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--api-port", type=int, default=18080)
    parser.add_argument("--client-port", type=int, default=18081)
    args = parser.parse_args()
    experiment = Experiment(args)
    try:
        experiment.run()
    except Exception as error:
        experiment.summary["failure"] = str(error)
        raise
    finally:
        experiment.finish()
        print(json.dumps(experiment.summary, indent=2))


if __name__ == "__main__":
    main()
