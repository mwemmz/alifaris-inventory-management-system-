"""Consistent copies of the SQLite database, safe to take while the system is in use."""
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connection


def make_backup(dest_dir=None, keep=None):
    """Write a timestamped copy using SQLite's online backup API. Returns the file path.
    With `keep`, older backup files in dest_dir beyond that many are removed."""
    dest_dir = Path(dest_dir or tempfile.gettempdir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"alifaris-{settings.SITE_CODE}-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    path = dest_dir / name
    connection.ensure_connection()
    target = sqlite3.connect(path)
    try:
        connection.connection.backup(target)
    finally:
        target.close()
    if keep:
        files = sorted(dest_dir.glob(f"alifaris-{settings.SITE_CODE}-*.sqlite3"))
        for old in files[:-keep]:
            old.unlink(missing_ok=True)
    return path
