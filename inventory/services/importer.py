"""Product import from CSV or Excel (.xlsx). Mirrors the Create / Update / Create-or-update modes of the old screen."""
import csv
import io
from decimal import Decimal, InvalidOperation

from ..models import Product, ProductCategory, UoM

COLUMNS = ["internal_ref", "name", "barcode", "category", "uom", "price", "secondary_price", "cost",
           "min_qty", "max_qty", "tax_type", "zra_item_class_code", "opening_stock"]
NUMERIC = ["price", "secondary_price", "cost", "min_qty", "max_qty", "opening_stock"]


def template_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    w.writerow(["392", "10.00R20 DURUN UNIVERSAL YTH1", "", "Tyres", "Units", "5200", "5460", "4300", "5", "20",
                "A", "5059690800", "44"])
    return out.getvalue()


def read_rows(upload):
    name = upload.name.lower()
    data = upload.read()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(c or "").strip().lower() for c in rows[0]]
        return [dict(zip(header, ["" if c is None else str(c).strip() for c in r])) for r in rows[1:] if any(r)]
    text = data.decode("utf-8-sig", "replace")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text))]


def _num(row, key):
    raw = row.get(key, "")
    if raw == "":
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{key} '{raw}' is not a number")


def import_rows(rows, mode="create", skip_errors=True):
    """mode: create | update | both. Returns dict(created, updated, errors=[(row_no, message)], opening=[(product, qty)])."""
    res = {"created": 0, "updated": 0, "errors": [], "opening": []}
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            ref = row.get("internal_ref", "").strip()
            if not ref:
                raise ValueError("internal_ref is empty")
            existing = Product.objects.filter(internal_ref=ref).first()
            if existing and mode == "create":
                raise ValueError(f"{ref} already exists")
            if not existing and mode == "update":
                raise ValueError(f"{ref} does not exist")
            if not existing and not row.get("name"):
                raise ValueError("name is empty")
            values = {}
            if row.get("name"):
                values["name"] = row["name"]
            for key in ("barcode", "zra_item_class_code"):
                if row.get(key, "") != "":
                    values[key] = row[key]
            for key in NUMERIC[:5]:
                v = _num(row, key)
                if v is not None:
                    values[key] = v
            if row.get("tax_type"):
                if row["tax_type"] not in dict(Product.TAX_TYPES):
                    raise ValueError(f"tax_type '{row['tax_type']}' is not one of A, B, C1, C2, C3, D")
                values["tax_type"] = row["tax_type"]
            if row.get("category"):
                values["category"], _ = ProductCategory.objects.get_or_create(name=row["category"])
            if row.get("uom"):
                uom = UoM.objects.filter(name__iexact=row["uom"]).first()
                if not uom:
                    raise ValueError(f"unit of measure '{row['uom']}' does not exist")
                values["uom"] = uom
            elif not existing:
                values["uom"] = UoM.objects.filter(name="Units").first()
            opening = _num(row, "opening_stock")
            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
                existing.save()
                res["updated"] += 1
                product = existing
            else:
                product = Product.objects.create(internal_ref=ref, **values)
                res["created"] += 1
            if opening and opening > 0:
                res["opening"].append((product, opening))
        except Exception as e:
            res["errors"].append((n, str(e)))
            if not skip_errors:
                break
    return res
