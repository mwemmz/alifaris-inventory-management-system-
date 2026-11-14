from django.test import TestCase

# Create your tests here.


class EveryTileOpens(TestCase):
    def test_all_tiles_open_for_a_manager(self):
        from django.contrib.auth import get_user_model
        from . import modules
        u = get_user_model().objects.create_user("m", password="pw", is_staff=True)
        self.client.force_login(u)
        from django.core.management import call_command
        call_command("seed_inventory", verbosity=0)
        for t in modules.tiles():
            r = self.client.get(t["url"], follow=True)
            self.assertEqual(r.status_code, 200, t["slug"])
            self.assertNotContains(r, "not built yet", msg_prefix=t["slug"])
