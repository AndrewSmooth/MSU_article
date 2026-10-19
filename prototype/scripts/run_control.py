import json
import os
import time
import urllib.error
import urllib.request


BASE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8000")
PAYMENT_BASE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8001")
NOTIFICATION_BASE_URL = os.getenv(
    "NOTIFICATION_SERVICE_URL", "http://localhost:8002"
)
SERVICE_URLS = [BASE_URL, PAYMENT_BASE_URL, NOTIFICATION_BASE_URL]


def request_json(
    method: str, base_url: str, path: str, body: dict | None = None
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
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


def main() -> None:
    for service_url in SERVICE_URLS:
        wait_for_service(service_url)
        request_json(
            "PUT", service_url, "/internal/fault/mode", {"enabled": True}
        )
        request_json("DELETE", service_url, "/internal/fault")
    created = request_json(
        "POST",
        BASE_URL,
        "/orders",
        {"customer_id": "control-user", "amount": "100.00"},
    )
    order_id = created["order_id"]

    verification = None
    for _ in range(30):
        verification = request_json(
            "GET", BASE_URL, f"/orders/{order_id}/verification"
        )
        if verification["counts"]["notifications"] == 1:
            break
        time.sleep(0.5)

    if verification is None:
        raise RuntimeError("Verification result was not received")
    if verification["counts"]["notifications"] != 1:
        raise RuntimeError("Notification was not processed in time")
    if not verification["all_passed"]:
        raise RuntimeError(
            "Control scenario violated invariants:\n"
            + json.dumps(verification, ensure_ascii=False, indent=2)
        )

    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit(f"The stand is unavailable: {error}") from error

