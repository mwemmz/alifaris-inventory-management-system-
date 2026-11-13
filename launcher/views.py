from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from . import modules


@login_required
def home(request):
    ctx = {
        "tiles": modules.tiles(),
        "company": settings.COMPANY_NAME,
        "notice": "Welcome to Alifaris ERP. Log in as an administrator to finish setting up your company.",
        "unread": 2,
    }
    return render(request, "launcher/home.html", ctx)


@login_required
def module(request, slug):
    mod = modules.get(slug)
    if not mod:
        raise Http404
    return render(request, "launcher/module_stub.html", {"mod": mod, "company": settings.COMPANY_NAME})
