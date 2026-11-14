"""Starts Alifaris ERP for the Windows desktop install: its own database folder, first-run setup, then the web server.

Built into alifaris-server.exe with PyInstaller (see desktop/build-installer.md). Run directly to test:
    python desktop_server.py --port 8765
"""
import argparse
import os
import secrets
import sys
from pathlib import Path


def data_dir():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(os.environ.get("ALIFARIS_DATA_DIR") or Path(base) / "AlifarisERP")
    d.mkdir(parents=True, exist_ok=True)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 on the shop server so other tills can connect")
    a = ap.parse_args()

    d = data_dir()
    key_file = d / "secret.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50))
    os.environ.setdefault("ALIFARIS_DB_PATH", str(d / "alifaris.sqlite3"))
    os.environ["DJANGO_SECRET_KEY"] = key_file.read_text().strip()
    os.environ["DJANGO_DEBUG"] = "0"
    os.environ["ALIFARIS_LOCAL"] = "1"
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,*")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if getattr(sys, "frozen", False):
        sys.path.insert(0, sys._MEIPASS)
        os.chdir(sys._MEIPASS)

    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_inventory", verbosity=0)

    # First run: one manager account with a random password, written where the app window can show it once.
    User = get_user_model()
    if not User.objects.exists():
        pw = secrets.token_urlsafe(9)
        User.objects.create_user("admin", password=pw, is_staff=True, is_superuser=True)
        (d / "first-login.txt").write_text(f"Username: admin\nPassword: {pw}\n\n"
                                           "Sign in, then open Configuration > Users and roles to change the password "
                                           "and add your staff.\n")

    # A fresh backup every time the program starts; the last 14 are kept.
    try:
        from inventory.services.backup import make_backup
        make_backup(d / "backups", keep=14)
    except Exception as e:  # never stop the shop from opening over a backup problem
        print("backup skipped:", e, flush=True)

    from waitress import serve
    from config.wsgi import application
    print(f"Alifaris ERP running on http://{a.host}:{a.port}  (data in {d})", flush=True)
    serve(application, host=a.host, port=a.port, threads=8)


if __name__ == "__main__":
    main()
