from django.core.management.base import BaseCommand

from django.core.management.base import CommandError

from inventory.services.backup import BackupUnavailable, make_backup


class Command(BaseCommand):
    help = "Copy the database to a timestamped file. Run it daily from the server's scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--to", default="backups", help="folder to write into (default: backups)")
        parser.add_argument("--keep", type=int, default=14, help="how many backups to keep (default: 14)")

    def handle(self, *a, to, keep, **o):
        try:
            path = make_backup(to, keep)
        except BackupUnavailable as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Backup written: {path} ({path.stat().st_size // 1024} KB)"))
