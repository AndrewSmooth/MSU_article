import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.injection_catalog import FAULT_CASES, POINTS
from run_experiment import (
    ORDER_BASE_URL,
    SERVICE_URLS,
    execute_experiment,
    request_json,
    request_json_with_status,
    wait_for_service,
    wait_for_verification,
)


JAEGER_QUERY_URL = os.getenv("JAEGER_QUERY_URL", "http://localhost:16686")
RESULTS_DIR = Path(os.getenv("EXPERIMENT_RESULTS_DIR", "/app/results"))
RUNS_PATH = RESULTS_DIR / "campaign_runs.jsonl"
CAMPAIGNS_PATH = RESULTS_DIR / "campaigns.jsonl"
METRICS_PATH = RESULTS_DIR / "strategy_metrics.json"
METRICS_CSV_PATH = RESULTS_DIR / "strategy_metrics.csv"
TRACE_POINTS_PATH = RESULTS_DIR / "trace_points.json"


def append_jsonl(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def clear_faults() -> None:
    for service_url in SERVICE_URLS.values():
        request_json(
            "PUT", service_url, "/internal/fault/mode", {"enabled": True}
        )
        request_json("DELETE", service_url, "/internal/fault")


def collect_control_trace() -> tuple[str, list[str]]:
    for service_url in SERVICE_URLS.values():
        wait_for_service(service_url)
    clear_faults()
    request_json("POST", ORDER_BASE_URL, "/internal/reset")

    order_id = str(uuid4())
    response_status, response_body = request_json_with_status(
        "POST",
        ORDER_BASE_URL,
        "/orders",
        {
            "order_id": order_id,
            "customer_id": "trace-control-user",
            "amount": "100.00",
        },
    )
    if response_status != 200:
        raise RuntimeError(f"Control request failed with HTTP {response_status}")
    verification = wait_for_verification(order_id)
    if not verification["all_passed"]:
        raise RuntimeError("Control request violated invariants")

    trace_id = response_body["trace_id"]
    trace = None
    point_ids: set[str] = set()
    expected_points = {
        point["id"] for point in POINTS if point["used_by_control_scenario"]
    }
    for _ in range(30):
        try:
            response = request_json(
                "GET", JAEGER_QUERY_URL, f"/api/traces/{trace_id}"
            )
            if response.get("data"):
                trace = response["data"][0]
                point_ids = {
                    tag["value"]
                    for span in trace["spans"]
                    for tag in span.get("tags", [])
                    if tag.get("key") == "fault.point.id"
                }
                if expected_points.issubset(point_ids):
                    break
        except urllib.error.HTTPError:
            pass
        time.sleep(0.5)
    if trace is None:
        raise RuntimeError(f"Trace {trace_id} was not exported to Jaeger")

    missing_points = expected_points - point_ids
    if missing_points:
        raise RuntimeError(
            "Control trace is incomplete; missing points: "
            + ", ".join(sorted(missing_points))
        )

    observed = sorted(point_ids)
    TRACE_POINTS_PATH.write_text(
        json.dumps(
            {"trace_id": trace_id, "observed_points": observed},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return trace_id, observed


def candidates_for(strategy: str, observed_points: set[str]) -> list[dict]:
    if strategy == "random":
        return list(FAULT_CASES)
    if strategy == "trace":
        return [
            fault_case
            for fault_case in FAULT_CASES
            if fault_case["point_id"] in observed_points
        ]
    raise ValueError(f"Unknown strategy: {strategy}")


def run_campaign(
    *,
    strategy: str,
    campaign_number: int,
    seed: int,
    budget: int,
    observed_points: set[str],
) -> dict:
    candidates = candidates_for(strategy, observed_points)
    if budget > len(candidates):
        raise ValueError(
            f"Budget {budget} exceeds {len(candidates)} candidates for {strategy}"
        )

    selected = random.Random(seed).sample(candidates, budget)
    campaign_id = f"{strategy}-c{campaign_number:03d}-s{seed}"
    campaign_started = time.perf_counter()
    first_bug_run = None
    time_to_first_bug_ms = None
    experiments_to_bug: dict[str, int] = {}
    unique_bugs: set[str] = set()
    useful_count = 0

    for run_number, fault_case in enumerate(selected, start=1):
        experiment_id = f"{campaign_id}-r{run_number:02d}-{fault_case['id']}"
        result = execute_experiment(
            experiment_id=experiment_id,
            point=fault_case["point_id"],
            fault_type=fault_case["fault_type"],
            delay_ms=fault_case["delay_ms"],
            status_code=fault_case["status_code"],
            activation_limit=fault_case["activation_limit"],
            seed=seed,
            strategy=strategy,
            metadata={
                "campaign_id": campaign_id,
                "campaign_number": campaign_number,
                "run_number": run_number,
                "fault_case_id": fault_case["id"],
                "expected_bug": fault_case["expected_bug"],
            },
            reset_before=True,
            results_path=RUNS_PATH,
        )
        if result["useful_injection"]:
            useful_count += 1
        if result["detected_bugs"] and first_bug_run is None:
            first_bug_run = run_number
            time_to_first_bug_ms = round(
                (time.perf_counter() - campaign_started) * 1000
            )
        for bug in result["detected_bugs"]:
            unique_bugs.add(bug)
            experiments_to_bug.setdefault(bug, run_number)

    summary = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "campaign_number": campaign_number,
        "strategy": strategy,
        "seed": seed,
        "budget": budget,
        "candidate_count": len(candidates),
        "unique_bugs": sorted(unique_bugs),
        "unique_bug_count": len(unique_bugs),
        "experiments_to_bug": experiments_to_bug,
        "first_bug_run": first_bug_run,
        "time_to_first_bug_ms": time_to_first_bug_ms,
        "useful_injection_count": useful_count,
        "useful_injection_rate": useful_count / budget,
        "campaign_wall_time_ms": round(
            (time.perf_counter() - campaign_started) * 1000
        ),
    }
    append_jsonl(CAMPAIGNS_PATH, summary)
    return summary


def aggregate_metrics(campaigns: list[dict], control_trace_id: str) -> dict:
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for campaign in campaigns:
        by_strategy[campaign["strategy"]].append(campaign)

    strategy_metrics = {}
    for strategy, rows in sorted(by_strategy.items()):
        first_bug_runs = [
            row["first_bug_run"] for row in rows if row["first_bug_run"] is not None
        ]
        first_bug_times = [
            row["time_to_first_bug_ms"]
            for row in rows
            if row["time_to_first_bug_ms"] is not None
        ]
        bug_discovery_rate = {}
        for bug in ["B1", "B2", "B3", "B4"]:
            bug_discovery_rate[bug] = sum(
                bug in row["unique_bugs"] for row in rows
            ) / len(rows)
        strategy_metrics[strategy] = {
            "campaigns": len(rows),
            "mean_unique_bugs": statistics.fmean(
                row["unique_bug_count"] for row in rows
            ),
            "median_unique_bugs": statistics.median(
                row["unique_bug_count"] for row in rows
            ),
            "mean_useful_injection_rate": statistics.fmean(
                row["useful_injection_rate"] for row in rows
            ),
            "median_first_bug_run": (
                statistics.median(first_bug_runs) if first_bug_runs else None
            ),
            "median_time_to_first_bug_ms": (
                statistics.median(first_bug_times) if first_bug_times else None
            ),
            "campaigns_with_a_bug_rate": len(first_bug_runs) / len(rows),
            "bug_discovery_rate": bug_discovery_rate,
        }

    return {
        "schema_version": 1,
        "control_trace_id": control_trace_id,
        "fault_case_count": len(FAULT_CASES),
        "strategies": strategy_metrics,
        "paired_comparison": {
            "unique_bug_count": paired_sign_test(
                campaigns, "unique_bug_count", higher_is_better=True
            ),
            "useful_injection_rate": paired_sign_test(
                campaigns, "useful_injection_rate", higher_is_better=True
            ),
            "first_bug_run": paired_sign_test(
                campaigns, "first_bug_run", higher_is_better=False
            ),
            "time_to_first_bug_ms": paired_sign_test(
                campaigns, "time_to_first_bug_ms", higher_is_better=False
            ),
        },
    }


def paired_sign_test(
    campaigns: list[dict], metric: str, *, higher_is_better: bool
) -> dict:
    paired: dict[int, dict[str, float]] = defaultdict(dict)
    for campaign in campaigns:
        value = campaign[metric]
        if value is not None:
            paired[campaign["campaign_number"]][campaign["strategy"]] = value

    improvements = 0
    regressions = 0
    ties = 0
    for values in paired.values():
        if {"random", "trace"} - values.keys():
            continue
        difference = values["trace"] - values["random"]
        if not higher_is_better:
            difference = -difference
        if difference > 0:
            improvements += 1
        elif difference < 0:
            regressions += 1
        else:
            ties += 1

    non_ties = improvements + regressions
    p_value = (
        sum(math.comb(non_ties, k) for k in range(improvements, non_ties + 1))
        / (2**non_ties)
        if non_ties
        else 1.0
    )
    return {
        "paired_campaigns": improvements + regressions + ties,
        "trace_better": improvements,
        "random_better": regressions,
        "ties": ties,
        "one_sided_sign_test_p": p_value,
    }


def write_metrics(metrics: dict) -> None:
    METRICS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with METRICS_CSV_PATH.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "strategy",
                "campaigns",
                "mean_unique_bugs",
                "median_unique_bugs",
                "mean_useful_injection_rate",
                "median_first_bug_run",
                "median_time_to_first_bug_ms",
                "campaigns_with_a_bug_rate",
                "B1_discovery_rate",
                "B2_discovery_rate",
                "B3_discovery_rate",
                "B4_discovery_rate",
            ]
        )
        for strategy, values in metrics["strategies"].items():
            writer.writerow(
                [
                    strategy,
                    values["campaigns"],
                    values["mean_unique_bugs"],
                    values["median_unique_bugs"],
                    values["mean_useful_injection_rate"],
                    values["median_first_bug_run"],
                    values["median_time_to_first_bug_ms"],
                    values["campaigns_with_a_bug_rate"],
                    values["bug_discovery_rate"]["B1"],
                    values["bug_discovery_rate"]["B2"],
                    values["bug_discovery_rate"]["B3"],
                    values["bug_discovery_rate"]["B4"],
                ]
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaigns", type=int, default=10)
    parser.add_argument("--budget", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=1)
    parser.add_argument(
        "--strategy", choices=["both", "random", "trace"], default="both"
    )
    parser.add_argument("--clear-results", action="store_true")
    parser.add_argument("--analyze-existing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.campaigns < 1 or args.budget < 1:
        raise SystemExit("Campaign count and budget must be positive")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.analyze_existing:
        if not CAMPAIGNS_PATH.exists() or not TRACE_POINTS_PATH.exists():
            raise SystemExit("Existing campaign or trace data was not found")
        campaigns = [
            json.loads(line)
            for line in CAMPAIGNS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        trace_data = json.loads(TRACE_POINTS_PATH.read_text(encoding="utf-8"))
        metrics = aggregate_metrics(campaigns, trace_data["trace_id"])
        write_metrics(metrics)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return
    if args.clear_results:
        for path in [RUNS_PATH, CAMPAIGNS_PATH, METRICS_PATH, METRICS_CSV_PATH]:
            path.unlink(missing_ok=True)

    control_trace_id, observed = collect_control_trace()
    strategies = ["random", "trace"] if args.strategy == "both" else [args.strategy]
    campaigns = []
    for campaign_number in range(1, args.campaigns + 1):
        seed = args.base_seed + campaign_number - 1
        ordered_strategies = (
            strategies if campaign_number % 2 else list(reversed(strategies))
        )
        for strategy in ordered_strategies:
            summary = run_campaign(
                strategy=strategy,
                campaign_number=campaign_number,
                seed=seed,
                budget=args.budget,
                observed_points=set(observed),
            )
            campaigns.append(summary)
            print(
                f"{summary['campaign_id']}: "
                f"bugs={summary['unique_bug_count']}, "
                f"useful={summary['useful_injection_rate']:.3f}"
            )

    metrics = aggregate_metrics(campaigns, control_trace_id)
    write_metrics(metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error
