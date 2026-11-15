"""Reset a DEMO installation to fresh demo data. Refuses to run unless ALIFARIS_DEMO=1, so it can never wipe a live shop."""
import os
import threading

from django.core.management import call_command

_state = {"running": False}


class DemoResetError(Exception):
    pass


def enabled():
    return os.environ.get("ALIFARIS_DEMO") == "1"


def running():
    return _state["running"]


def check():
    if not enabled():
        raise DemoResetError("Demo reset is only available when ALIFARIS_DEMO=1.")
    if not os.environ.get("ALIFARIS_DEMO_PASSWORD"):
        raise DemoResetError("Set ALIFARIS_DEMO_PASSWORD first, otherwise nobody could log in after the reset.")
    if _state["running"]:
        raise DemoResetError("A reset is already running.")


def reset_now():
    """Wipe everything and load fresh demo data. Blocks until done."""
    check()
    _state["running"] = True
    try:
        call_command("flush", interactive=False, verbosity=0)
        call_command("seed_inventory", verbosity=0)
        call_command("seed_demo", verbosity=0)
    finally:
        _state["running"] = False


def reset_in_background():
    check()
    threading.Thread(target=reset_now, name="demo-reset", daemon=True).start()
