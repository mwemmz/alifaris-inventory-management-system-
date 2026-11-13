from django.core.management.base import BaseCommand

from inventory.models import SyncPeer
from inventory.services.sync import sync_peer


class Command(BaseCommand):
    help = "Sync with every active peer site. Run it every few minutes from Task Scheduler or cron."

    def handle(self, *args, **opts):
        peers = SyncPeer.objects.filter(active=True)
        if not peers:
            self.stdout.write("No active peers. Add one under Configuration > Site sync.")
        failed = 0
        for peer in peers:
            ok, msg = sync_peer(peer)
            failed += not ok
            self.stdout.write(f"{peer.name}: {msg}")
        if failed:
            raise SystemExit(1)
