from django.http import JsonResponse
from django.utils.dateparse import parse_datetime

from inventory.api import _body, api

from . import services
from .models import PosOrder


@api
def sync_orders(request):
    """GET: sales recorded here since `since`. POST: sales from another site (idempotent by uuid)."""
    if request.method == "GET":
        qs = PosOrder.objects.select_related("refund_of").prefetch_related("lines", "payments").order_by("recorded_at", "id")
        if since := request.GET.get("since"):
            dt = parse_datetime(since)
            if not dt:
                raise ValueError("since must be an ISO date-time")
            qs = qs.filter(recorded_at__gt=dt)
        if site := request.GET.get("exclude_site"):
            qs = qs.exclude(site_code=site)
        rows = list(qs[:int(request.GET.get("limit", 500))])
        return JsonResponse({"orders": [services.order_to_dict(o) for o in rows]})
    if request.method != "POST":
        return JsonResponse({"error": "GET or POST only"}, status=405)
    added = services.import_remote_orders(_body(request).get("orders", []))
    return JsonResponse({"added": added})
