import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_experiment import (
    ORDER_BASE_URL,
    SERVICE_URLS,
    request_json,
    request_json_with_status,
    wait_for_service,
    wait_for_verification,
)


RESULTS_DIR = Path(os.getenv("EXPERIMENT_RESULTS_DIR", "/app/results"))
RUNS_PATH = RESULTS_DIR / "overhead_runs.jsonl"
METRICS_PATH = RESULTS_DIR / "overhead_metrics.json"
METRICS_CSV_PATH = RESULTS_DIR / "overhead_metrics.csv"


def append_jsonl(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def set_injection_mode(enabled: bool) -> None:
    for service_url in SERVICE_URLS.values():
        request_json(
            "PUT", service_url, "/internal/fault/mode", {"enabled": enabled}
        )
        request_json("DELETE", service_url, "/internal/fault")


def run_control(mode: str, run_number: int, record: bool) -> dict:
    enabled = mode == "instrumented"
    set_injection_mode(enabled)
    request_json("POST", ORDER_BASE_URL, "/internal/reset")
    order_id = str(uuid4())
    started = time.perf_counter()
    status, response = request_json_with_status(
        "POST",
        ORDER_BASE_URL,
        "/orders",
        {
            "order_id": order_id,
            "customer_id": f"overhead-{mode}",
            "amount": "100.00",
        },
    )
    verification = wait_for_verification(order_id)
    client_duration_ms = (time.perf_counter() - started) * 1000
    if status != 200 or not verification["all_passed"]:
        raise RuntimeError(f"Overhead control failed in mode {mode}: {response}")
    result = {
        "schema_version": 1,
        "mode": mode,
        "run_number": run_number,
        "client_duration_ms": round(client_duration_ms, 3),
        "service_duration_ms": verification["duration_ms"],
        "trace_id": response["trace_id"],
    }
    if record:
        append_jsonl(RUNS_PATH, result)
    return result


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def trimmed_mean(values: list[float], fraction: float = 0.05) -> float:
    ordered = sorted(values)
    trim = int(len(ordered) * fraction)
    selected = ordered[trim:-trim] if trim else ordered
    return statistics.fmean(selected)


def summarize(rows: list[dict], mode: str) -> dict:
    selected = [row for row in rows if row["mode"] == mode]
    client = [row["client_duration_ms"] for row in selected]
    service = [row["service_duration_ms"] for row in selected]
    return {
        "runs": len(selected),
        "client_mean_ms": statistics.fmean(client),
        "client_trimmed_mean_ms": trimmed_mean(client),
        "client_median_ms": statistics.median(client),
        "client_p95_ms": percentile(client, 0.95),
        "service_mean_ms": statistics.fmean(service),
        "service_trimmed_mean_ms": trimmed_mean(service),
        "service_median_ms": statistics.median(service),
        "service_p95_ms": percentile(service, 0.95),
    }


def paired_difference(rows: list[dict], field: str) -> dict:
    by_run: dict[int, dict[str, float]] = {}
    for row in rows:
        by_run.setdefault(row["run_number"], {})[row["mode"]] = row[field]
    differences = [
        values["instrumented"] - values["bypass"]
        for values in by_run.values()
        if {"instrumented", "bypass"}.issubset(values)
    ]
    mean_difference = statistics.fmean(differences)
    standard_error = statistics.stdev(differences) / (len(differences) ** 0.5)
    margin = 1.96 * standard_error
    return {
        "pairs": len(differences),
        "mean_difference_ms": mean_difference,
        "median_difference_ms": statistics.median(differences),
        "mean_difference_ci95_low_ms": mean_difference - margin,
        "mean_difference_ci95_high_ms": mean_difference + margin,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--clear-results", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.runs < 2 or args.warmup < 0:
        raise SystemExit("At least two measured runs are required")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.clear_results:
        for path in [RUNS_PATH, METRICS_PATH, METRICS_CSV_PATH]:
            path.unlink(missing_ok=True)
    for service_url in SERVICE_URLS.values():
        wait_for_service(service_url)

    rows = []
    try:
        for warmup_number in range(1, args.warmup + 1):
            for mode in ["bypass", "instrumented"]:
                run_control(mode, warmup_number, record=False)

        for run_number in range(1, args.runs + 1):
            modes = (
                ["bypass", "instrumented"]
                if run_number % 2
                else ["instrumented", "bypass"]
            )
            for mode in modes:
                rows.append(run_control(mode, run_number, record=True))
    finally:
        set_injection_mode(True)

    baseline = summarize(rows, "bypass")
    instrumented = summarize(rows, "instrumented")
    client_paired = paired_difference(rows, "client_duration_ms")
    service_paired = paired_difference(rows, "service_duration_ms")
    client_mean_overhead = (
        (instrumented["client_mean_ms"] - baseline["client_mean_ms"])
        / baseline["client_mean_ms"]
        * 100
    )
    service_mean_overhead = (
        (instrumented["service_mean_ms"] - baseline["service_mean_ms"])
        / baseline["service_mean_ms"]
        * 100
    )
    client_trimmed_overhead = (
        (
            instrumented["client_trimmed_mean_ms"]
            - baseline["client_trimmed_mean_ms"]
        )
        / baseline["client_trimmed_mean_ms"]
        * 100
    )
    service_trimmed_overhead = (
        (
            instrumented["service_trimmed_mean_ms"]
            - baseline["service_trimmed_mean_ms"]
        )
        / baseline["service_trimmed_mean_ms"]
        * 100
    )
    client_overhead = (
        (instrumented["client_median_ms"] - baseline["client_median_ms"])
        / baseline["client_median_ms"]
        * 100
    )
    service_overhead = (
        (instrumented["service_median_ms"] - baseline["service_median_ms"])
        / baseline["service_median_ms"]
        * 100
    )
    metrics = {
        "schema_version": 1,
        "measured_runs_per_mode": args.runs,
        "warmup_runs_per_mode": args.warmup,
        "baseline": baseline,
        "instrumented": instrumented,
        "client_paired_difference": client_paired,
        "service_paired_difference": service_paired,
        "client_mean_overhead_percent": client_mean_overhead,
        "service_mean_overhead_percent": service_mean_overhead,
        "client_trimmed_mean_overhead_percent": client_trimmed_overhead,
        "service_trimmed_mean_overhead_percent": service_trimmed_overhead,
        "client_median_overhead_percent": client_overhead,
        "service_median_overhead_percent": service_overhead,
    }
    METRICS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with METRICS_CSV_PATH.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["metric", "value"])
        writer.writerow(["client_mean_overhead_percent", client_mean_overhead])
        writer.writerow(["service_mean_overhead_percent", service_mean_overhead])
        writer.writerow(
            ["client_trimmed_mean_overhead_percent", client_trimmed_overhead]
        )
        writer.writerow(
            ["service_trimmed_mean_overhead_percent", service_trimmed_overhead]
        )
        writer.writerow(["client_median_overhead_percent", client_overhead])
        writer.writerow(["service_median_overhead_percent", service_overhead])
        for mode, values in [("baseline", baseline), ("instrumented", instrumented)]:
            for key, value in values.items():
                writer.writerow([f"{mode}_{key}", value])
        for scope, values in [
            ("client_paired", client_paired),
            ("service_paired", service_paired),
        ]:
            for key, value in values.items():
                writer.writerow([f"{scope}_{key}", value])
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error
