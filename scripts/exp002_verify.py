"""EXP-002 runtime acceptance on a Docker Engine host. Python stdlib only.

Builds the image (re-running the Maven test stage), checks image contents and
user, then for each resource scenario starts a fresh container and records:
startup/readiness/health timing, parity against src/test/resources/parity-cases.json,
repeated load trials, docker stats samples, cgroup memory peak, health-check
probe durations, and restart/OOM/exit state. Finishes with a SIGTERM test while
requests are in flight.

Evidence is written to ignored artifacts/EXP-002/run-<UTC>/. Review it, then
promote non-sensitive files to experiments/EXP-002/evidence/.

A scenario that fails to start, OOMs or goes unhealthy is recorded as a result;
limits are never raised automatically.

    python scripts/exp002_verify.py
    python scripts/exp002_verify.py --skip-build --scenarios baseline,stress
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "fraudops-exp002-verify"
SCENARIOS = {
    "baseline": {"cpus": "2", "memory": "1g"},
    "constrained": {"cpus": "1", "memory": "512m"},
    "stress": {"cpus": "0.5", "memory": "256m"},
}
STOP_TIMEOUT_S = 35  # Spring 25 s shutdown phase + 10 s margin (README)


class Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.commands = (out / "commands.txt").open("a", encoding="utf-8")

    def run(self, args: list[str], check: bool = True, timeout: float | None = 600,
            log_file: str | None = None) -> subprocess.CompletedProcess:
        self.commands.write(f"{utc_now()} $ {' '.join(args)}\n")
        self.commands.flush()
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        if log_file:
            self.write(log_file, proc.stdout + proc.stderr)
        if check and proc.returncode != 0:
            raise RuntimeError(f"{' '.join(args)} failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
        return proc

    def write(self, name: str, content: str | dict | list) -> None:
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, default=str)
        path.write_text(content, encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_docker_time(value: str) -> datetime | None:
    # Docker emits RFC 3339 with nanoseconds; datetime accepts at most microseconds.
    if not value or value.startswith("0001-"):
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)", value)
    if not m:
        return None
    frac = ((m.group(2) or ".")[1:7]).ljust(6, "0")
    tz = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")


def http(method: str, url: str, body: dict | None = None, timeout: float = 10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return None, type(e).__name__


def inspect(rec: Recorder, name: str) -> dict | None:
    proc = rec.run(["docker", "inspect", name], check=False, timeout=30)
    return json.loads(proc.stdout)[0] if proc.returncode == 0 else None


def state_summary(info: dict | None) -> dict:
    if not info:
        return {"present": False}
    st = info["State"]
    health = st.get("Health") or {}
    probes = []
    for entry in health.get("Log", []):
        start, end = parse_docker_time(entry.get("Start", "")), parse_docker_time(entry.get("End", ""))
        probes.append({"exit_code": entry.get("ExitCode"),
                       "duration_ms": round((end - start).total_seconds() * 1000, 1) if start and end else None})
    durations = [p["duration_ms"] for p in probes if p["duration_ms"] is not None]
    return {
        "present": True,
        "status": st.get("Status"),
        "running": st.get("Running"),
        "oom_killed": st.get("OOMKilled"),
        "exit_code": st.get("ExitCode"),
        "error": st.get("Error"),
        "restart_count": info.get("RestartCount"),
        "health_status": health.get("Status"),
        "health_failing_streak": health.get("FailingStreak"),
        "health_probes_last5": probes,
        "health_probe_max_ms": max(durations) if durations else None,
    }


class StatsSampler(threading.Thread):
    """Samples `docker stats --no-stream` for one container until stopped."""

    def __init__(self, rec: Recorder, name: str, interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.rec, self.name, self.interval = rec, name, interval
        self.samples: list[dict] = []
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            proc = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", self.name],
                                  capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                sample = json.loads(proc.stdout.strip().splitlines()[0])
                sample["t"] = utc_now()
                self.samples.append(sample)
            self.stop_event.wait(self.interval)

    def stop(self) -> list[dict]:
        self.stop_event.set()
        self.join(timeout=35)
        return self.samples


def mem_to_bytes(text: str) -> float | None:
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", text.strip())
    if not m:
        return None
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
             "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9}
    return float(m.group(1)) * units.get(m.group(2), 1)


def stats_summary(samples: list[dict]) -> dict:
    mem = [mem_to_bytes(s.get("MemUsage", "").split("/")[0]) for s in samples]
    mem = [m for m in mem if m is not None]
    cpu = [float(s["CPUPerc"].rstrip("%")) for s in samples if s.get("CPUPerc", "").rstrip("%")]
    return {"samples": len(samples),
            "mem_max_mib": round(max(mem) / 1024**2, 1) if mem else None,
            "cpu_max_percent": max(cpu) if cpu else None,
            "cpu_median_percent": statistics.median(cpu) if cpu else None}


def cgroup_memory_peak(rec: Recorder, name: str) -> int | None:
    for path in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        proc = rec.run(["docker", "exec", name, "cat", path], check=False, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            return int(proc.stdout.strip())
    return None


def start_container(rec: Recorder, image: str, port: int, cpus: str, memory: str) -> None:
    rec.run(["docker", "rm", "-f", CONTAINER], check=False)
    # --memory-swap == --memory disables swap, so the limit means an OOM kill rather than swapping.
    rec.run(["docker", "run", "-d", "--name", CONTAINER, "--restart=no",
             f"--cpus={cpus}", f"--memory={memory}", f"--memory-swap={memory}",
             "-p", f"{port}:8080", image])


def wait_ready(rec: Recorder, base: str, timeout: float = 180) -> dict:
    t0 = time.monotonic()
    result: dict = {"ready_s": None, "healthy_s": None, "outcome": "timeout"}
    while time.monotonic() - t0 < timeout:
        info = inspect(rec, CONTAINER)
        st = state_summary(info)
        if not st.get("running"):
            result["outcome"] = "exited"
            return result
        if result["ready_s"] is None:
            status, body = http("GET", f"{base}/actuator/health/readiness", timeout=3)
            if status == 200 and isinstance(body, dict) and body.get("status") == "UP":
                result["ready_s"] = round(time.monotonic() - t0, 2)
        if st.get("health_status") == "healthy":
            result["healthy_s"] = round(time.monotonic() - t0, 2)
        if st.get("health_status") == "unhealthy":
            result["outcome"] = "unhealthy"
            return result
        if result["ready_s"] is not None and result["healthy_s"] is not None:
            result["outcome"] = "ready"
            return result
        time.sleep(0.5)
    return result


def check_parity(base: str) -> dict:
    fixture = json.loads((ROOT / "src/test/resources/parity-cases.json").read_text(encoding="utf-8"))
    atol, rtol = fixture["absolute_tolerance"], fixture["relative_tolerance"]
    cases = []
    for case in fixture["cases"]:
        status, body = http("POST", f"{base}/api/v1/predict", case["features"])
        actual = body.get("fraud_probability") if isinstance(body, dict) else None
        expected = case["fraud_probability"]
        ok = (status == 200 and actual is not None
              and abs(actual - expected) <= atol + rtol * abs(expected)
              and body.get("decision") == case["expected_decision"]
              and body.get("model_version") == fixture["model_version"])
        cases.append({"name": case["name"], "http_status": status, "expected": expected,
                      "actual": actual, "abs_diff": None if actual is None else abs(actual - expected),
                      "decision": body.get("decision") if isinstance(body, dict) else None,
                      "expected_decision": case["expected_decision"], "passed": ok})
    return {"passed": sum(c["passed"] for c in cases), "total": len(cases), "cases": cases}


def run_load(rec: Recorder, base: str, args) -> dict:
    proc = rec.run([sys.executable, str(ROOT / "scripts/load_test.py"), "--base-url", base,
                    "--requests", str(args.requests), "--concurrency", str(args.concurrency),
                    "--warmup-requests", str(args.warmup_requests)], check=False, timeout=900)
    if proc.returncode != 0:
        return {"failed": True, "stderr_tail": proc.stderr[-2000:]}
    return json.loads(proc.stdout)


def stop_and_collect(rec: Recorder, prefix: str) -> dict:
    info = inspect(rec, CONTAINER)
    rec.write(f"{prefix}/inspect.json", info or {})
    rec.run(["docker", "logs", "--timestamps", CONTAINER], check=False, log_file=f"{prefix}/container.log")
    state = state_summary(info)
    rec.run(["docker", "stop", "--time", str(STOP_TIMEOUT_S), CONTAINER], check=False, timeout=STOP_TIMEOUT_S + 30)
    rec.run(["docker", "rm", "-f", CONTAINER], check=False)
    return state


def run_scenario(rec: Recorder, image: str, name: str, spec: dict, args) -> dict:
    base = f"http://localhost:{args.port}"
    print(f"[{name}] cpus={spec['cpus']} memory={spec['memory']}", flush=True)
    start_container(rec, image, args.port, spec["cpus"], spec["memory"])
    sampler = StatsSampler(rec, CONTAINER)
    sampler.start()
    result: dict = {"scenario": name, **spec, "startup": wait_ready(rec, base)}
    if result["startup"]["outcome"] == "ready":
        result["parity"] = check_parity(base)
        result["load_trials"] = []
        for i in range(args.trials):
            if not state_summary(inspect(rec, CONTAINER)).get("running"):
                break
            print(f"[{name}] load trial {i + 1}/{args.trials}", flush=True)
            result["load_trials"].append(run_load(rec, base, args))
        result["cgroup_memory_peak_bytes"] = cgroup_memory_peak(rec, CONTAINER)
        # Let a few more health probes run after load, to see whether they still pass.
        time.sleep(25)
    result["stats"] = stats_summary(stats := sampler.stop())
    rec.write(f"{name}/docker-stats.json", stats)
    result["final_state"] = stop_and_collect(rec, name)
    rec.write(f"{name}/result.json", result)
    return result


def sigterm_test(rec: Recorder, image: str, args) -> dict:
    """Stop the container while 16 clients send requests; classify every request."""
    base = f"http://localhost:{args.port}"
    spec = SCENARIOS["baseline"]
    print("[sigterm] starting", flush=True)
    start_container(rec, image, args.port, spec["cpus"], spec["memory"])
    startup = wait_ready(rec, base)
    if startup["outcome"] != "ready":
        return {"startup": startup, "final_state": stop_and_collect(rec, "sigterm")}
    run_load(rec, base, argparse.Namespace(requests=200, concurrency=16, warmup_requests=0))

    fixture = json.loads((ROOT / "src/test/resources/parity-cases.json").read_text(encoding="utf-8"))
    payload = fixture["cases"][0]["features"]
    records: list[tuple[float, float, object]] = []
    lock, done = threading.Lock(), threading.Event()

    def worker() -> None:
        while not done.is_set():
            t_start = time.monotonic()
            status, body = http("POST", f"{base}/api/v1/predict", payload, timeout=40)
            outcome = status if status is not None else body  # error class name when no HTTP status
            with lock:
                records.append((t_start, time.monotonic(), outcome))
            if outcome != 200:
                time.sleep(0.05)  # avoid a hot loop of refused connections

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    for t in threads:
        t.start()
    time.sleep(5)
    t_stop = time.monotonic()
    stop = rec.run(["docker", "stop", "--time", str(STOP_TIMEOUT_S), CONTAINER], check=False,
                   timeout=STOP_TIMEOUT_S + 30)
    stop_duration = round(time.monotonic() - t_stop, 2)
    time.sleep(1)
    done.set()
    for t in threads:
        t.join(timeout=45)

    def bucket(rows):
        out: dict = {"total": len(rows)}
        for _, _, s in rows:
            out[str(s)] = out.get(str(s), 0) + 1
        return out

    before = [r for r in records if r[1] < t_stop]
    in_flight = [r for r in records if r[0] < t_stop <= r[1]]
    after = [r for r in records if r[0] >= t_stop]
    info = inspect(rec, CONTAINER)
    rec.write("sigterm/inspect.json", info or {})
    logs = rec.run(["docker", "logs", "--timestamps", CONTAINER], check=False, log_file="sigterm/container.log")
    log_text = logs.stdout + logs.stderr
    rec.run(["docker", "rm", "-f", CONTAINER], check=False)
    result = {
        "startup": startup,
        "docker_stop_returncode": stop.returncode,
        "stop_duration_s": stop_duration,
        "killed_after_timeout": stop_duration >= STOP_TIMEOUT_S,
        "completed_before_sigterm": bucket(before),
        "in_flight_at_sigterm": bucket(in_flight),
        "started_after_sigterm": bucket(after),
        "log_commencing_graceful_shutdown": "Commencing graceful shutdown" in log_text,
        "log_graceful_shutdown_complete": "Graceful shutdown complete" in log_text,
        "log_onnx_session_closed": bool(re.search(r"(?i)onnx.*(clos|shut)", log_text)),
        "final_state": state_summary(info),
    }
    rec.write("sigterm/result.json", result)
    return result


def build_and_inspect(rec: Recorder, image: str, args) -> dict:
    result: dict = {"image": image}
    if not args.skip_build:
        t0 = time.monotonic()
        cmd = ["docker", "build", "--progress=plain", "-t", image,
               "--build-arg", "APP_VERSION=1.0.0-exp001", "--build-arg", "MODEL_VERSION=1.0.0-exp000"]
        if not args.allow_cached_tests:
            cmd += ["--no-cache-filter", "build"]  # force the Maven test stage to rerun
        proc = rec.run(cmd + [str(ROOT)], check=False, timeout=1800, log_file="build/build.log")
        result["build_seconds"] = round(time.monotonic() - t0, 1)
        result["build_returncode"] = proc.returncode
        tests = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)\s*$",
                           proc.stdout + proc.stderr, re.M)
        if tests:
            result["maven_tests"] = dict(zip(["run", "failures", "errors", "skipped"], map(int, tests[-1])))
        if proc.returncode != 0:
            raise RuntimeError("Image build failed; see build/build.log")
    info = inspect(rec, image)
    rec.write("build/image-inspect.json", info or {})
    cfg = info["Config"]
    result.update({"id": info["Id"], "size_mib": round(info["Size"] / 1024**2, 1), "user": cfg.get("User"),
                   "labels": cfg.get("Labels"), "entrypoint": cfg.get("Entrypoint"),
                   "stop_signal": cfg.get("StopSignal"), "healthcheck": cfg.get("Healthcheck")})
    script = ("id; ls -ln /app /app/model /opt/healthcheck; sha256sum /app/model/*; "
              "(touch /app/model/probe 2>/dev/null && echo APP_WRITABLE) || echo APP_READ_ONLY; "
              "java -version 2>&1")
    proc = rec.run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script],
                   check=False, log_file="build/image-contents.txt")
    fixture = json.loads((ROOT / "src/test/resources/parity-cases.json").read_text(encoding="utf-8"))
    text = proc.stdout
    result["contents"] = {
        "uid_10001": "uid=10001" in text,
        "app_read_only": "APP_READ_ONLY" in text,
        "model_sha256_matches": fixture["model_sha256"] in text,
        "metadata_sha256_matches": fixture["metadata_sha256"] in text,
        "runtime_java": next((l for l in text.splitlines() if "Runtime Environment" in l), None),
    }
    if args.scan and shutil.which("trivy"):
        rec.run(["trivy", "image", "--format", "json", "--output", str(rec.out / "build/trivy.json"), image],
                check=False, timeout=1800)
        result["scan"] = "trivy: build/trivy.json"
    elif args.scan:
        result["scan"] = "requested, but trivy not on PATH"
    rec.write("build/result.json", result)
    return result


def fmt(v, suffix=""):
    return "n/a" if v is None else f"{v}{suffix}"


def summary_markdown(build: dict, scenarios: list[dict], sig: dict | None) -> str:
    lines = ["## Build and image", "", "| Check | Result |", "| --- | --- |"]
    t = build.get("maven_tests")
    lines += [
        f"| Image build | {'passed' if build.get('build_returncode', 0) == 0 else 'failed'}"
        f" in {fmt(build.get('build_seconds'), ' s')}; image {build['id'][:19]}, {build['size_mib']} MiB |",
        f"| Maven tests in build stage | {t['run']} run, {t['failures']} failures, {t['errors']} errors, "
        f"{t['skipped']} skipped |" if t else "| Maven tests in build stage | not parsed (cached or skipped build) |",
        f"| Runtime user / app files | UID 10001: {build['contents']['uid_10001']}; "
        f"read-only: {build['contents']['app_read_only']} |",
        f"| Model / metadata SHA-256 match fixture | {build['contents']['model_sha256_matches']} / "
        f"{build['contents']['metadata_sha256_matches']} |",
        f"| Runtime JRE | {fmt(build['contents']['runtime_java'])} |",
        f"| Vulnerability scan | {build.get('scan', 'not run')} |",
        "", "## Resource scenarios", "",
        "| Scenario | Ready | Healthy | Parity | Throughput median | p99 median | Errors | "
        "Peak mem (cgroup) | Max health probe | OOM | Restarts | Final health |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for s in scenarios:
        trials = [x for x in s.get("load_trials", []) if not x.get("failed")]
        thr = statistics.median(x["throughput_requests_per_second"] for x in trials) if trials else None
        p99 = statistics.median(x["p99_latency_ms"] for x in trials) if trials else None
        errs = sum(x["error_count"] for x in trials) if trials else None
        failed = len(s.get("load_trials", [])) - len(trials)
        peak = s.get("cgroup_memory_peak_bytes")
        fs, st = s["final_state"], s["startup"]
        par = s.get("parity")
        parity = f"{par['passed']}/{par['total']}" if par else st["outcome"]
        lines.append(
            f"| {s['scenario']} ({s['cpus']} CPU / {s['memory']}) | {fmt(st['ready_s'], ' s')} | "
            f"{fmt(st['healthy_s'], ' s')} | {parity} | "
            f"{fmt(thr, ' req/s')} | {fmt(p99, ' ms')} | {fmt(errs)}{f' ({failed} trials failed)' if failed else ''} | "
            f"{fmt(round(peak / 1024**2, 1) if peak else None, ' MiB')} | {fmt(fs.get('health_probe_max_ms'), ' ms')} | "
            f"{fs.get('oom_killed')} | {fs.get('restart_count')} | {fs.get('health_status')} |")
    if sig:
        lines += ["", "## SIGTERM under load", "", "| Check | Result |", "| --- | --- |"]
        if "in_flight_at_sigterm" in sig:
            fl = sig["in_flight_at_sigterm"]
            lines += [
                f"| `docker stop --time {STOP_TIMEOUT_S}` duration | {sig['stop_duration_s']} s "
                f"(SIGKILL needed: {sig['killed_after_timeout']}) |",
                f"| Exit code / OOM | {sig['final_state'].get('exit_code')} / {sig['final_state'].get('oom_killed')} |",
                f"| Requests in flight at SIGTERM | {fl['total']} total; {fl.get('200', 0)} returned 200 |",
                f"| In-flight outcomes | {json.dumps(fl)} |",
                f"| Requests started after SIGTERM | {json.dumps(sig['started_after_sigterm'])} |",
                f"| Graceful shutdown logged (commencing / complete) | {sig['log_commencing_graceful_shutdown']} / "
                f"{sig['log_graceful_shutdown_complete']} |",
            ]
        else:
            lines.append(f"| Startup | {sig['startup']['outcome']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", default="fraudops-api:exp002")
    p.add_argument("--scenarios", default="baseline,constrained,stress")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--requests", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--warmup-requests", type=int, default=200)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--skip-build", action="store_true", help="use an existing local image")
    p.add_argument("--allow-cached-tests", action="store_true", help="do not force the Maven stage to rerun")
    p.add_argument("--skip-sigterm", action="store_true")
    p.add_argument("--scan", action="store_true", help="run trivy if it is on PATH")
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        p.error(f"unknown scenarios: {unknown}; choose from {list(SCENARIOS)}")
    if not shutil.which("docker"):
        sys.exit("docker CLI not on PATH")
    out = args.output or ROOT / "artifacts/EXP-002" / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    rec = Recorder(out)
    rec.write("environment.txt", rec.run(["docker", "version"], check=False).stdout
              + rec.run(["docker", "info"], check=False).stdout)
    print(f"Evidence directory: {out}", flush=True)

    build = build_and_inspect(rec, args.image, args)
    scenarios = []
    try:
        for name in names:
            scenarios.append(run_scenario(rec, args.image, name, SCENARIOS[name], args))
        sig = None if args.skip_sigterm else sigterm_test(rec, args.image, args)
    finally:
        rec.run(["docker", "rm", "-f", CONTAINER], check=False)
    rec.write("summary.json", {"started": out.name, "build": build, "scenarios": scenarios, "sigterm": sig})
    md = summary_markdown(build, scenarios, sig)
    rec.write("summary.md", md)
    print("\n" + md)


if __name__ == "__main__":
    main()
