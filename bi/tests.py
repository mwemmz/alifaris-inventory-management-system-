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
