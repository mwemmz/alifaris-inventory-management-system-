from django.conf import settings
from django.contrib.auth.decorators import login_required
import json

from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render

from . import modules


def _notice():
    """One alert for the home screen: ZRA trouble first, then low stock, then an optional fixed message."""
    try:
        from datetime import timedelta

        from django.utils import timezone

        from inventory.models import ZraConfig, ZraLog
        from inventory.services import stock
        cfg = ZraConfig.get()
        failed = ZraLog.objects.filter(status="error", date__gte=timezone.now() - timedelta(hours=24)).count()
        if cfg.enabled and failed:
            return f"{failed} ZRA request(s) failed in the last 24 hours. Open Inventory > ZRA Logs to see why."
        low = len(stock.replenishment_candidates())
        if low:
            return f"{low} product(s) are below their reorder minimum. Open Inventory > Replenishment."
    except Exception:
        pass  # the home screen must always load
    return settings.HOME_NOTICE or None


def _pulse(user):
    """Four live numbers for managers on the home screen. Never blocks the page."""
    if not user.is_staff:
        return None
    try:
        from datetime import date, timedelta
        from decimal import Decimal

        from django.db.models import Count, Q, Sum

        from inventory.services import stock
        from pos import receivables
        from pos.models import PosOrder

        def day(d):
            a = PosOrder.objects.filter(created_at__date=d).aggregate(t=Sum("total"), n=Count("id", filter=Q(kind="sale")))
            return a["t"] or Decimal("0"), a["n"] or 0

        today, n = day(date.today())
        yest, _ = day(date.today() - timedelta(days=1))
        return {
            "sales": today, "orders": n,
            "delta": round(float((today - yest) * 100 / yest)) if yest else None,
            "low": len(stock.replenishment_candidates()), "owed": receivables.total_owed(),
        }
    except Exception:
        return None


@login_required
def home(request):
    from django.utils import timezone
    now = timezone.localtime()
    ctx = {
        "tiles": modules.tiles(),
        "company": settings.COMPANY_NAME,
        "notice": _notice(),
        "unread": 2,
        "pulse": _pulse(request.user),
        "greeting": "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening",
        "who": (request.user.get_short_name() or request.user.get_username()).title(),
        "today": now,
    }
    return render(request, "launcher/home.html", ctx)


@login_required
def module(request, slug):
    mod = modules.get(slug)
    if not mod or (not mod["built"] and not modules.show_planned()):
        raise Http404
    if slug == "apps":
        return render(request, "launcher/apps.html", {"mods": modules.catalogue(), "company": settings.COMPANY_NAME})
    return render(request, "launcher/module_stub.html", {"mod": mod, "company": settings.COMPANY_NAME})


def manifest(request):
    return JsonResponse({
        "name": "Alifaris ERP", "short_name": "Alifaris", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#24272b", "theme_color": "#24272b",
        "icons": [{"src": "/static/launcher/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/launcher/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, content_type="application/manifest+json")


def service_worker(request):
    path = finders.find("inventory/sw.js")
    with open(path, encoding="utf-8") as fh:
        resp = HttpResponse(fh.read(), content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def offline(request):
    return render(request, "launcher/offline.html")
