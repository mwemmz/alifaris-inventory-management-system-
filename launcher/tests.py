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

    def test_planned_modules_hidden_by_default(self):
        from django.contrib.auth import get_user_model
        from . import modules
        self.client.force_login(get_user_model().objects.create_user("m2", password="pw", is_staff=True))
        slugs = {t["slug"] for t in modules.tiles()}
        self.assertEqual(slugs, {"dashboards", "point-of-sale", "inventory", "contacts", "sales", "purchase", "barcode"})
        self.assertEqual(self.client.get("/app/accounting/").status_code, 404)
        with self.settings(SHOW_PLANNED_MODULES=True):
            self.assertEqual(self.client.get("/app/accounting/").status_code, 200)
            self.assertIn("apps", {t["slug"] for t in modules.tiles()})
