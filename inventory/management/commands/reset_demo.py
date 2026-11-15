from django.core.management.base import BaseCommand, CommandError

from inventory.services import demo


class Command(BaseCommand):
    help = "Wipe a DEMO installation and load fresh demo data (needs ALIFARIS_DEMO=1 and ALIFARIS_DEMO_PASSWORD)."

    def handle(self, *args, **opts):
        try:
            demo.reset_now()
        except demo.DemoResetError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS("Demo data reset."))
