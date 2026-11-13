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
