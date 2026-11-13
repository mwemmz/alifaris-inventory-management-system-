from decimal import Decimal

from inventory.models import ProductCategory
from pos.tests import PosBase


class BiTests(PosBase):
    def setUp(self):
        super().setUp()
        self.cat = ProductCategory.objects.create(name="Tyres")
        self.p.category, self.p.cost = self.cat, Decimal("60")
        self.p.save()
        self.sell(2, pay=[{"method": "cash", "amount": 200}])
        self.client.login(username="boss", password="x")

    def test_sales_filters_and_kpis(self):
        r = self.client.get("/bi/", {"category": self.cat.pk, "min_price": 50, "max_price": 150, "q": "tyre"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["kpi"]["total"], Decimal("200.00"))
        self.assertEqual(r.context["kpi"]["orders"], 1)
        self.assertEqual(r.context["kpi"]["cost"], Decimal("120"))
        none = self.client.get("/bi/", {"min_price": 500})
        self.assertEqual(none.context["kpi"]["total"], 0)

    def test_exports_and_stock_page(self):
        self.assertIn("text/csv", self.client.get("/bi/?export=csv")["Content-Type"])
        r = self.client.get("/bi/stock/", {"category": self.cat.pk})
        self.assertEqual(r.context["count"], 1)
        self.assertEqual(r.context["value"], Decimal("480"))  # 8 left x cost 60
        self.assertEqual(self.client.get("/bi/stock/?view=zero").context["count"], 0)

    def test_managers_only(self):
        self.client.login(username="till1", password="x")
        self.assertEqual(self.client.get("/bi/").status_code, 302)


class InsightTests(PosBase):
    def setUp(self):
        super().setUp()
        self.client.login(username="boss", password="x")

    def test_fast_seller_is_flagged_for_reorder_and_draft_created(self):
        from . import insights
        from inventory.models import Picking
        self.sell(8, pay=[{"method": "cash", "amount": 800}])         # 2 left, sold 8 in 30 days
        rows = insights.reorder_suggestions()
        self.assertEqual(rows[0]["p"], self.p)
        self.assertGreater(rows[0]["qty"], 0)
        before = self.p.on_hand
        r = self.client.post("/bi/draft-order/", {"pick": [self.p.pk]})
        self.assertEqual(r.status_code, 302)
        pk = Picking.objects.get(origin="Auto reorder")
        self.assertEqual(pk.state, Picking.DRAFT)
        self.assertEqual(self.p.on_hand, before)  # nothing moves until validated

    def test_pages_render_and_forecast_has_seven_days(self):
        from . import insights
        self.sell(1, pay=[{"method": "cash", "amount": 100}])
        hist, future = insights.forecast()
        self.assertEqual(len(future), 7)
        for u in ["/bi/insights/", "/bi/", "/bi/stock/"]:
            self.assertEqual(self.client.get(u).status_code, 200, u)

    def test_cash_shortage_is_flagged(self):
        from . import insights
        from pos import services
        services.close_session(self.session, 10, self.cashier)   # float was 100, so 90 short
        self.assertTrue(any("short" in f for f in insights.anomalies()))

    def ask(self, q):
        import json
        return self.client.post("/bi/ask/", json.dumps({"question": q}), content_type="application/json").json()

    def test_questions_are_answered_from_the_database_without_a_key(self):
        self.sell(3, pay=[{"method": "cash", "amount": 300}])
        a = self.ask("sales today")
        self.assertIn("300.00", a["answer"])
        self.assertEqual(a["source"], "database")
        a = self.ask("how many tyre do we have")
        self.assertEqual(a["table"]["rows"][0][2], "7")
        self.assertEqual(self.ask("price of tyre")["table"]["rows"][0][2], "100.00")
        self.assertEqual(self.ask("top products this month")["table"]["rows"][0][1], "Tyre")
        self.assertEqual(self.ask("sales by cashier")["table"]["rows"][0][0], "till1")
        self.assertIn("stock", self.ask("stock value")["answer"].lower())
        for q in ["refunds this week", "payment methods today", "forecast next week", "dead stock", "what should I reorder", "out of stock", "cash short last week", "zra status"]:
            self.assertNotIn("error", self.ask(q), q)

    def test_unknown_question_gets_help_and_ai_is_only_a_fallback(self):
        from unittest import mock
        from django.test import override_settings
        r = self.ask("qwertyuiop zzzz")
        self.assertIn("error", r)
        self.assertTrue(r["examples"])
        with override_settings(AI_API_KEY="k"), mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.read.return_value = b'{"content":[{"type":"text","text":"Stock tyres."}]}'
            self.assertEqual(self.ask("qwertyuiop zzzz")["answer"], "Stock tyres.")
            self.assertNotIn("till1", u.call_args[0][0].data.decode())
            self.assertEqual(self.ask("sales today")["source"], "database")  # local answer wins, nothing sent
