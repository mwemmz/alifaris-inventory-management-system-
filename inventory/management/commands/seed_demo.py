"""Fill an empty database with believable auto-parts demo data so every screen has something to show.

Everything here is made up: products, prices (kwacha), suppliers, customers and TPINs. Do not run it on a live shop
database; it refuses to run if products already exist unless --force is given.
"""
import os
import random
import zlib
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from inventory.models import (Location, OperationType, Partner, Product, ProductCategory, StockMove, UoM, Warehouse)
from inventory.services import stock
from pos import services as pos
from pos.models import PosPayment

# (ref, name, category, cost, price, on_hand, min, max, weight) ; weight = how often it sells (0 = never: slow stock)
PRODUCTS = [
    ("BRK-001", "Brake pads front, Toyota Corolla", "Brakes", 190, 320, 60, 20, 80, 9),
    ("BRK-002", "Brake pads rear, Toyota Corolla", "Brakes", 160, 270, 45, 15, 60, 6),
    ("BRK-003", "Brake disc front, Toyota Hilux", "Brakes", 420, 690, 24, 8, 30, 3),
    ("BRK-004", "Brake fluid DOT4 500 ml", "Brakes", 38, 65, 120, 30, 150, 7),
    ("FIL-001", "Oil filter, Toyota 1NZ/2NZ", "Filters", 45, 85, 150, 40, 180, 14),
    ("FIL-002", "Air filter, Nissan NP200", "Filters", 70, 125, 55, 20, 70, 8),
    ("FIL-003", "Fuel filter, Toyota Hilux diesel", "Filters", 95, 165, 14, 15, 50, 5),
    ("FIL-004", "Cabin filter, Mazda Demio", "Filters", 60, 110, 38, 12, 40, 3),
    ("OIL-001", "Engine oil 5W-30, 4 L", "Lubricants", 310, 470, 70, 24, 90, 12),
    ("OIL-002", "Engine oil 10W-40, 5 L", "Lubricants", 360, 540, 52, 20, 70, 9),
    ("OIL-003", "Gear oil 80W-90, 1 L", "Lubricants", 85, 140, 40, 15, 50, 4),
    ("OIL-004", "Coolant concentrate, 1 L", "Lubricants", 55, 95, 66, 20, 80, 6),
    ("ENG-001", "Spark plug set (4), NGK type", "Engine", 140, 240, 48, 15, 60, 8),
    ("ENG-002", "Timing belt kit, Toyota 1NZ", "Engine", 520, 860, 12, 6, 20, 2),
    ("ENG-003", "Fan belt, universal 6PK", "Engine", 75, 130, 35, 10, 45, 4),
    ("ENG-004", "Water pump, Toyota Hilux 2.7", "Engine", 640, 1040, 8, 5, 14, 1),
    ("ENG-005", "Radiator cap 1.1 bar", "Engine", 25, 50, 90, 20, 100, 3),
    ("SUS-001", "Shock absorber front, Toyota Corolla", "Suspension", 480, 780, 20, 8, 28, 3),
    ("SUS-002", "Shock absorber rear, Nissan NP200", "Suspension", 410, 670, 16, 8, 24, 2),
    ("SUS-003", "Ball joint lower, Toyota Hilux", "Suspension", 210, 350, 22, 8, 30, 2),
    ("SUS-004", "Stabiliser link, Mazda Demio", "Suspension", 90, 160, 30, 10, 40, 2),
    ("ELE-001", "Car battery 12 V 65 Ah", "Electrical", 1050, 1580, 18, 8, 24, 5),
    ("ELE-002", "Car battery 12 V 100 Ah", "Electrical", 1650, 2400, 10, 5, 14, 2),
    ("ELE-003", "Headlight bulb H4 (pair)", "Electrical", 55, 110, 80, 20, 100, 6),
    ("ELE-004", "Alternator, Toyota Hilux 2.4", "Electrical", 1900, 2850, 4, 3, 8, 1),
    ("ELE-005", "Wiper blades 22 in (pair)", "Electrical", 70, 125, 44, 12, 55, 5),
    ("TYR-001", "Tyre 185/65 R15", "Tyres and wheels", 880, 1250, 26, 12, 40, 4),
    ("TYR-002", "Tyre 205/70 R15, light truck", "Tyres and wheels", 1150, 1620, 18, 10, 30, 3),
    ("TYR-003", "Wheel nut, set of 20", "Tyres and wheels", 120, 210, 35, 10, 40, 2),
    ("TYR-004", "Tyre valve, pack of 10", "Tyres and wheels", 18, 40, 100, 20, 120, 3),
    ("BOD-001", "Side mirror right, Toyota Corolla", "Body", 230, 390, 9, 5, 14, 1),
    ("BOD-002", "Door handle outer, Nissan NP200", "Body", 85, 150, 18, 6, 24, 1),
    ("BOD-003", "Bumper clip set", "Body", 28, 55, 70, 15, 80, 2),
    ("BOD-004", "Mud flaps, universal pair", "Body", 110, 190, 20, 6, 28, 1),
    ("TRN-001", "Clutch kit, Toyota Hilux 2.4", "Transmission", 1450, 2180, 6, 4, 10, 1),
    ("TRN-002", "CV joint, Toyota Corolla", "Transmission", 360, 590, 11, 5, 16, 1),
    ("ACC-001", "Jumper cables 400 A", "Accessories", 120, 210, 25, 8, 30, 2),
    ("ACC-002", "Car jack 2 t hydraulic", "Accessories", 380, 620, 12, 5, 16, 1),
    ("ACC-003", "Tool kit 40 pieces", "Accessories", 260, 430, 9, 4, 12, 1),
    ("ACC-004", "Seat covers set, universal", "Accessories", 210, 360, 14, 5, 18, 0),   # slow stock
    ("ACC-005", "Steering wheel cover", "Accessories", 45, 85, 30, 8, 36, 0),            # slow stock
    ("ENG-006", "Head gasket, Toyota 4Y", "Engine", 330, 540, 0, 4, 10, 1),             # out of stock
]
SUPPLIERS = ["Copperbelt Auto Wholesale (demo)", "Lusaka Parts Importers (demo)", "Southern Africa Filters (demo)"]
CUSTOMERS = [("Kabwe Haulage (demo)", "1000000001"), ("Mwami Taxi Co-op (demo)", "1000000002"),
             ("Chilanga Garage (demo)", "1000000003"), ("Zambezi Logistics (demo)", "1000000004")]
USERS = [  # username, role
    ("manager", "manager"), ("warehouse", "warehouse"), ("keeper", "storekeeper"),
    ("cashier1", "cashier"), ("cashier2", "cashier"),
]


class Command(BaseCommand):
    help = "Create demo users, products, stock and 30 days of sales. For demos only."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even if products already exist")
        parser.add_argument("--days", type=int, default=30, help="How many days of sales to create")

    def handle(self, *args, **opts):
        if Product.objects.exists() and not opts["force"]:
            self.stdout.write("Products already exist, so demo data was not added. Use --force to add it anyway.")
            return
        password = os.environ.get("ALIFARIS_DEMO_PASSWORD") or ("alifaris-demo" if settings.DEBUG else "")
        if not password:
            raise CommandError("Set ALIFARIS_DEMO_PASSWORD (demo logins need a password).")
        rng = random.Random(2026)
        call_command("seed_inventory", verbosity=0)
        wh = Warehouse.objects.get(code="SHOP")
        users = self._users(password)
        cats = {n: ProductCategory.objects.get_or_create(name=n)[0] for n in sorted({p[2] for p in PRODUCTS})}
        uom = UoM.objects.get(name="Units")
        suppliers = [Partner.objects.get_or_create(name=n, defaults=dict(is_supplier=True, is_customer=False))[0] for n in SUPPLIERS]
        customers = [Partner.objects.get_or_create(name=n, defaults=dict(tpin=t, is_customer=True))[0] for n, t in CUSTOMERS]

        products = []
        for ref, name, cat, cost, price, qty, mn, mx, weight in PRODUCTS:
            p = Product.objects.create(name=name, internal_ref=ref, category=cats[cat], uom=uom, price=Decimal(price),
                                       secondary_price=Decimal(price) * Decimal("0.92"), cost=Decimal(cost),
                                       min_qty=Decimal(mn), max_qty=Decimal(mx), tax_type="A",
                                       barcode=f"600{zlib.crc32(ref.encode()) % 10**9:09d}")
            products.append((p, qty, weight))

        # Opening stock: one receipt per supplier, dated the day before the sales start, bought a little above what sells.
        start = timezone.localtime().date() - timedelta(days=opts["days"])
        opening = start - timedelta(days=1)
        rcp = OperationType.objects.get(warehouse=wh, code=OperationType.RECEIPT)
        for i, sup in enumerate(suppliers):
            lines = [(p, Decimal(qty) + self._sold_estimate(weight, opts["days"]), p.cost)
                     for j, (p, qty, weight) in enumerate(products) if j % len(suppliers) == i]
            lines = [l for l in lines if l[1] > 0]
            pk = stock.new_picking(rcp, lines, partner=sup, origin=f"Opening stock {i + 1}", user=users["warehouse"])
            stock.validate_picking(pk)
            StockMove.objects.filter(picking=pk).update(date=timezone.make_aware(datetime.combine(opening, time(9, 0))))

        # Sales
        selling = [(p, w) for p, _, w in products if w > 0]
        weights = [w for _, w in selling]
        cashiers = [users["cashier1"], users["cashier2"]]
        n_orders = n_refunds = 0
        tz = timezone.get_current_timezone()
        for d in range(opts["days"], -1, -1):
            day = timezone.localtime().date() - timedelta(days=d)
            per_day = rng.randint(7, 14) + (5 if day.weekday() >= 4 else 0)
            cashier = cashiers[d % 2]
            session = pos.open_session(cashier, wh, 500)
            opened = timezone.make_aware(datetime.combine(day, time(8, 0)), tz)
            session.opened_at = opened
            session.save(update_fields=["opened_at"])
            first = None
            for k in range(per_day):
                when = opened + timedelta(minutes=rng.randint(5, 540))
                if d == 0 and when > timezone.now():
                    when = timezone.now() - timedelta(minutes=rng.randint(1, 120))
                lines = []
                for p in rng.choices([s[0] for s in selling], weights=weights, k=rng.choice([1, 1, 2, 2, 3])):
                    qty = rng.choice([1, 1, 1, 2, 2, 4])
                    if all(l["product"] != p.pk for l in lines) and p.on_hand >= qty:  # never sell what is not on the shelf
                        lines.append({"product": p.pk, "qty": qty, "discount": 0})
                if not lines:
                    continue
                total = sum(Product.objects.get(pk=l["product"]).price * l["qty"] for l in lines)
                gross = pos.money(total)
                method = rng.choices([PosPayment.CASH, PosPayment.MOBILE, PosPayment.CARD], [6, 3, 1])[0]
                cust = rng.choice(customers) if rng.random() < 0.18 else None
                try:
                    order, _ = pos.create_sale(session, cashier, lines, [{"method": method, "amount": gross}],
                                               customer=cust, created_at=when)
                except pos.PosError:
                    continue
                StockMove.objects.filter(picking=order.picking).update(date=when)
                n_orders += 1
                first = first or order
            if first and d > 0 and rng.random() < 0.5:
                # a refund comes out of the manager's own till, so the manager needs an open session
                msession = pos.open_session(users["manager"], wh, 0)
                try:
                    refund = pos.refund_order(first, users["manager"], "Wrong part, returned")
                    when = first.created_at + timedelta(hours=1)
                    type(refund).objects.filter(pk=refund.pk).update(created_at=when)
                    StockMove.objects.filter(picking=refund.picking).update(date=when)
                    n_refunds += 1
                except pos.PosError:
                    pass
                msummary = pos.session_summary(msession)
                pos.close_session(msession, pos.money(msummary["expected_cash"]), users["manager"])
                msession.opened_at, msession.closed_at = opened, opened + timedelta(hours=9)
                msession.save(update_fields=["opened_at", "closed_at"])
            summary = pos.session_summary(session)
            pos.close_session(session, pos.money(summary["expected_cash"]) - (Decimal("5") if d % 9 == 4 else 0), users["manager"])
            session.closed_at = opened + timedelta(hours=9)
            session.save(update_fields=["closed_at"])

        # A stock count the day before: a few shelves turn out lower than the books, so the demo has low and empty items.
        loc = Location.objects.filter(usage=Location.INTERNAL, warehouse=wh).first()
        counts = {Product.objects.get(internal_ref=r).pk: q for r, q in
                  [("ENG-006", 0), ("FIL-003", 4), ("ELE-004", 2), ("BRK-003", 6), ("OIL-001", 18), ("TRN-001", 3)]}
        stock.apply_count(loc, counts, reason="Physical inventory")

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready: {len(products)} products, {n_orders} sales, {n_refunds} refunds over {opts['days']} days."))
        self.stdout.write("Demo logins (password is ALIFARIS_DEMO_PASSWORD): " + ", ".join(f"{u} ({r})" for u, r in USERS))

    @staticmethod
    def _sold_estimate(weight, days):
        return Decimal(weight * days // 6)

    def _users(self, password):
        U = get_user_model()
        out = {}
        for name, role in USERS:
            u, created = U.objects.get_or_create(username=name, defaults=dict(
                first_name=name.rstrip("12").title(), is_staff=(role == "manager")))
            u.set_password(password)
            u.is_staff = role == "manager"
            u.save()
            group = {"warehouse": "Warehouse Manager", "storekeeper": "Storekeeper", "cashier": "Cashier"}.get(role)
            if group:
                u.groups.add(Group.objects.get_or_create(name=group)[0])
            out[name] = u
        return out
