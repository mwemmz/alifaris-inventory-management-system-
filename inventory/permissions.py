"""Roles: staff = manager (everything).
'Warehouse Manager' group = runs the warehouse: everything a storekeeper can do, plus warehouses, locations, categories,
units and contacts, product import, stock reports and reorder drafts, and the warehouse screen. No sales reports, ZRA
settings, API tokens, site sync or user admin.
'Storekeeper' group = can record stock operations. Anyone else logged in = view only."""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

STOREKEEPER = "Storekeeper"
WAREHOUSE_MANAGER = "Warehouse Manager"
# Configuration lists a warehouse manager may maintain (ZRA settings, tokens and site sync stay manager-only).
WM_CONFIG_KEYS = {"warehouses", "locations", "categories", "uom-categories", "uoms", "partners"}


def _active(user):
    return user.is_authenticated and user.is_active


def is_warehouse_manager(user):
    return _active(user) and user.groups.filter(name=WAREHOUSE_MANAGER).exists()


def can_manage_stock(user):
    """Manager or warehouse manager: warehouse screen, stock reports, reorder drafts, product import, stock configuration."""
    return _active(user) and (user.is_staff or is_warehouse_manager(user))


def can_edit(user):
    return _active(user) and (user.is_staff or user.groups.filter(name__in=[STOREKEEPER, WAREHOUSE_MANAGER]).exists())


def deny(request):
    messages.error(request, "Your account can view stock but not change it. Ask a manager to add you to the Storekeeper or Warehouse Manager group.")
    return redirect("inventory:overview")


def editor_required(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not can_edit(request.user):
            return deny(request)
        return view(request, *a, **kw)
    return wrapper


def post_needs_editor(view):
    """Anyone logged in may open the page; saving needs edit rights."""
    @wraps(view)
    def wrapper(request, *a, **kw):
        if request.method == "POST" and request.user.is_authenticated and not can_edit(request.user):
            return deny(request)
        return view(request, *a, **kw)
    return wrapper
