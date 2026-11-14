import json
import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings

from inventory.models import Warehouse, ZraConfig
from inventory.tests import Base

from . import services
from .models import PosOrder, PosSession
from .services import PosError


class PosBase(Base):
    def setUp(self):
        super().setUp()
        U = get_user_model()
        self.boss = U.objects.create_user("boss", password="x", is_staff=True)
        self.cashier = U.objects.create_user("till1", password="x")
        self.cashier.groups.add(Group.objects.get(name="Cashier"))
        self.wh = Warehouse.objects.first()
        self.receive(10)
        self.session = services.open_session(self.cashier, self.wh, 100)

    def sell(self, qty=2, pay=None, **kw):
        lines = [{"product": self.p.pk, "qty": qty, **kw}]
        pay = pay if pay is not None else [{"method": "cash", "amount": 1000}]
        return services.create_sale(self.session, self.cashier, lines, pay)


class SaleTests(PosBase):
    def test_sale_deducts_stock_and_computes_vat(self):
        order, created = self.sell(2)
        self.assertTrue(created)
        self.assertEqual(self.p.on_hand, 8)
        self.assertEqual(order.total, Decimal("200.00"))
        self.assertEqual(order.tax_total, Decimal("27.59"))  # 16% inside a VAT-inclusive price
        self.assertEqual(order.change_due, Decimal("800.00"))
        self.assertEqual(order.picking.origin, order.number)

    def test_same_uuid_twice_sells_once(self):
        u = uuid.uuid4()
        lines, pay = [{"product": self.p.pk, "qty": 1}], [{"method": "cash", "amount": 100}]
        a, c1 = services.create_sale(self.session, self.cashier, lines, pay, client_uuid=u)
        b, c2 = services.create_sale(self.session, self.cashier, lines, pay, client_uuid=u)
        self.assertEqual((a.pk, c1, c2), (b.pk, True, False))
        self.assertEqual(self.p.on_hand, 9)

    def test_short_payment_rejected_and_nothing_saved(self):
        with self.assertRaises(PosError):
            self.sell(2, pay=[{"method": "cash", "amount": 50}])
        self.assertEqual(PosOrder.objects.count(), 0)
        self.assertEqual(self.p.on_hand, 10)

    def test_card_cannot_be_overpaid(self):
        with self.assertRaises(PosError):
            self.sell(1, pay=[{"method": "card", "amount": 150}])

    def test_cashier_discount_cap_but_manager_can(self):
        with self.assertRaises(PosError):
            self.sell(1, discount=50)
        order, _ = services.create_sale(self.session, self.boss, [{"product": self.p.pk, "qty": 1, "discount": 50}],
                                        [{"method": "cash", "amount": 50}])
        self.assertEqual(order.total, Decimal("50.00"))

    def test_cashier_cannot_override_price(self):
        order, _ = self.sell(1, price=1)
        self.assertEqual(order.total, Decimal("100.00"))

    def test_secondary_price_tier(self):
        self.p.secondary_price = 80
        self.p.save()
        order, _ = services.create_sale(self.session, self.cashier, [{"product": self.p.pk, "qty": 1}],
                                        [{"method": "cash", "amount": 80}], tier="secondary")
        self.assertEqual(order.total, Decimal("80.00"))

    @override_settings(ALLOW_NEGATIVE_STOCK=False)
    def test_cannot_oversell_when_negative_stock_is_off(self):
        with self.assertRaises(PosError):
            self.sell(11, pay=[{"method": "cash", "amount": 5000}])
        self.assertEqual(PosOrder.objects.count(), 0)

    def test_closed_session_blocks_sales(self):
        services.close_session(self.session, 100, self.cashier)
        with self.assertRaises(PosError):
            self.sell(1)


class RefundAndCashUp(PosBase):
    def test_refund_restores_stock_once(self):
        order, _ = self.sell(3, pay=[{"method": "cash", "amount": 300}])
        self.assertEqual(self.p.on_hand, 7)
        services.open_session(self.boss, self.wh)
        r = services.refund_order(order, self.boss)
        self.assertEqual(self.p.on_hand, 10)
        self.assertEqual(r.total, Decimal("-300.00"))
        with self.assertRaises(PosError):
            services.refund_order(order, self.boss)

    def test_cashier_cannot_refund(self):
        order, _ = self.sell(1, pay=[{"method": "cash", "amount": 100}])
        with self.assertRaises(PosError):
            services.refund_order(order, self.cashier)

    def test_cash_up_counts_change_and_refunds(self):
        self.sell(1, pay=[{"method": "cash", "amount": 200}])        # +100 net (200 handed, 100 change)
        self.sell(1, pay=[{"method": "card", "amount": 100}])        # card, no cash
        s = services.session_summary(self.session)
        self.assertEqual(s["expected_cash"], Decimal("200.00"))      # 100 float + 100
        self.assertEqual(s["by_method"]["card"], Decimal("100.00"))
        services.close_session(self.session, 195, self.cashier)
        self.session.refresh_from_db()
        self.assertEqual(self.session.difference, Decimal("-5.00"))


class ZraTests(PosBase):
    def test_payload_matches_vsdc_shape(self):
        cfg = ZraConfig.get()
        cfg.tpin, cfg.branch_id = "1000000000", "000"
        cfg.save()
        order, _ = self.sell(2)
        p = services.build_sale_payload(order, cfg)
        self.assertEqual((p["rcptTyCd"], p["salesTyCd"], p["totAmt"]), ("S", "N", 200.0))
        self.assertEqual(p["itemList"][0]["itemClsCd"], "5059690800")
        self.assertEqual(p["taxAmtA"], float(order.tax_total))
        self.assertEqual(p["taxRtA"], 16)

    def test_bad_class_code_is_blocked_before_sending(self):
        cfg = ZraConfig.get()
        cfg.enabled, cfg.tpin, cfg.branch_id = True, "1000000000", "000"
        cfg.save()
        self.p.zra_item_class_code = "123"
        self.p.save()
        order, _ = self.sell(1, pay=[{"method": "cash", "amount": 100}])
        with mock.patch("inventory.services.zra.post") as post:
            self.assertFalse(services.submit_to_zra(order))
            post.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.zra_status, "failed")

    def test_success_marks_sent(self):
        cfg = ZraConfig.get()
        cfg.enabled, cfg.tpin, cfg.branch_id = True, "1000000000", "000"
        cfg.save()
        order, _ = self.sell(1, pay=[{"method": "cash", "amount": 100}])
        with mock.patch("inventory.services.zra.post", return_value=(True, "ok")):
            self.assertTrue(services.submit_to_zra(order))
        order.refresh_from_db()
        self.assertEqual(order.zra_status, "sent")


class ViewAndSync(PosBase):
    def test_sale_endpoint_and_permissions(self):
        self.client.login(username="till1", password="x")
        body = {"uuid": str(uuid.uuid4()), "session": str(self.session.uuid),
                "lines": [{"product": self.p.pk, "qty": 1}], "payments": [{"method": "cash", "amount": 100}]}
        r = self.client.post("/pos/sale/", json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        r2 = self.client.post("/pos/sale/", json.dumps(body), content_type="application/json")
        self.assertFalse(r2.json()["created"])
        self.assertEqual(self.client.get("/pos/report/").status_code, 302)  # managers only
        self.assertEqual(self.client.get("/pos/").status_code, 200)

    def test_viewer_without_cashier_group_cannot_sell(self):
        get_user_model().objects.create_user("viewer", password="x")
        self.client.login(username="viewer", password="x")
        self.assertEqual(self.client.get("/pos/").status_code, 302)

    def test_pages_render(self):
        order, _ = self.sell(1, pay=[{"method": "cash", "amount": 100}])
        self.client.login(username="boss", password="x")
        for url in ["/pos/", "/pos/session/open/", "/pos/orders/", f"/pos/orders/{order.pk}/", f"/pos/orders/{order.pk}/receipt/",
                    "/pos/sessions/", "/pos/report/", "/pos/catalog.json", "/", "/inventory/"]:
            self.assertIn(self.client.get(url).status_code, (200, 302), url)

    def test_orders_copy_to_another_site_without_touching_stock(self):
        order, _ = self.sell(2)
        row = services.order_to_dict(order)
        row["uuid"], row["number"], row["site"] = str(uuid.uuid4()), "BR2/POS/000001", "BR2"
        before = self.p.on_hand
        self.assertEqual(services.import_remote_orders([row]), 1)
        self.assertEqual(services.import_remote_orders([row]), 0)
        self.assertEqual(self.p.on_hand, before)
        self.assertTrue(PosOrder.objects.get(number="BR2/POS/000001").remote)


class ProfitTests(PosBase):
    def test_line_keeps_the_cost_at_the_time_of_sale(self):
        self.p.cost = Decimal("60")
        self.p.save()
        order, _ = self.sell(2)
        self.p.cost = Decimal("90")  # cost changes later; the old sale must not
        self.p.save()
        line = order.lines.get()
        self.assertEqual(line.unit_cost, Decimal("60.00"))
        boss = self.boss
        self.client.force_login(boss)
        r = self.client.get("/bi/sales/")
        self.assertEqual(r.status_code, 200)
        # 200 incl. VAT, VAT 27.59, cost 2 x 60 = 120 -> margin 52.41
        self.assertEqual(r.context["kpi"]["cost"], Decimal("120.00"))
        self.assertEqual(r.context["kpi"]["margin"], Decimal("52.41"))

    def test_refund_reverses_the_same_cost(self):
        self.p.cost = Decimal("60")
        self.p.save()
        order, _ = self.sell(1)
        services.open_session(self.boss, self.wh, 0)
        refund = services.refund_order(order, self.boss, "test")
        self.assertEqual(refund.lines.get().unit_cost, Decimal("60.00"))


class ReceivablesTests(PosBase):
    def setUp(self):
        super().setUp()
        from inventory.models import Partner
        self.cust = Partner.objects.create(name="Kabwe Haulage", is_customer=True)
        self.client.force_login(self.boss)

    def on_account(self, qty=1, customer=None):
        return services.create_sale(self.session, self.cashier, [{"product": self.p.pk, "qty": qty}],
                                    [{"method": "credit", "amount": 100 * qty}], customer=customer or self.cust)

    def test_credit_sale_needs_a_customer_record(self):
        with self.assertRaises(PosError):
            services.create_sale(self.session, self.cashier, [{"product": self.p.pk, "qty": 1}],
                                 [{"method": "credit", "amount": 100}], customer_name="Someone")

    def test_balance_payment_and_refund(self):
        from . import receivables
        self.on_account(2)
        self.assertEqual(receivables.balance(self.cust), Decimal("200.00"))
        receivables.record_payment(self.cust, 50, "cash", self.boss)
        self.assertEqual(receivables.balance(self.cust), Decimal("150.00"))
        order, _ = self.on_account(1)
        services.open_session(self.boss, self.wh, 0)
        services.refund_order(order, self.boss, "returned")
        self.assertEqual(receivables.balance(self.cust), Decimal("150.00"))
        rows = receivables.statement(self.cust)
        self.assertEqual(rows[-1]["balance"], Decimal("150.00"))

    def test_overpaying_is_refused(self):
        from . import receivables
        self.on_account(1)
        with self.assertRaises(PosError):
            receivables.record_payment(self.cust, 500, "cash", self.boss)

    def test_credit_limit_stops_the_sale_and_nothing_is_saved(self):
        self.cust.credit_limit = Decimal("150")
        self.cust.save()
        self.on_account(1)
        before = PosOrder.objects.count()
        with self.assertRaises(PosError):
            self.on_account(1)
        self.assertEqual(PosOrder.objects.count(), before)
        self.assertEqual(self.p.on_hand, 9)

    def test_payments_clear_the_oldest_sale_first_for_aging(self):
        from datetime import timedelta
        from django.utils import timezone
        from . import receivables
        old, _ = self.on_account(1)
        PosOrder.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=70))
        self.on_account(1)
        receivables.record_payment(self.cust, 100, "cash", self.boss)
        buckets = {a["label"]: a["amount"] for a in receivables.aging(self.cust)}
        self.assertEqual(buckets["61-90 days"], Decimal("0"))
        self.assertEqual(buckets["0-30 days"], Decimal("100.00"))

    def test_screens_and_payment_form(self):
        self.on_account(1)
        self.assertEqual(self.client.get("/pos/customers/").status_code, 200)
        url = f"/pos/customers/{self.cust.pk}/"
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"action": "payment", "amount": "40", "method": "mobile", "reference": "M123"})
        self.assertEqual(r.status_code, 302)
        from . import receivables
        self.assertEqual(receivables.balance(self.cust), Decimal("60.00"))
        self.client.logout()
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get("/pos/customers/").status_code, 302)
