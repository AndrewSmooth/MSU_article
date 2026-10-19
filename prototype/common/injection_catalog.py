POINTS = [
    {
        "id": "order.http.payment.create",
        "service": "order-service",
        "fault_types": ["delay", "error", "unavailable"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "payment.http.request.before_processing",
        "service": "payment-service",
        "fault_types": ["delay", "error", "unavailable"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "payment.http.response.after_commit",
        "service": "payment-service",
        "fault_types": ["lost_response"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "order.kafka.order_paid.publish",
        "service": "order-service",
        "fault_types": ["delay", "error", "unavailable"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "notification.kafka.order_paid.consume",
        "service": "notification-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "order.db.order.read",
        "service": "order-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": True,
    },
    {
        "id": "payment.http.refund.create",
        "service": "payment-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": True,
    },
    {
        "id": "notification.kafka.notification.retry",
        "service": "notification-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": True,
    },
]


def implemented_points() -> list[dict]:
    return [point for point in POINTS if point["implemented"]]


def point_by_id(point_id: str) -> dict:
    for point in POINTS:
        if point["id"] == point_id:
            return point
    raise KeyError(f"Unknown injection point: {point_id}")


FAULT_CASES = [
    {
        "id": "F01",
        "point_id": "payment.http.response.after_commit",
        "fault_type": "lost_response",
        "delay_ms": 1500,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": "B1",
    },
    {
        "id": "F02",
        "point_id": "payment.http.request.before_processing",
        "fault_type": "error",
        "delay_ms": 0,
        "status_code": 400,
        "activation_limit": 2,
        "expected_bug": "B2",
    },
    {
        "id": "F03",
        "point_id": "order.kafka.order_paid.publish",
        "fault_type": "error",
        "delay_ms": 0,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": "B3",
    },
    {
        "id": "F04",
        "point_id": "order.http.payment.create",
        "fault_type": "unavailable",
        "delay_ms": 3000,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": "B4",
    },
    {
        "id": "F05",
        "point_id": "order.http.payment.create",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F06",
        "point_id": "payment.http.request.before_processing",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F07",
        "point_id": "order.kafka.order_paid.publish",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F08",
        "point_id": "notification.kafka.order_paid.consume",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F09",
        "point_id": "order.db.order.read",
        "fault_type": "error",
        "delay_ms": 0,
        "status_code": 500,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F10",
        "point_id": "order.db.order.read",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F11",
        "point_id": "payment.http.refund.create",
        "fault_type": "error",
        "delay_ms": 0,
        "status_code": 500,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F12",
        "point_id": "payment.http.refund.create",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F13",
        "point_id": "notification.kafka.notification.retry",
        "fault_type": "error",
        "delay_ms": 0,
        "status_code": 500,
        "activation_limit": 1,
        "expected_bug": None,
    },
    {
        "id": "F14",
        "point_id": "notification.kafka.notification.retry",
        "fault_type": "delay",
        "delay_ms": 100,
        "status_code": 503,
        "activation_limit": 1,
        "expected_bug": None,
    },
]


def fault_case_by_id(case_id: str) -> dict:
    for fault_case in FAULT_CASES:
        if fault_case["id"] == case_id:
            return fault_case
    raise KeyError(f"Unknown fault case: {case_id}")
