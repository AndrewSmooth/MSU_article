import argparse
import csv
import json
import os
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.injection_catalog import FAULT_CASES
from run_experiment import execute_experiment


RESULTS_DIR = Path(os.getenv("EXPERIMENT_RESULTS_DIR", "/app/results"))
RUNS_PATH = RESULTS_DIR / "reproducibility_runs.jsonl"
METRICS_PATH = RESULTS_DIR / "reproducibility_metrics.json"
METRICS_CSV_PATH = RESULTS_DIR / "reproducibility_metrics.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--base-seed", type=int, default=1000)
    parser.add_argument("--clear-results", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.repetitions < 1:
        raise SystemExit("Repetition count must be positive")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.clear_results:
        for path in [RUNS_PATH, METRICS_PATH, METRICS_CSV_PATH]:
            path.unlink(missing_ok=True)

    known_cases = [case for case in FAULT_CASES if case["expected_bug"]]
    rows = []
    total_matches = 0
    total_runs = 0
    for fault_case in known_cases:
        matches = 0
        useful = 0
        for repetition in range(1, args.repetitions + 1):
            seed = args.base_seed + repetition - 1
            experiment_id = f"repro-{fault_case['id']}-r{repetition:02d}-s{seed}"
            result = execute_experiment(
                experiment_id=experiment_id,
                point=fault_case["point_id"],
                fault_type=fault_case["fault_type"],
                delay_ms=fault_case["delay_ms"],
                status_code=fault_case["status_code"],
                activation_limit=fault_case["activation_limit"],
                seed=seed,
                strategy="reproducibility",
                metadata={
                    "fault_case_id": fault_case["id"],
                    "repetition": repetition,
                    "expected_bug": fault_case["expected_bug"],
                },
                reset_before=True,
                results_path=RUNS_PATH,
            )
            matched = fault_case["expected_bug"] in result["detected_bugs"]
            matches += int(matched)
            useful += int(result["useful_injection"])
            total_matches += int(matched)
            total_runs += 1

        row = {
            "fault_case_id": fault_case["id"],
            "expected_bug": fault_case["expected_bug"],
            "repetitions": args.repetitions,
            "matching_runs": matches,
            "reproducibility_rate": matches / args.repetitions,
            "useful_injection_rate": useful / args.repetitions,
        }
        rows.append(row)
        print(
            f"{fault_case['id']} ({fault_case['expected_bug']}): "
            f"{matches}/{args.repetitions}"
        )

    metrics = {
        "schema_version": 1,
        "total_runs": total_runs,
        "matching_runs": total_matches,
        "overall_reproducibility_rate": total_matches / total_runs,
        "fault_cases": rows,
    }
    METRICS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with METRICS_CSV_PATH.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error
