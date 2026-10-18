POINTS = [
    {
        "id": "order.http.payment.create",
        "service": "order-service",
        "fault_types": ["delay", "unavailable", "error"],
        "used_by_control_scenario": True,
        "implemented": False,
    },
    {
        "id": "order.db.status.commit",
        "service": "order-service",
        "fault_types": ["error"],
        "used_by_control_scenario": True,
        "implemented": False,
    },
    {
        "id": "order.kafka.order_paid.publish",
        "service": "order-service",
        "fault_types": ["delay", "unavailable", "error"],
        "used_by_control_scenario": True,
        "implemented": False,
    },
    {
        "id": "payment.db.payment.commit",
        "service": "payment-service",
        "fault_types": ["error"],
        "used_by_control_scenario": True,
        "implemented": False,
    },
    {
        "id": "payment.http.response.after_commit",
        "service": "payment-service",
        "fault_types": ["lost_response"],
        "used_by_control_scenario": True,
        "implemented": True,
    },
    {
        "id": "notification.kafka.order_paid.consume",
        "service": "notification-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": True,
        "implemented": False,
    },
    {
        "id": "order.db.order.read",
        "service": "order-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": False,
    },
    {
        "id": "payment.http.refund.create",
        "service": "payment-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": False,
    },
    {
        "id": "notification.kafka.notification.retry",
        "service": "notification-service",
        "fault_types": ["delay", "error"],
        "used_by_control_scenario": False,
        "implemented": False,
    },
]


def implemented_points() -> list[dict]:
    return [point for point in POINTS if point["implemented"]]
