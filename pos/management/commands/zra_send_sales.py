import time

from django.core.management.base import BaseCommand

from pos.services import send_pending_sales


class Command(BaseCommand):
    help = "Send waiting POS sales to the ZRA VSDC. Use --loop to keep trying every --every seconds."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--every", type=int, default=60)

    def handle(self, *a, **o):
        while True:
            sent, failed = send_pending_sales(limit=50)
            self.stdout.write(f"sent {sent}, failed {failed}")
            if not o["loop"]:
                return
            time.sleep(o["every"])
