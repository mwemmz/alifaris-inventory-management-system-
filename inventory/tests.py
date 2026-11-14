import io
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import (ApiToken, LandedCost, Location, OperationType, Picking, Product, StockMove, StockQuant, UoM,
                     ZraConfig, ZraLog)
from .services import importer, stock, zra
from .services.stock import StockError


class Base(TestCase):
    def setUp(self):
        call_command("seed_inventory", stdout=io.StringIO())
        self.stock = Location.objects.get(name="Stock")
        self.vend = Location.objects.get(usage=Location.SUPPLIER)
        self.uom = UoM.objects.get(name="Units")
        self.p = Product.objects.create(name="Tyre", internal_ref="T1", price=100, uom=self.uom,
                                        zra_item_class_code="5059690800")

    def receive(self, qty, cost=0, product=None):
        op = OperationType.objects.get(code="receipt")
        pk = stock.new_picking(op, [(product or self.p, qty, cost)])
        return stock.validate_picking(pk)


class StockEngine(Base):
    def test_receipt_then_delivery_updates_on_hand(self):
        self.receive(10)
        self.assertEqual(self.p.on_hand, 10)
        op = OperationType.objects.get(code="delivery")
        stock.validate_picking(stock.new_picking(op, [(self.p, 4)]))
        self.assertEqual(self.p.on_hand, 6)
        self.assertEqual(StockMove.objects.count(), 2)

    def test_validate_twice_is_refused(self):
        pk = stock.new_picking(OperationType.objects.get(code="receipt"), [(self.p, 5)])
        stock.validate_picking(pk)
        with self.assertRaises(StockError):
            stock.validate_picking(pk)
        self.assertEqual(self.p.on_hand, 5)

    def test_average_cost(self):
        self.receive(10, cost=100)
        self.receive(10, cost=200)
        self.p.refresh_from_db()
        self.assertEqual(self.p.cost, Decimal("150.00"))

    @override_settings(ALLOW_NEGATIVE_STOCK=False)
    def test_negative_stock_can_be_blocked(self):
        op = OperationType.objects.get(code="delivery")
        with self.assertRaises(StockError):
            stock.validate_picking(stock.new_picking(op, [(self.p, 1)]))
        self.assertEqual(self.p.on_hand, 0)

    def test_scrap_and_count(self):
        from .models import Scrap
        self.receive(10)
        s = Scrap.objects.create(product=self.p, location=self.stock, qty=2, reason="Damaged")
        stock.do_scrap(s)
        self.assertEqual(self.p.on_hand, 8)
        stock.apply_count(self.stock, {self.p.pk: Decimal("5")})
        self.assertEqual(self.p.on_hand, 5)
        stock.apply_count(self.stock, {self.p.pk: Decimal("9")})
        self.assertEqual(self.p.on_hand, 9)

    def test_landed_cost_raises_cost(self):
        pick = self.receive(10, cost=100)
        lc = LandedCost.objects.create(picking=pick, description="Freight", amount=Decimal("100"))
        stock.apply_landed_cost(lc)
        self.p.refresh_from_db()
        self.assertEqual(self.p.cost, Decimal("110.00"))
        with self.assertRaises(StockError):
            stock.apply_landed_cost(lc)

    def test_replenishment_and_valuation(self):
        self.p.min_qty, self.p.max_qty, self.p.cost = 5, 20, 10
        self.p.save()
        self.receive(2)
        self.assertEqual(stock.replenishment_candidates()[0]["to_order"], 18)
        self.assertEqual(stock.stock_valuation()[0]["value"], 20)

    def test_move_uuid_is_idempotent(self):
        import uuid
        u = uuid.uuid4()
        stock.apply_move(self.p, self.vend, self.stock, 3, uuid=u)
        _, created = stock.apply_move(self.p, self.vend, self.stock, 3, uuid=u)
        self.assertFalse(created)
        self.assertEqual(self.p.on_hand, 3)


class PosApi(Base):
    def setUp(self):
        super().setUp()
        self.tok = ApiToken.objects.create(name="POS")
        self.h = {"HTTP_AUTHORIZATION": f"Token {self.tok.key}"}
        self.receive(10)

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json", **self.h)

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/v1/products/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/products/", HTTP_AUTHORIZATION="Token nope").status_code, 401)

    def test_products_and_stock(self):
        r = self.client.get("/api/v1/products/?q=T1", **self.h).json()
        self.assertEqual(r["products"][0]["on_hand"], "10.000")
        self.assertEqual(self.client.get("/api/v1/stock/?ref=T1", **self.h).json()["on_hand"], "10.000")

    def test_sale_deducts_once(self):
        body = {"external_ref": "POS-1", "lines": [{"ref": "T1", "qty": 3}]}
        r1 = self.post("/api/v1/sales/", body)
        r2 = self.post("/api/v1/sales/", body)  # POS retries after a timeout
        self.assertEqual(r1.status_code, 201)
        self.assertTrue(r2.json()["duplicate"])
        self.assertEqual(self.p.on_hand, 7)

    def test_bad_sale_changes_nothing(self):
        r = self.post("/api/v1/sales/", {"external_ref": "POS-2", "lines": [{"ref": "T1", "qty": 1}, {"ref": "NOPE", "qty": 1}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.p.on_hand, 10)
        self.assertFalse(Picking.objects.filter(origin="POS-2").exists())

    def test_return_puts_stock_back(self):
        self.post("/api/v1/sales/", {"external_ref": "POS-3", "lines": [{"ref": "T1", "qty": 4}]})
        self.post("/api/v1/returns/", {"external_ref": "RET-3", "lines": [{"ref": "T1", "qty": 1}]})
        self.assertEqual(self.p.on_hand, 7)

    def test_sync_round_trip(self):
        out = self.client.get("/api/v1/sync/moves/", **self.h).json()["moves"]
        self.assertEqual(len(out), 1)
        StockMove.objects.all().delete()
        StockQuant.objects.all().delete()
        r = self.post("/api/v1/sync/moves/", {"moves": out}).json()
        self.assertEqual(r["applied"], 1)
        r = self.post("/api/v1/sync/moves/", {"moves": out}).json()
        self.assertEqual(r["applied"], 0)
        self.assertEqual(self.p.on_hand, 10)


class ZraTests(Base):
    def setUp(self):
        super().setUp()
        cfg = ZraConfig.get()
        cfg.enabled, cfg.tpin = True, "1003845799"
        cfg.save()

    def test_bad_class_code_is_caught_before_sending(self):
        self.p.zra_item_class_code = "12345"
        self.p.save()
        with mock.patch("urllib.request.urlopen") as net:
            ok, msg = zra.register_product(self.p)
        net.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("10 characters", msg)
        self.assertEqual(ZraLog.objects.get().status, ZraLog.ERROR)

    def test_success_marks_registered(self):
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = b'{"resultCd":"000","resultMsg":"It is succeeded"}'
        with mock.patch("urllib.request.urlopen", return_value=fake):
            ok, _ = zra.register_product(self.p)
        self.assertTrue(ok)
        self.p.refresh_from_db()
        self.assertTrue(self.p.zra_registered)

    def test_vsdc_error_and_unreachable_are_logged(self):
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = b'{"resultCd":"902","resultMsg":"Invalid Item Class"}'
        with mock.patch("urllib.request.urlopen", return_value=fake):
            ok, msg = zra.register_product(self.p)
        self.assertFalse(ok)
        self.assertEqual(msg, "Invalid Item Class")
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            ok, msg = zra.register_product(self.p)
        self.assertIn("Could not reach", msg)
        self.assertEqual(ZraLog.objects.filter(status="error").count(), 2)

    def test_disabled(self):
        cfg = ZraConfig.get()
        cfg.enabled = False
        cfg.save()
        with self.assertRaises(zra.ZraDisabled):
            zra.register_product(self.p)


class ImportTests(Base):
    def test_create_update_and_errors(self):
        csv_text = ("internal_ref,name,category,uom,price,secondary_price,tax_type,zra_item_class_code,opening_stock\n"
                    "A1,Wheel,Tyres,Units,500,525,A,5059690800,7\n"
                    ",No ref,,,1,1,A,,\n"
                    "A2,Bad tax,,Units,5,5,Z,,\n")
        rows = importer.read_rows(SimpleUploadedFile("x.csv", csv_text.encode()))
        res = importer.import_rows(rows, "create")
        self.assertEqual((res["created"], len(res["errors"])), (1, 2))
        self.assertEqual(Product.objects.get(internal_ref="A1").category.name, "Tyres")
        res = importer.import_rows(importer.read_rows(SimpleUploadedFile("x.csv", b"internal_ref,price\nA1,600\n")), "update")
        self.assertEqual(res["updated"], 1)
        self.assertEqual(Product.objects.get(internal_ref="A1").price, 600)
        res = importer.import_rows(importer.read_rows(SimpleUploadedFile("x.csv", b"internal_ref,name\nA1,Dup\n")), "create")
        self.assertEqual(len(res["errors"]), 1)


class Screens(Base):
    def setUp(self):
        super().setUp()
        self.u = get_user_model().objects.create_user("boss", password="pw", is_staff=True)
        self.client.login(username="boss", password="pw")
        self.receive(5)

    def test_every_screen_renders(self):
        urls = ["/", "/inventory/", "/inventory/products/", "/inventory/products/?view=list", "/inventory/products/new/",
                f"/inventory/products/{self.p.pk}/", "/inventory/operations/receipts/", "/inventory/operations/deliveries/",
                "/inventory/operations/internal/", "/inventory/operations/receipts/new/", "/inventory/physical-inventory/",
                "/inventory/scrap/", "/inventory/scrap/new/", "/inventory/landed-costs/", "/inventory/landed-costs/new/",
                "/inventory/replenishment/", "/inventory/reporting/stock/", "/inventory/reporting/locations/",
                "/inventory/reporting/moves/", "/inventory/reporting/analysis/", "/inventory/reporting/valuation/",
                "/inventory/config/warehouses/", "/inventory/config/locations/", "/inventory/config/categories/",
                "/inventory/config/uoms/", "/inventory/config/uom-categories/", "/inventory/config/partners/",
                "/inventory/config/uoms/new/", "/inventory/config/zra/", "/inventory/config/api-tokens/",
                "/inventory/import/", "/inventory/import/template.csv", "/inventory/zra-logs/", "/inventory/scan/",
                "/manifest.webmanifest", "/sw.js", "/offline/", f"/inventory/transfer/{Picking.objects.first().pk}/"]
        for u in urls:
            r = self.client.get(u)
            self.assertEqual(r.status_code, 200, u)

    def test_create_receipt_through_the_form_and_validate(self):
        op = OperationType.objects.get(code="receipt")
        data = {"partner": "", "source": op.default_source.pk, "dest": op.default_dest.pk,
                "scheduled_date": "2026-11-13T10:00", "origin": "", "notes": "",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-product": self.p.pk, "lines-0-qty": "12", "lines-0-unit_cost": "80"}
        r = self.client.post("/inventory/operations/receipts/new/", data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        pk = Picking.objects.latest("id")
        self.client.post(f"/inventory/transfer/{pk.pk}/validate/")
        self.assertEqual(self.p.on_hand, 17)

    def test_non_staff_cannot_reach_config_or_import(self):
        get_user_model().objects.create_user("clerk", password="pw")
        self.client.logout()
        self.client.login(username="clerk", password="pw")
        self.assertEqual(self.client.get("/inventory/config/zra/").status_code, 302)
        self.assertEqual(self.client.get("/inventory/import/").status_code, 302)
        self.assertEqual(self.client.get("/inventory/products/").status_code, 200)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get("/inventory/").status_code, 302)
        self.assertEqual(self.client.get("/inventory/lookup/?code=T1").status_code, 302)


class Roles(Base):
    def setUp(self):
        super().setUp()
        U = get_user_model()
        U.objects.create_user("viewer", password="pw")
        k = U.objects.create_user("keeper", password="pw")
        from django.contrib.auth.models import Group
        k.groups.add(Group.objects.get(name="Storekeeper"))
        wm = U.objects.create_user("wh", password="pw")
        wm.groups.add(Group.objects.get(name="Warehouse Manager"))
        self.receive(5)

    def login(self, name):
        self.client.logout()
        self.client.login(username=name, password="pw")

    def test_viewer_can_look_but_not_change(self):
        self.login("viewer")
        self.assertEqual(self.client.get("/inventory/products/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/operations/receipts/new/").status_code, 302)
        pk = Picking.objects.create(op_type=OperationType.objects.get(code="receipt"), source=self.vend, dest=self.stock)
        stock.new_picking  # noqa
        self.client.post(f"/inventory/transfer/{pk.pk}/validate/")
        self.assertEqual(Picking.objects.get(pk=pk.pk).state, Picking.DRAFT)
        r = self.client.post(f"/inventory/products/{self.p.pk}/", {"name": "Hacked", "internal_ref": "T1"})
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual(self.p.name, "Tyre")
        r = self.client.post("/inventory/physical-inventory/", {"location": self.stock.pk, f"count_{self.p.pk}": "99"})
        self.assertEqual(self.p.on_hand, 5)

    def test_warehouse_manager_scope(self):
        self.login("wh")
        # can operate like a storekeeper
        self.assertEqual(self.client.get("/inventory/operations/receipts/new/").status_code, 200)
        # has the warehouse screen, stock configuration and import
        self.assertEqual(self.client.get("/inventory/warehouse/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/config/locations/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/config/warehouses/new/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/import/").status_code, 200)
        self.assertEqual(self.client.get("/bi/stock/").status_code, 200)
        self.assertEqual(self.client.get("/bi/insights/").status_code, 200)
        # but not ZRA settings, tokens, site sync, sync peers or sales reports
        for url in ["/inventory/config/zra/", "/inventory/config/api-tokens/", "/inventory/config/sync/",
                    "/inventory/config/sync-peers/"]:
            self.assertNotEqual(self.client.get(url).status_code, 200, url)
        for url in ["/bi/", "/bi/sales/"]:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_warehouse_screen_closed_to_others(self):
        for name in ["viewer", "keeper"]:
            self.login(name)
            self.assertEqual(self.client.get("/inventory/warehouse/").status_code, 302, name)
            self.assertEqual(self.client.get("/inventory/config/locations/").status_code, 302, name)

    def test_storekeeper_can_operate_but_not_configure(self):
        self.login("keeper")
        pk = Picking.objects.create(op_type=OperationType.objects.get(code="receipt"), source=self.vend, dest=self.stock)
        from .models import PickingLine
        PickingLine.objects.create(picking=pk, product=self.p, qty=3)
        self.client.post(f"/inventory/transfer/{pk.pk}/validate/")
        self.assertEqual(self.p.on_hand, 8)
        self.assertEqual(self.client.get("/inventory/config/zra/").status_code, 302)


class SiteSync(Base):
    def peer(self):
        from .models import SyncPeer
        return SyncPeer.objects.create(name="HQ", url="http://hq.test", token="k")

    def test_catalogue_and_moves_are_exchanged(self):
        import uuid as _uuid
        from .services import sync
        move = {"uuid": str(_uuid.uuid4()), "ref": "NEW1", "source": "Vendors", "dest": "SHOP/Stock", "qty": "5",
                "unit_cost": "0", "date": "2026-11-13T10:00:00+02:00", "reason": "Receipt", "site": "HQ1",
                "recorded_at": "2026-11-13T10:00:01+02:00"}
        product = {"ref": "NEW1", "name": "From HQ", "barcode": "", "price": "10.00", "secondary_price": "11.00",
                   "tax_type": "A", "type": "goods", "active": True, "cost": "5.00", "min_qty": "0", "max_qty": "0",
                   "uom": "Units", "zra_item_class_code": "5059690800", "zra_item_type": "2", "category": "Tyres"}
        sent = []

        def fake(method, url, token, body=None):
            if "/products/" in url:
                return {"products": [product]}
            if "/sync/orders/" in url:
                return {"orders": []} if method == "GET" else {"added": 0}
            if method == "GET":
                return {"moves": [move]} if "since" not in url else {"moves": []}
            sent.append(body)
            return {"applied": len(body["moves"])}

        self.receive(2)  # a local move to push
        peer = self.peer()
        with mock.patch.object(sync, "_http", fake):
            ok, msg = sync.sync_peer(peer)
            self.assertTrue(ok, msg)
            self.assertEqual(Product.objects.get(internal_ref="NEW1").on_hand, 5)
            self.assertEqual(len(sent[0]["moves"]), 1)
            ok, msg = sync.sync_peer(peer)  # second run: nothing new, nothing doubled
        self.assertTrue(ok, msg)
        self.assertEqual(Product.objects.get(internal_ref="NEW1").on_hand, 5)
        self.assertEqual(len(sent), 1)

    def test_failure_is_reported_and_keeps_progress(self):
        from .services import sync
        peer = self.peer()
        with mock.patch.object(sync, "_http", side_effect=sync.SyncError("Could not reach http://hq.test")):
            ok, msg = sync.sync_peer(peer)
        self.assertFalse(ok)
        peer.refresh_from_db()
        self.assertTrue(peer.last_result.startswith("FAILED"))

    def test_unknown_product_in_moves_fails_whole_batch(self):
        from .services import sync
        bad = {"uuid": "6f1f7b43-2e2a-4f4f-8d77-1d3a6a0e8b11", "ref": "GHOST", "source": "Vendors", "dest": "SHOP/Stock",
               "qty": "1", "date": "2026-11-13T10:00:00+02:00"}
        with self.assertRaises(Product.DoesNotExist):
            sync.apply_remote_moves([bad])
        self.assertEqual(StockMove.objects.count(), 0)


class HomeNotice(Base):
    def test_low_stock_notice_and_screen_loads(self):
        self.p.min_qty = 5
        self.p.save()
        u = get_user_model().objects.create_user("x", password="pw")
        self.client.login(username="x", password="pw")
        r = self.client.get("/")
        self.assertContains(r, "below their reorder minimum")


class DemoSeed(TestCase):
    def test_demo_data_builds_and_is_consistent(self):
        import os
        from unittest import mock
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from pos.models import PosOrder
        from .models import Product, StockQuant
        with mock.patch.dict(os.environ, {"ALIFARIS_DEMO_PASSWORD": ""}):
            with self.assertRaises(CommandError):
                call_command("seed_demo", days=2, verbosity=0)
        with mock.patch.dict(os.environ, {"ALIFARIS_DEMO_PASSWORD": "pw-for-tests-1"}):
            call_command("seed_demo", days=3, verbosity=0)
            self.assertTrue(Product.objects.count() >= 40)
            self.assertTrue(PosOrder.objects.filter(kind="sale").exists())
            self.assertFalse(StockQuant.objects.filter(qty__lt=0).exists())
            self.assertTrue(self.client.login(username="manager", password="pw-for-tests-1"))
            self.assertEqual(self.client.get("/bi/").status_code, 200)
            self.assertEqual(self.client.get("/inventory/warehouse/").status_code, 200)
            from .models import AuditLog, PurchaseOrder
            from pos import receivables
            self.assertEqual(set(PurchaseOrder.objects.values_list("state", flat=True)), {"received", "confirmed", "draft"})
            self.assertTrue(receivables.summary())
            self.assertTrue(AuditLog.objects.exists())
            for url in ("/pos/customers/", "/inventory/purchase/", "/inventory/audit/", "/inventory/data/"):
                self.assertEqual(self.client.get(url).status_code, 200, url)
            n = Product.objects.count()
            call_command("seed_demo", days=3, verbosity=0)  # second run must not duplicate anything
            self.assertEqual(Product.objects.count(), n)

    def test_health_check_needs_no_login(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)


class AuditAndUsers(Base):
    def setUp(self):
        super().setUp()
        U = get_user_model()
        self.boss = U.objects.create_user("boss", password="pw", is_staff=True)
        U.objects.create_user("viewer", password="pw")
        self.client.force_login(self.boss)

    def test_product_change_is_logged_with_old_and_new_price(self):
        from .models import AuditLog
        self.client.post(f"/inventory/products/{self.p.pk}/", {"name": "Tyre", "internal_ref": "T1", "product_type": "goods",
                                                               "price": "250", "cost": "0", "tax_type": "A", "zra_item_type": "2",
                                                               "active": "on", "min_qty": "0", "max_qty": "0", "secondary_price": "0"})
        e = AuditLog.objects.filter(action="product changed").first()
        self.assertIsNotNone(e)
        self.assertEqual(e.username, "boss")
        self.assertIn("price", e.detail)

    def test_stock_validation_and_failed_login_are_logged(self):
        from .models import AuditLog
        self.receive(3)
        pk = Picking.objects.create(op_type=OperationType.objects.get(code="receipt"), source=self.vend, dest=self.stock)
        from .models import PickingLine
        PickingLine.objects.create(picking=pk, product=self.p, qty=2)
        self.client.post(f"/inventory/transfer/{pk.pk}/validate/")
        self.assertTrue(AuditLog.objects.filter(action="stock validated").exists())
        self.client.logout()
        self.client.post("/login/", {"username": "boss", "password": "wrong"})
        self.assertTrue(AuditLog.objects.filter(action="failed login", subject="boss").exists())

    def test_audit_screen_is_for_managers_only(self):
        self.assertEqual(self.client.get("/inventory/audit/").status_code, 200)
        self.client.force_login(get_user_model().objects.get(username="viewer"))
        self.assertEqual(self.client.get("/inventory/audit/").status_code, 302)
        self.assertEqual(self.client.get("/inventory/users/").status_code, 302)

    def test_create_user_with_role_and_change_it(self):
        from django.contrib.auth.models import Group
        r = self.client.post("/inventory/users/new/", {"username": "mary", "first_name": "Mary", "role": "cashier",
                                                       "active": "on", "password": "a-long-pass-9"})
        self.assertEqual(r.status_code, 302)
        u = get_user_model().objects.get(username="mary")
        self.assertTrue(u.check_password("a-long-pass-9"))
        self.assertTrue(u.groups.filter(name="Cashier").exists())
        self.client.post(f"/inventory/users/{u.pk}/", {"username": "mary", "role": "warehouse", "active": "on"})
        u.refresh_from_db()
        self.assertFalse(u.groups.filter(name="Cashier").exists())
        self.assertTrue(u.groups.filter(name="Warehouse Manager").exists())
        self.assertFalse(u.is_staff)

    def test_cannot_lock_yourself_out_or_remove_last_manager(self):
        r = self.client.post(f"/inventory/users/{self.boss.pk}/", {"username": "boss", "role": "cashier", "active": "on"})
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_staff)
        weak = self.client.post("/inventory/users/new/", {"username": "x", "role": "viewer", "active": "on", "password": "123"})
        self.assertEqual(weak.status_code, 200)  # password rejected, form shown again
        self.assertFalse(get_user_model().objects.filter(username="x").exists())


class PurchaseAndData(Base):
    def setUp(self):
        super().setUp()
        from .models import Partner
        U = get_user_model()
        self.boss = U.objects.create_user("boss2", password="pw", is_staff=True)
        self.viewer = U.objects.create_user("viewer2", password="pw")
        self.supplier = Partner.objects.create(name="Lusaka Tyres Ltd", is_supplier=True, is_customer=False)
        self.client.force_login(self.boss)

    def _order(self, qty="10", cost="80"):
        from .models import PurchaseOrder, PurchaseOrderLine
        po = PurchaseOrder.objects.create(supplier=self.supplier, created_by=self.boss)
        PurchaseOrderLine.objects.create(order=po, product=self.p, qty=Decimal(qty), unit_cost=Decimal(cost))
        return po

    def test_order_number_and_total(self):
        po = self._order("10", "80")
        self.assertTrue(po.ref.startswith("PO"))
        self.assertEqual(po.total, Decimal("800"))

    def test_full_cycle_order_receive_validate_closes_order(self):
        from .models import PurchaseOrder
        po = self._order()
        self.client.post(f"/inventory/purchase/{po.pk}/confirm/")
        po.refresh_from_db()
        self.assertEqual(po.state, PurchaseOrder.CONFIRMED)
        r = self.client.post(f"/inventory/purchase/{po.pk}/receive/")
        po.refresh_from_db()
        self.assertIsNotNone(po.receipt)
        self.assertEqual(po.receipt.origin, po.ref)
        self.assertRedirects(r, f"/inventory/transfer/{po.receipt.pk}/")
        self.assertEqual(self.p.on_hand, 0)
        self.client.post(f"/inventory/transfer/{po.receipt.pk}/validate/")
        po.refresh_from_db()
        self.assertEqual(po.state, PurchaseOrder.RECEIVED)
        self.p.refresh_from_db()
        self.assertEqual(self.p.on_hand, 10)
        self.assertEqual(self.p.cost, 80)

    def test_receive_twice_does_not_create_two_receipts(self):
        from .models import Picking
        po = self._order()
        self.client.post(f"/inventory/purchase/{po.pk}/confirm/")
        self.client.post(f"/inventory/purchase/{po.pk}/receive/")
        self.client.post(f"/inventory/purchase/{po.pk}/receive/")
        self.assertEqual(Picking.objects.filter(origin=po.ref).count(), 1)

    def test_empty_order_cannot_be_placed(self):
        from .models import PurchaseOrder
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        self.client.post(f"/inventory/purchase/{po.pk}/confirm/")
        po.refresh_from_db()
        self.assertEqual(po.state, PurchaseOrder.DRAFT)

    def test_cancel_also_cancels_open_receipt(self):
        po = self._order()
        self.client.post(f"/inventory/purchase/{po.pk}/confirm/")
        self.client.post(f"/inventory/purchase/{po.pk}/receive/")
        self.client.post(f"/inventory/purchase/{po.pk}/cancel/")
        po.refresh_from_db()
        self.assertEqual(po.state, "cancel")
        self.assertEqual(po.receipt.state, Picking.CANCEL)

    def test_orders_from_replenishment(self):
        from .models import PurchaseOrder
        self.p.min_qty, self.p.max_qty = Decimal("5"), Decimal("20")
        self.p.save()
        r = self.client.post("/inventory/purchase/from-replenishment/", {"supplier": self.supplier.pk})
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.lines.get().qty, Decimal("20"))
        self.assertRedirects(r, f"/inventory/purchase/{po.pk}/")

    def test_viewer_cannot_open_purchasing_or_backup(self):
        self.client.force_login(self.viewer)
        self.assertNotEqual(self.client.get("/inventory/purchase/").status_code, 200)
        self.assertNotEqual(self.client.get("/inventory/data/backup/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/data/export/sales.xlsx").status_code, 404)

    def test_audit_trail_of_purchasing(self):
        from .models import AuditLog
        po = self._order()
        self.client.post(f"/inventory/purchase/{po.pk}/confirm/")
        self.assertTrue(AuditLog.objects.filter(action="purchase order placed", subject=po.ref).exists())

    def test_backup_command_copies_a_real_database_and_keeps_only_recent_files(self):
        import os
        import sqlite3
        import subprocess
        import sys
        import tempfile
        import time
        from pathlib import Path
        from django.conf import settings
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, ALIFARIS_DB_PATH=str(Path(d) / "live.sqlite3"), DJANGO_DEBUG="1")
            run = lambda *args: subprocess.run([sys.executable, "manage.py", *args], cwd=settings.BASE_DIR, env=env,
                                               capture_output=True, text=True, timeout=90)
            self.assertEqual(run("migrate", "--noinput").returncode, 0)
            self.assertEqual(run("seed_inventory").returncode, 0)
            for _ in range(3):
                r = run("backup_db", "--to", str(Path(d) / "bk"), "--keep", "2")
                self.assertEqual(r.returncode, 0, r.stderr)
                time.sleep(1.1)
            files = sorted((Path(d) / "bk").glob("*.sqlite3"))
            self.assertEqual(len(files), 2)
            con = sqlite3.connect(files[-1])
            self.assertGreater(con.execute("select count(*) from inventory_location").fetchone()[0], 0)
            con.close()

    def test_manager_downloads_backup_and_exports(self):
        import io as _io
        from openpyxl import load_workbook
        with mock.patch("inventory.services.backup.make_backup") as mk:
            import tempfile
            from pathlib import Path
            f = Path(tempfile.mkdtemp()) / "x.sqlite3"
            f.write_bytes(b"SQLite format 3\x00test")
            mk.return_value = f
            r = self.client.get("/inventory/data/backup/")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(b"".join(r.streaming_content).startswith(b"SQLite format 3"))
            self.assertIn("attachment", r["Content-Disposition"])
        for kind in ("products", "valuation", "moves", "purchases", "sales", "receivables"):
            r = self.client.get(f"/inventory/data/export/{kind}.xlsx")
            self.assertEqual(r.status_code, 200, kind)
            load_workbook(_io.BytesIO(r.content))
        ws = load_workbook(_io.BytesIO(self.client.get("/inventory/data/export/products.xlsx").content)).active
        self.assertEqual(ws["A1"].value, "Ref")
        self.assertEqual(ws.max_row, 2)
