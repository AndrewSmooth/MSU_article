import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.injection_catalog import point_by_id


ORDER_BASE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8000")
PAYMENT_BASE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8001")
NOTIFICATION_BASE_URL = os.getenv(
    "NOTIFICATION_SERVICE_URL", "http://localhost:8002"
)
SERVICE_URLS = {
    "order-service": ORDER_BASE_URL,
    "payment-service": PAYMENT_BASE_URL,
    "notification-service": NOTIFICATION_BASE_URL,
}
RESULTS_PATH = Path(
    os.getenv("EXPERIMENT_RESULTS_PATH", "/app/results/experiments.jsonl")
)


def make_request(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
) -> urllib.request.Request:
    data = None if body is None else json.dumps(body).encode("utf-8")
    return urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )


def request_json(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
    timeout: float = 5,
) -> dict:
    request = make_request(method, base_url, path, body)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def request_json_with_status(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
    timeout: float = 30,
) -> tuple[int, dict]:
    request = make_request(method, base_url, path, body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


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
        status = verification["status"]
        counts = verification["counts"]
        if status.startswith("FAILED"):
            return verification
        if status == "PAID" and counts["events"] == 0:
            return verification
        if counts["notifications"] >= 1:
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
    if not invariants["I4_retry_policy"]:
        bugs.append("B2")
    if not invariants["I2_paid_order_has_event"]:
        bugs.append("B3")
    if not invariants["I3_request_within_deadline"]:
        bugs.append("B4")
    return bugs


def append_result(result: dict, results_path: Path = RESULTS_PATH) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--point", required=True)
    parser.add_argument("--fault-type", required=True)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--status-code", type=int, default=503)
    parser.add_argument("--activation-limit", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--expect-bug")
    return parser.parse_args()


def trace_id_from_response(response_body: dict) -> str | None:
    if "trace_id" in response_body:
        return response_body["trace_id"]
    detail = response_body.get("detail")
    if isinstance(detail, dict):
        return detail.get("trace_id")
    return None


def execute_experiment(
    *,
    experiment_id: str,
    point: str,
    fault_type: str,
    delay_ms: int,
    status_code: int,
    activation_limit: int,
    seed: int,
    strategy: str = "direct",
    metadata: dict | None = None,
    reset_before: bool = False,
    results_path: Path = RESULTS_PATH,
) -> dict:
    random.seed(seed)
    catalog_point = point_by_id(point)
    if fault_type not in catalog_point["fault_types"]:
        raise ValueError(f"Fault {fault_type!r} is not compatible with {point!r}")
    target_service = catalog_point["service"]
    target_url = SERVICE_URLS[target_service]
    fault_configuration = {
        "experiment_id": experiment_id,
        "point_id": point,
        "fault_type": fault_type,
        "delay_ms": delay_ms,
        "status_code": status_code,
        "activation_limit": activation_limit,
    }

    for service_url in SERVICE_URLS.values():
        wait_for_service(service_url)
        request_json(
            "PUT", service_url, "/internal/fault/mode", {"enabled": True}
        )
        request_json("DELETE", service_url, "/internal/fault")
    if reset_before:
        request_json("POST", ORDER_BASE_URL, "/internal/reset")

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    result: dict | None = None
    try:
        request_json("PUT", target_url, "/internal/fault", fault_configuration)

        order_id = str(uuid4())
        response_status, response_body = request_json_with_status(
            "POST",
            ORDER_BASE_URL,
            "/orders",
            {
                "order_id": order_id,
                "customer_id": "experiment-user",
                "amount": "100.00",
            },
        )
        verification = wait_for_verification(order_id)
        fault_state = request_json("GET", target_url, "/internal/fault")
        bugs = detected_bugs(verification)
        finished_at = datetime.now(timezone.utc)
        result = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "strategy": strategy,
            "seed": seed,
            "selected_point": point,
            "target_service": target_service,
            "fault_type": fault_type,
            "fault_parameters": {
                "delay_ms": delay_ms,
                "status_code": status_code,
                "activation_limit": activation_limit,
            },
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "wall_time_ms": round((time.perf_counter() - started) * 1000),
            "order_id": order_id,
            "trace_id": trace_id_from_response(response_body),
            "response_status": response_status,
            "fault_activation_count": fault_state["activation_count"],
            "useful_injection": fault_state["activation_count"] > 0,
            "verification": verification,
            "detected_bugs": bugs,
        }
        if metadata:
            result.update(metadata)
        append_result(result, results_path)
    finally:
        try:
            request_json("DELETE", target_url, "/internal/fault")
        except urllib.error.URLError:
            pass

    return result


def main() -> None:
    args = parse_args()
    try:
        result = execute_experiment(
            experiment_id=args.experiment_id,
            point=args.point,
            fault_type=args.fault_type,
            delay_ms=args.delay_ms,
            status_code=args.status_code,
            activation_limit=args.activation_limit,
            seed=args.seed,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.expect_bug and args.expect_bug not in result["detected_bugs"]:
        raise SystemExit(f"Expected bug {args.expect_bug} was not detected")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error
