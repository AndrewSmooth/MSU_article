import json
import os
import time
import urllib.error
import urllib.request


BASE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8000")


def request_json(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main() -> None:
    created = request_json(
        "POST",
        "/orders",
        {"customer_id": "control-user", "amount": "100.00"},
    )
    order_id = created["order_id"]

    verification = None
    for _ in range(30):
        verification = request_json("GET", f"/orders/{order_id}/verification")
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

