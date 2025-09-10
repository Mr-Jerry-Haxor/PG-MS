from .models import AuditLog


def log(actor, action: str, target_type: str, target_id: int, message: str = "", meta: dict | None = None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        message=message,
        meta=meta or {},
    )
