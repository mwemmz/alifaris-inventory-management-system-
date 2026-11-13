"""Exchange data with another installation.

Catalogue (products) flows from the head office to the shops. Stock moves flow both ways and are applied by uuid,
so a move can never be counted twice, however often a sync runs or is interrupted.
"""
import json
from datetime import timedelta
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import Location, Product, ProductCategory, StockMove, UoM
from . import stock

BATCH = 200


class SyncError(Exception):
    pass


def find_location(name):
    for loc in Location.objects.select_related("warehouse", "parent"):
        if loc.full_name == name:
            return loc
    raise Location.DoesNotExist(f"Unknown location: {name}")


def apply_remote_moves(moves):
    """Apply ledger rows received from another site. Returns (applied, already_had). All or nothing."""
    applied = skipped = 0
    with transaction.atomic():
        for m in moves:
            _, created = stock.apply_move(
                Product.objects.get(internal_ref=m["ref"]), find_location(m["source"]), find_location(m["dest"]),
                Decimal(str(m["qty"])), uuid=m["uuid"], reason=m.get("reason", ""), date=parse_datetime(m["date"]),
                unit_cost=Decimal(str(m.get("unit_cost", 0))), site_code=m.get("site"))
            applied += created
            skipped += not created
    return applied, skipped


def upsert_products(rows):
    """Create or update products by internal reference. Stock quantities are never copied: they come from moves."""
    created = updated = 0
    for r in rows:
        fields = {"name": r["name"], "barcode": r.get("barcode") or "", "price": Decimal(r["price"]),
                  "secondary_price": Decimal(r["secondary_price"]), "tax_type": r.get("tax_type") or "A",
                  "product_type": r.get("type") or Product.GOODS, "active": r.get("active", True)}
        for k in ("cost", "min_qty", "max_qty"):
            if r.get(k) is not None:
                fields[k] = Decimal(r[k])
        if r.get("zra_item_class_code") is not None:
            fields["zra_item_class_code"] = r["zra_item_class_code"]
        if r.get("zra_item_type"):
            fields["zra_item_type"] = r["zra_item_type"]
        if r.get("category"):
            fields["category"], _ = ProductCategory.objects.get_or_create(name=r["category"])
        if r.get("uom"):
            fields["uom"] = UoM.objects.filter(name=r["uom"]).first()
        obj, was_new = Product.objects.update_or_create(internal_ref=r["ref"], defaults=fields)
        created += was_new
        updated += not was_new
    return created, updated


def _http(method, url, token, body=None):
    """One HTTP call. Kept separate so tests can replace it."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Token {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise SyncError(f"{url} answered {e.code}: {detail}")
    except Exception as e:
        raise SyncError(f"Could not reach {url}: {e}")


def sync_peer(peer):
    """Run one full sync with a peer. Progress is saved after each step, so a failure part-way loses nothing."""
    base = peer.url.rstrip("/") + "/api/v1"
    started = timezone.now()
    notes = []
    try:
        if peer.pull_products:
            qs = {"limit": 2000}
            if peer.last_product_pull:
                qs["updated_since"] = peer.last_product_pull.isoformat()
            rows = _http("GET", f"{base}/products/?{urllib.parse.urlencode(qs)}", peer.token)["products"]
            c, u = upsert_products(rows)
            peer.last_product_pull = started - timedelta(minutes=5)  # overlap covers clock differences; upserts are harmless to repeat
            peer.save(update_fields=["last_product_pull"])
            notes.append(f"products {c} new, {u} updated")

        pulled = 0
        while True:
            qs = {"exclude_site": settings.SITE_CODE, "limit": BATCH}
            if peer.last_move_pull:
                qs["since"] = peer.last_move_pull.isoformat()
            moves = _http("GET", f"{base}/sync/moves/?{urllib.parse.urlencode(qs)}", peer.token)["moves"]
            if not moves:
                break
            apply_remote_moves(moves)
            peer.last_move_pull = parse_datetime(moves[-1]["recorded_at"])
            peer.save(update_fields=["last_move_pull"])
            pulled += len(moves)
            if len(moves) < BATCH:
                break
        notes.append(f"{pulled} move(s) received")

        pushed = 0
        while True:
            qs = StockMove.objects.filter(site_code=settings.SITE_CODE).select_related("product", "source", "dest")
            if peer.last_move_push:
                qs = qs.filter(recorded_at__gt=peer.last_move_push)
            batch = list(qs.order_by("recorded_at", "id")[:BATCH])
            if not batch:
                break
            payload = [{"uuid": str(m.uuid), "ref": m.product.internal_ref, "source": m.source.full_name,
                        "dest": m.dest.full_name, "qty": str(m.qty), "unit_cost": str(m.unit_cost),
                        "date": m.date.isoformat(), "reason": m.reason, "site": m.site_code} for m in batch]
            _http("POST", f"{base}/sync/moves/", peer.token, {"moves": payload})
            peer.last_move_push = batch[-1].recorded_at
            peer.save(update_fields=["last_move_push"])
            pushed += len(batch)
            if len(batch) < BATCH:
                break
        notes.append(f"{pushed} move(s) sent")
        result = "OK: " + ", ".join(notes)
        ok = True
    except (SyncError, Product.DoesNotExist, Location.DoesNotExist, KeyError, ValueError) as e:
        result = f"FAILED: {str(e).strip(chr(39))}" + (f" (done before failing: {', '.join(notes)})" if notes else "")
        ok = False
    peer.last_run = timezone.now()
    peer.last_result = result[:300]
    peer.save(update_fields=["last_run", "last_result"])
    return ok, result
