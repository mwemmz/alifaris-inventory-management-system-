"""Roles: staff = manager (everything). 'Storekeeper' group = can record stock operations. Anyone else logged in = view only."""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

STOREKEEPER = "Storekeeper"


def can_edit(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.groups.filter(name=STOREKEEPER).exists())


def deny(request):
    messages.error(request, "Your account can view stock but not change it. Ask a manager to add you to the Storekeeper group.")
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
