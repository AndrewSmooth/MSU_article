import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ORDER_BASE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8000")
PAYMENT_BASE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8001")
RESULTS_PATH = Path(
    os.getenv("EXPERIMENT_RESULTS_PATH", "/app/results/experiments.jsonl")
)


def request_json(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
    timeout: float = 5,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def wait_for_service(base_url: str) -> None:
    last_error: urllib.error.URLError | None = None
    for _ in range(30):
        try:
            request_json("GET", base_url, "/health")
            return
        except urllib.error.URLError as error:
            last_error = error
            time.sleep(0.5)
    raise RuntimeError(f"Service {base_url} is not ready") from last_error


def wait_for_verification(order_id: str) -> dict:
    verification = None
    for _ in range(30):
        verification = request_json(
            "GET", ORDER_BASE_URL, f"/orders/{order_id}/verification"
        )
        if verification["counts"]["notifications"] >= 1:
            return verification
        time.sleep(0.5)
    if verification is None:
        raise RuntimeError("Verification result was not received")
    return verification


def detected_bugs(verification: dict) -> list[str]:
    invariants = verification["invariants"]
    bugs = []
    if not invariants["I1_single_payment"]:
        bugs.append("B1")
    if not invariants["I2_paid_order_has_event"]:
        bugs.append("B3")
    if not invariants["I3_request_within_deadline"]:
        bugs.append("B4")
    if not invariants["I4_attempt_limit"]:
        bugs.append("B2")
    return bugs


def append_result(result: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument(
        "--point", default="payment.http.response.after_commit"
    )
    parser.add_argument("--fault-type", default="lost_response")
    parser.add_argument("--delay-ms", type=int, default=1500)
    parser.add_argument("--activation-limit", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--expect-bug")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    fault_configuration = {
        "experiment_id": args.experiment_id,
        "point_id": args.point,
        "fault_type": args.fault_type,
        "delay_ms": args.delay_ms,
        "activation_limit": args.activation_limit,
    }

    wait_for_service(ORDER_BASE_URL)
    wait_for_service(PAYMENT_BASE_URL)
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    result: dict | None = None
    try:
        request_json("DELETE", PAYMENT_BASE_URL, "/internal/fault")
        request_json(
            "PUT", PAYMENT_BASE_URL, "/internal/fault", fault_configuration
        )
        created = request_json(
            "POST",
            ORDER_BASE_URL,
            "/orders",
            {"customer_id": "experiment-user", "amount": "100.00"},
            timeout=10,
        )
        order_id = created["order_id"]
        verification = wait_for_verification(order_id)
        fault_state = request_json("GET", PAYMENT_BASE_URL, "/internal/fault")
        bugs = detected_bugs(verification)
        finished_at = datetime.now(timezone.utc)
        result = {
            "schema_version": 1,
            "experiment_id": args.experiment_id,
            "strategy": "direct",
            "seed": args.seed,
            "selected_point": args.point,
            "fault_type": args.fault_type,
            "fault_parameters": {
                "delay_ms": args.delay_ms,
                "activation_limit": args.activation_limit,
            },
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "wall_time_ms": round((time.perf_counter() - started) * 1000),
            "order_id": order_id,
            "trace_id": created["trace_id"],
            "fault_activation_count": fault_state["activation_count"],
            "useful_injection": fault_state["activation_count"] > 0,
            "verification": verification,
            "detected_bugs": bugs,
        }
        append_result(result)
    finally:
        try:
            request_json("DELETE", PAYMENT_BASE_URL, "/internal/fault")
        except urllib.error.URLError:
            pass

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.expect_bug and args.expect_bug not in result["detected_bugs"]:
        raise SystemExit(f"Expected bug {args.expect_bug} was not detected")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error
