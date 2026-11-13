"""Module tiles shown on the home screen. Order = display order (6 per row)."""
from django.utils.safestring import mark_safe

# Original flat icons, 56x56 viewBox. Colours are Alifaris palette, not Odoo artwork.
def _svg(body):
    return mark_safe('<svg viewBox="0 0 56 56" width="56" height="56" aria-hidden="true">%s</svg>' % body)

ICONS = {
    "discuss": _svg('<circle cx="26" cy="26" r="20" fill="#ff8a3d"/><circle cx="34" cy="30" r="14" fill="#ff6b2c"/>'),
    "contacts": _svg('<rect x="6" y="8" width="38" height="42" rx="6" fill="#1f7a6d"/><rect x="14" y="4" width="38" height="42" rx="6" fill="#f4a6a0" opacity=".8"/><circle cx="25" cy="22" r="6" fill="#fff"/><path d="M14 40c2-8 18-8 22 0z" fill="#fff"/>'),
    "sales": _svg('<rect x="8" y="26" width="12" height="24" rx="2" fill="#8e3a6b"/><rect x="22" y="12" width="14" height="38" rx="2" fill="#ff8a3d"/><rect x="38" y="6" width="12" height="44" rx="2" fill="#f5b83d"/>'),
    "dashboards": _svg('<rect x="6" y="6" width="22" height="22" rx="5" fill="#8e3a6b"/><rect x="32" y="6" width="18" height="9" rx="4" fill="#ff6b6b"/><rect x="32" y="19" width="18" height="9" rx="4" fill="#ff9aa0"/><rect x="6" y="32" width="12" height="18" rx="4" fill="#2f8df5"/><rect x="22" y="32" width="12" height="18" rx="4" fill="#27b6a0"/><rect x="38" y="32" width="12" height="18" rx="4" fill="#27b6a0"/>'),
    "subscriptions": _svg('<path d="M28 8a20 20 0 1 0 20 20" fill="none" stroke="#ff9a2e" stroke-width="7" stroke-linecap="round"/><circle cx="14" cy="14" r="6" fill="#27b6a0"/><circle cx="44" cy="40" r="6" fill="#27b6a0"/>'),
    "pos": _svg('<path d="M6 16l6-10h32l6 10z" fill="#f5b83d"/><rect x="8" y="16" width="40" height="32" fill="#fff3d6"/><path d="M6 16h10v10a5 5 0 0 1-10 0zM16 16h12v10a6 6 0 0 1-12 0zM28 16h12v10a6 6 0 0 1-12 0zM40 16h10v10a5 5 0 0 1-10 0z" fill="#8e3a6b"/>'),
    "motors": _svg('<rect x="2" y="2" width="52" height="52" rx="6" fill="#16264a"/><circle cx="28" cy="28" r="14" fill="none" stroke="#e6b94d" stroke-width="3"/><circle cx="28" cy="28" r="4.5" fill="#e6b94d"/>'),
    "accounting": _svg('<ellipse cx="22" cy="20" rx="14" ry="14" fill="#f5b83d"/><circle cx="38" cy="38" r="12" fill="#27b6a0"/><path d="M8 50L50 8" stroke="#8e3a6b" stroke-width="7" stroke-linecap="round"/>'),
    "documents": _svg('<rect x="8" y="12" width="28" height="36" rx="4" fill="#2f8df5" transform="rotate(-12 22 30)"/><rect x="20" y="8" width="28" height="36" rx="4" fill="#ff9a2e" transform="rotate(10 34 26)"/>'),
    "purchase": _svg('<rect x="4" y="10" width="48" height="36" rx="5" fill="#8e3a6b"/><rect x="4" y="10" width="48" height="10" fill="#27b6a0"/><rect x="4" y="22" width="48" height="6" fill="#1b3a5c"/>'),
    "inventory": _svg('<path d="M28 4l20 10v24L28 52 8 38V14z" fill="#ff9a2e"/><path d="M28 4l20 10-20 10L8 14z" fill="#ffc15e"/><path d="M28 24v28L8 38V14z" fill="#8e3a6b"/>'),
    "manufacturing": _svg('<path d="M4 50V30l12-8v8l12-8v8l12-8v28z" fill="#27b6a0"/><rect x="38" y="8" width="8" height="22" fill="#f5b83d"/><rect x="10" y="38" width="8" height="12" fill="#1b6f66"/>'),
    "shopfloor": _svg('<path d="M4 20l34-10 10 22-34 10z" fill="#2f8df5"/><path d="M14 44l24-22 14 8-18 22z" fill="#8e3a6b" opacity=".85"/>'),
    "barcode": _svg('<rect x="6" y="6" width="44" height="44" rx="5" fill="#fff" stroke="#e5e1ee"/><g fill="#8e3a6b"><rect x="12" y="12" width="4" height="32"/><rect x="19" y="12" width="2" height="32"/><rect x="24" y="12" width="5" height="32"/><rect x="32" y="12" width="2" height="32"/><rect x="37" y="12" width="3" height="32"/><rect x="43" y="12" width="2" height="32"/></g>'),
    "employees": _svg('<circle cx="28" cy="16" r="9" fill="#8e3a6b"/><path d="M12 36c2-8 30-8 32 0v6H12z" fill="#8e3a6b"/><rect x="4" y="42" width="48" height="8" rx="4" fill="#ffb02e"/><rect x="4" y="42" width="24" height="8" rx="4" fill="#27b6a0"/>'),
    "apps": _svg('<path d="M28 4a24 24 0 0 1 24 24H28z" fill="#8e3a6b"/><path d="M52 28a24 24 0 0 1-24 24V28z" fill="#2f8df5"/><path d="M28 52A24 24 0 0 1 4 28h24z" fill="#ff6b6b"/><path d="M4 28A24 24 0 0 1 28 4v24z" fill="#27b6a0"/>'),
}

# slug, label, icon key, built?
MODULES = [
    ("discuss", "Discuss", "discuss", False),
    ("contacts", "Contacts", "contacts", False),
    ("sales", "Sales", "sales", False),
    ("dashboards", "Dashboards", "dashboards", False),
    ("subscriptions", "Subscriptions", "subscriptions", False),
    ("point-of-sale", "Point of Sale", "pos", False),
    ("alifaris-motors", "Alifaris Motors", "motors", False),
    ("accounting", "Accounting", "accounting", False),
    ("documents", "Documents", "documents", False),
    ("purchase", "Purchase", "purchase", False),
    ("inventory", "Inventory", "inventory", True),
    ("manufacturing", "Manufacturing", "manufacturing", False),
    ("shop-floor", "Shop Floor", "shopfloor", False),
    ("barcode", "Barcode", "barcode", False),
    ("employees", "Employees", "employees", False),
    ("apps", "Apps", "apps", False),
]

# Built modules open their own screen instead of the "not built yet" page.
URLS = {"inventory": "/inventory/"}


def tiles():
    return [{"slug": s, "label": l, "icon": ICONS[i], "built": b, "url": URLS.get(s, "/app/%s/" % s)}
            for s, l, i, b in MODULES]

def get(slug):
    for s, l, i, b in MODULES:
        if s == slug:
            return {"slug": s, "label": l, "built": b}
    return None
