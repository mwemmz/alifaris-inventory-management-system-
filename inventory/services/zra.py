"""ZRA Smart Invoice (VSDC) client.

Endpoints and field names follow the open-source `vsdcsdk` TypeScript SDK. They have NOT been tested against a
live VSDC from this build, so check the first few log entries on the ZRA Logs screen after enabling it.
"""
import json
import urllib.error
import urllib.request

from django.utils import timezone

from ..models import Product, ZraConfig, ZraLog


class ZraDisabled(Exception):
    pass


def check_item(product, cfg=None):
    """Return a list of problems that would make ZRA reject this item. Empty list = fine to send."""
    cfg = cfg or ZraConfig.get()
    problems = []
    code = (product.zra_item_class_code or "").strip()
    if len(code) != 10:
        problems.append(f"Item class code must be exactly 10 characters (has {len(code)}: '{code}')")
    if len(cfg.tpin) != 10:
        problems.append("TPIN in ZRA settings must be 10 digits")
    if len(cfg.branch_id) != 3:
        problems.append("Branch id in ZRA settings must be 3 characters")
    if len(product.internal_ref) > 20:
        problems.append("Internal reference is longer than 20 characters")
    if product.uom is None:
        problems.append("Unit of measure is missing")
    return problems


def _log(cfg, endpoint, status, message, model="", record_id="", req="", resp=""):
    return ZraLog.objects.create(tpin=cfg.tpin, branch=cfg.branch_id, model=model, record_id=str(record_id),
                                 endpoint=cfg.base_url.rstrip("/") + endpoint, status=status,
                                 message=message[:2000], request_body=req, response_body=resp[:20000])


def post(endpoint, payload, *, model="", record_id=""):
    """POST JSON to the VSDC. Always writes a ZraLog row. Returns (ok, message)."""
    cfg = ZraConfig.get()
    if not cfg.enabled:
        raise ZraDisabled("ZRA sync is switched off in ZRA settings")
    body = json.dumps(payload)
    req = urllib.request.Request(cfg.base_url.rstrip("/") + endpoint, data=body.encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_seconds) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        _log(cfg, endpoint, ZraLog.ERROR, f"HTTP {e.code}", model, record_id, body, raw)
        return False, f"HTTP {e.code}"
    except Exception as e:  # network down, VSDC not running, timeout
        _log(cfg, endpoint, ZraLog.ERROR, f"Could not reach the VSDC: {e}", model, record_id, body)
        return False, f"Could not reach the VSDC: {e}"
    try:
        data = json.loads(raw)
    except ValueError:
        _log(cfg, endpoint, ZraLog.ERROR, "VSDC returned something that is not JSON", model, record_id, body, raw)
        return False, "VSDC returned something that is not JSON"
    code = str(data.get("resultCd", ""))
    msg = str(data.get("resultMsg", ""))
    ok = code in ("000", "0")
    _log(cfg, endpoint, ZraLog.SUCCESS if ok else ZraLog.ERROR, msg or code, model, record_id, body, raw)
    return ok, msg or code


def _base(cfg):
    return {"tpin": cfg.tpin, "bhfId": cfg.branch_id}


def register_product(product, user="system"):
    cfg = ZraConfig.get()
    if not cfg.enabled:
        raise ZraDisabled("ZRA sync is switched off in ZRA settings")
    problems = check_item(product, cfg)
    if problems:
        msg = "Not sent. " + "; ".join(problems)
        _log(cfg, "/items/saveItems", ZraLog.ERROR, msg, "inventory.product", product.pk)
        return False, msg
    uom = product.uom
    payload = {**_base(cfg), "itemCd": product.internal_ref, "itemClsCd": product.zra_item_class_code.strip(),
               "itemTyCd": product.zra_item_type, "itemNm": product.name[:200], "orgnNatCd": "ZM",
               "pkgUnitCd": uom.zra_pkg_code, "qtyUnitCd": uom.zra_qty_code, "taxTyCd": product.tax_type,
               "bcd": product.barcode or None, "dftPrc": float(product.price), "isrcAplcbYn": "N", "useYn": "Y",
               "regrNm": user, "regrId": user, "modrNm": user, "modrId": user}
    ok, msg = post("/items/saveItems", payload, model="inventory.product", record_id=product.pk)
    if ok:
        Product.objects.filter(pk=product.pk).update(zra_registered=True)
    return ok, msg


def register_pending(limit=200):
    """Try every goods product that is not registered yet. Returns (sent_ok, failed)."""
    ok_n = bad_n = 0
    for p in Product.objects.filter(active=True, zra_registered=False).select_related("uom")[:limit]:
        ok, _ = register_product(p)
        ok_n += ok
        bad_n += not ok
    return ok_n, bad_n


def push_stock_level(product, user="system"):
    cfg = ZraConfig.get()
    if not cfg.enabled or not product.zra_registered:
        return False, "skipped"
    payload = {**_base(cfg), "itemCd": product.internal_ref, "rsdQty": float(product.on_hand),
               "regrNm": user, "regrId": user, "modrNm": user, "modrId": user}
    return post("/stockMaster/saveStockMaster", payload, model="inventory.product", record_id=product.pk)
