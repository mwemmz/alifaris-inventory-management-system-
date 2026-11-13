"""JSON API for the POS and for site-to-site sync. Auth: `Authorization: Token <key>` (create keys under Configuration)."""
import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .models import ApiToken, Location, OperationType, Picking, Product, StockMove
from .services import stock, sync
from .services.stock import StockError


def api(view):
    @csrf_exempt
    @wraps(view)
    def wrapper(request, *a, **kw):
        header = request.headers.get("Authorization", "")
        key = header[6:].strip() if header.lower().startswith("token ") else ""
        token = ApiToken.objects.filter(key=key, active=True).first() if key else None
        if not token:
            return JsonResponse({"error": "Missing or invalid API token"}, status=401)
        ApiToken.objects.filter(pk=token.pk).update(last_used=timezone.now())
        try:
            return view(request, *a, **kw)
        except (ValueError, KeyError, InvalidOperation, StockError, Product.DoesNotExist, Location.DoesNotExist) as e:
            return JsonResponse({"error": str(e).strip("'") or e.__class__.__name__}, status=400)
    return wrapper


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        raise ValueError("Body must be valid JSON")


def _dec(v):
    return Decimal(str(v))


def _product(line):
    if line.get("ref"):
        return Product.objects.get(internal_ref=line["ref"])
    if line.get("barcode"):
        p = Product.objects.filter(barcode=line["barcode"]).first()
        if p:
            return p
    raise ValueError(f"Unknown product: {line.get('ref') or line.get('barcode') or '(none given)'}")


def product_json(p):
    return {"id": p.pk, "ref": p.internal_ref, "barcode": p.barcode, "name": p.name,
            "price": str(p.price), "secondary_price": str(p.secondary_price), "tax_type": p.tax_type,
            "category": p.category.name if p.category_id else None, "on_hand": str(p.on_hand),
            "type": p.product_type, "active": p.active, "updated": p.updated.isoformat(),
            "cost": str(p.cost), "min_qty": str(p.min_qty), "max_qty": str(p.max_qty),
            "uom": p.uom.name if p.uom_id else None, "zra_item_class_code": p.zra_item_class_code,
            "zra_item_type": p.zra_item_type}


@api
def products(request):
    qs = Product.objects.select_related("category", "uom")
    if q := request.GET.get("q"):
        qs = qs.filter(name__icontains=q) | qs.filter(internal_ref__iexact=q) | qs.filter(barcode=q)
    if since := request.GET.get("updated_since"):
        dt = parse_datetime(since)
        if not dt:
            raise ValueError("updated_since must be an ISO date-time")
        qs = qs.filter(updated__gte=dt)
    limit = min(int(request.GET.get("limit", 500)), 2000)
    return JsonResponse({"products": [product_json(p) for p in qs.order_by("id")[:limit]]})


@api
def stock_level(request):
    p = _product({"ref": request.GET.get("ref"), "barcode": request.GET.get("barcode")})
    return JsonResponse({"ref": p.internal_ref, "on_hand": str(p.on_hand)})


def _op(code, warehouse_code):
    qs = OperationType.objects.filter(code=code)
    if warehouse_code:
        qs = qs.filter(warehouse__code=warehouse_code)
    op = qs.first()
    if not op:
        raise ValueError(f"No {code} operation type for warehouse '{warehouse_code or 'any'}'")
    return op


def _document(request, code, source_from_dest=False):
    data = _body(request)
    ext = str(data["external_ref"]).strip()
    if not ext:
        raise ValueError("external_ref is required")
    op = _op(code, data.get("warehouse"))
    existing = Picking.objects.filter(op_type=op, origin=ext).first()
    if existing:
        return JsonResponse({"picking": existing.ref, "duplicate": True, "state": existing.state})
    lines = data.get("lines") or []
    if not lines:
        raise ValueError("lines must not be empty")
    with transaction.atomic():
        picking = stock.new_picking(op, [(_product(ln), _dec(ln["qty"])) for ln in lines], origin=ext,
                                    notes=str(data.get("note", "")))
        stock.validate_picking(picking)
    return JsonResponse({"picking": picking.ref, "duplicate": False, "state": picking.state,
                         "lines": [{"ref": ln.product.internal_ref, "qty": str(ln.qty),
                                    "on_hand": str(ln.product.on_hand)} for ln in picking.lines.select_related("product")]},
                        status=201)


@api
def sales(request):
    """A POS sale. Takes stock out of the shop. Safe to resend: the same external_ref never deducts twice."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    return _document(request, OperationType.DELIVERY)


@api
def sale_returns(request):
    """A customer return. Puts stock back (a receipt from the customer location)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    data = _body(request)
    request._body = json.dumps(data).encode()
    op = _op(OperationType.RECEIPT, data.get("warehouse"))
    cust = Location.objects.filter(usage=Location.CUSTOMER).first()
    ext = str(data["external_ref"]).strip()
    existing = Picking.objects.filter(op_type=op, origin=ext).first()
    if existing:
        return JsonResponse({"picking": existing.ref, "duplicate": True, "state": existing.state})
    if not data.get("lines"):
        raise ValueError("lines must not be empty")
    with transaction.atomic():
        picking = stock.new_picking(op, [(_product(ln), _dec(ln["qty"])) for ln in data["lines"]], origin=ext,
                                    source=cust, notes="Customer return")
        stock.validate_picking(picking)
    return JsonResponse({"picking": picking.ref, "duplicate": False, "state": picking.state}, status=201)


@api
def sync_moves(request):
    """GET: ledger rows recorded here since `since`. POST: apply rows from another site (idempotent by uuid)."""
    if request.method == "GET":
        qs = StockMove.objects.select_related("product", "source", "dest").order_by("recorded_at", "id")
        if since := request.GET.get("since"):
            dt = parse_datetime(since)
            if not dt:
                raise ValueError("since must be an ISO date-time")
            qs = qs.filter(recorded_at__gt=dt)
        if site := request.GET.get("exclude_site"):
            qs = qs.exclude(site_code=site)
        rows = list(qs[:int(request.GET.get("limit", 500))])
        return JsonResponse({"moves": [{
            "uuid": str(m.uuid), "ref": m.product.internal_ref, "source": m.source.full_name,
            "dest": m.dest.full_name, "qty": str(m.qty), "unit_cost": str(m.unit_cost),
            "date": m.date.isoformat(), "reason": m.reason, "site": m.site_code,
            "recorded_at": m.recorded_at.isoformat()} for m in rows]})
    if request.method != "POST":
        return JsonResponse({"error": "GET or POST only"}, status=405)
    applied, skipped = sync.apply_remote_moves(_body(request).get("moves", []))
    return JsonResponse({"applied": applied, "already_had": skipped})
