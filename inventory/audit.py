"""Write one line to the audit trail. Accepts the request (best: it also records the address) or just a user."""
import logging

from .models import AuditLog

log_ = logging.getLogger(__name__)


def log(who, action, subject="", detail=""):
    try:
        user = getattr(who, "user", who)
        ip = None
        meta = getattr(who, "META", None)
        if meta:
            ip = (meta.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or meta.get("REMOTE_ADDR") or None)
        AuditLog.objects.create(user=user if getattr(user, "pk", None) else None,
                                username=getattr(user, "get_username", lambda: "")() if user else "",
                                action=action[:60], subject=str(subject)[:200], detail=str(detail)[:2000], ip=ip)
    except Exception:  # an audit failure must never stop a sale or a stock move
        log_.exception("audit log write failed")


def _login(sender, request, user, **kw):
    log(request, "login", user.get_username())


def _failed(sender, credentials, request=None, **kw):
    log(request, "failed login", credentials.get("username", ""))


def connect():
    from django.contrib.auth.signals import user_logged_in, user_login_failed
    user_logged_in.connect(_login, dispatch_uid="alifaris_audit_login")
    user_login_failed.connect(_failed, dispatch_uid="alifaris_audit_failed")
