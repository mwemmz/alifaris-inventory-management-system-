"""Module tiles shown on the home screen. Order = display order (6 per row)."""
from django.utils.safestring import mark_safe

# Original flat icons, 56x56 viewBox. Colours are Alifaris palette, not Odoo artwork.
def _svg(body):
    return mark_safe('<svg viewBox="0 0 56 56" width="56" height="56" aria-hidden="true">%s</svg>' % body)

ICONS = {
    "discuss": _svg('<circle cx="26" cy="26" r="20" fill="#ff8a3d"/><circle cx="34" cy="30" r="14" fill="#ff6b2c"/>'),
    "contacts": _svg('<rect x="6" y="8" width="38" height="42" rx="6" fill="#3a3f45"/><rect x="14" y="4" width="38" height="42" rx="6" fill="#f4a6a0" opacity=".8"/><circle cx="25" cy="22" r="6" fill="#fff"/><path d="M14 40c2-8 18-8 22 0z" fill="#fff"/>'),
    "sales": _svg('<rect x="8" y="26" width="12" height="24" rx="2" fill="#d4202c"/><rect x="22" y="12" width="14" height="38" rx="2" fill="#ff8a3d"/><rect x="38" y="6" width="12" height="44" rx="2" fill="#f5b83d"/>'),
    "dashboards": _svg('<rect x="6" y="6" width="22" height="22" rx="5" fill="#d4202c"/><rect x="32" y="6" width="18" height="9" rx="4" fill="#ef5b64"/><rect x="32" y="19" width="18" height="9" rx="4" fill="#f4a6ab"/><rect x="6" y="32" width="12" height="18" rx="4" fill="#7b8087"/><rect x="22" y="32" width="12" height="18" rx="4" fill="#2f3338"/><rect x="38" y="32" width="12" height="18" rx="4" fill="#2f3338"/>'),
    "subscriptions": _svg('<path d="M28 8a20 20 0 1 0 20 20" fill="none" stroke="#ff9a2e" stroke-width="7" stroke-linecap="round"/><circle cx="14" cy="14" r="6" fill="#2f3338"/><circle cx="44" cy="40" r="6" fill="#2f3338"/>'),
    "pos": _svg('<path d="M6 16l6-10h32l6 10z" fill="#f5b83d"/><rect x="8" y="16" width="40" height="32" fill="#fff3d6"/><path d="M6 16h10v10a5 5 0 0 1-10 0zM16 16h12v10a6 6 0 0 1-12 0zM28 16h12v10a6 6 0 0 1-12 0zM40 16h10v10a5 5 0 0 1-10 0z" fill="#d4202c"/>'),
    "motors": _svg('<rect x="2" y="2" width="52" height="52" rx="6" fill="#24272b"/><circle cx="28" cy="28" r="14" fill="none" stroke="#d4202c" stroke-width="3"/><circle cx="28" cy="28" r="4.5" fill="#d4202c"/>'),
    "accounting": _svg('<ellipse cx="22" cy="20" rx="14" ry="14" fill="#f5b83d"/><circle cx="38" cy="38" r="12" fill="#2f3338"/><path d="M8 50L50 8" stroke="#d4202c" stroke-width="7" stroke-linecap="round"/>'),
    "documents": _svg('<rect x="8" y="12" width="28" height="36" rx="4" fill="#7b8087" transform="rotate(-12 22 30)"/><rect x="20" y="8" width="28" height="36" rx="4" fill="#ff9a2e" transform="rotate(10 34 26)"/>'),
    "purchase": _svg('<rect x="4" y="10" width="48" height="36" rx="5" fill="#d4202c"/><rect x="4" y="10" width="48" height="10" fill="#2f3338"/><rect x="4" y="22" width="48" height="6" fill="#3a3f45"/>'),
    "inventory": _svg('<path d="M28 4l20 10v24L28 52 8 38V14z" fill="#ff9a2e"/><path d="M28 4l20 10-20 10L8 14z" fill="#ffc15e"/><path d="M28 24v28L8 38V14z" fill="#d4202c"/>'),
    "manufacturing": _svg('<path d="M4 50V30l12-8v8l12-8v8l12-8v28z" fill="#2f3338"/><rect x="38" y="8" width="8" height="22" fill="#f5b83d"/><rect x="10" y="38" width="8" height="12" fill="#1f2326"/>'),
    "shopfloor": _svg('<path d="M4 20l34-10 10 22-34 10z" fill="#7b8087"/><path d="M14 44l24-22 14 8-18 22z" fill="#d4202c" opacity=".85"/>'),
    "barcode": _svg('<rect x="6" y="6" width="44" height="44" rx="5" fill="#fff" stroke="#e0e1e1"/><g fill="#d4202c"><rect x="12" y="12" width="4" height="32"/><rect x="19" y="12" width="2" height="32"/><rect x="24" y="12" width="5" height="32"/><rect x="32" y="12" width="2" height="32"/><rect x="37" y="12" width="3" height="32"/><rect x="43" y="12" width="2" height="32"/></g>'),
    "employees": _svg('<circle cx="28" cy="16" r="9" fill="#d4202c"/><path d="M12 36c2-8 30-8 32 0v6H12z" fill="#d4202c"/><rect x="4" y="42" width="48" height="8" rx="4" fill="#ffb02e"/><rect x="4" y="42" width="24" height="8" rx="4" fill="#2f3338"/>'),
    "apps": _svg('<path d="M28 4a24 24 0 0 1 24 24H28z" fill="#d4202c"/><path d="M52 28a24 24 0 0 1-24 24V28z" fill="#7b8087"/><path d="M28 52A24 24 0 0 1 4 28h24z" fill="#ef5b64"/><path d="M4 28A24 24 0 0 1 28 4v24z" fill="#2f3338"/>'),
}

# slug, label, icon key, built?
MODULES = [
    ("discuss", "Discuss", "discuss", False),
    ("contacts", "Contacts", "contacts", True),
    ("sales", "Sales", "sales", True),
    ("dashboards", "Dashboards", "dashboards", True),
    ("subscriptions", "Subscriptions", "subscriptions", False),
    ("point-of-sale", "Point of Sale", "pos", True),
    ("alifaris-motors", "Alifaris Motors", "motors", False),
    ("accounting", "Accounting", "accounting", False),
    ("documents", "Documents", "documents", False),
    ("purchase", "Purchase", "purchase", True),
    ("inventory", "Inventory", "inventory", True),
    ("manufacturing", "Manufacturing", "manufacturing", False),
    ("shop-floor", "Shop Floor", "shopfloor", False),
    ("barcode", "Barcode", "barcode", True),
    ("employees", "Employees", "employees", False),
    ("apps", "Apps", "apps", False),
]

# Built modules open their own screen instead of the "not built yet" page.
URLS = {"inventory": "/inventory/", "point-of-sale": "/pos/", "dashboards": "/bi/",
        "contacts": "/inventory/config/partners/", "sales": "/pos/orders/", "purchase": "/inventory/purchase/",
        "barcode": "/inventory/scan/"}

# What each tile does today or is planned to do. Not-yet-built ones show this instead of a blank page.
INFO = {
    "contacts": "Customers and suppliers in one list, with TPIN and credit limits.",
    "sales": "Every till sale and refund, with receipts and ZRA status.",
    "purchase": "Purchase orders to suppliers, receiving goods into stock.",
    "barcode": "Scan a barcode to look up a product, its price and stock.",
    "dashboards": "Sales, profit, stock and reorder insights for managers.",
    "point-of-sale": "The till: sessions, sales, refunds and receipts, online or offline.",
    "inventory": "Stock levels, receipts, deliveries, transfers, counts and valuation.",
    "accounting": "Planned for a later phase: invoices and bills, payments, chart of accounts, VAT returns and profit and loss.",
    "employees": "Planned for a later phase: staff records, attendance, leave and payroll.",
    "discuss": "Planned for a later phase: messages and notes between staff, attached to products, orders and customers.",
    "subscriptions": "Planned: recurring billing for regular customers, such as monthly fleet accounts.",
    "alifaris-motors": "Planned: vehicle sales and workshop jobs, linked to the parts stock.",
    "documents": "Planned: one place for supplier invoices, ZRA documents and contracts, linked to the records they belong to.",
    "manufacturing": "Not needed for a parts shop today. Available later if Alifaris starts assembling or kitting products.",
    "shop-floor": "Not needed for a parts shop today. Works with Manufacturing for workshop tablets, if ever required.",
    "apps": "Every module in the system and where it stands.",
}


def tiles():
    return [{"slug": s, "label": l, "icon": ICONS[i], "built": b, "url": URLS.get(s, "/app/%s/" % s)}
            for s, l, i, b in MODULES]

def get(slug):
    for s, l, i, b in MODULES:
        if s == slug:
            return {"slug": s, "label": l, "built": b, "info": INFO.get(s, "")}
    return None


def catalogue():
    return [{"slug": s, "label": l, "built": b, "info": INFO.get(s, ""), "url": URLS.get(s, "/app/%s/" % s)}
            for s, l, i, b in MODULES if s != "apps"]
