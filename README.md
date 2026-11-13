# Alifaris ERP

Inventory management for Alifaris Auto (Django). Web app for managers, installable as a mobile PWA, with a desktop
shell, a POS API, and ZRA Smart Invoice integration.

## Run it

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed_inventory      # default warehouse, locations, operation types, units
    python manage.py createsuperuser
    python manage.py runserver

Open http://127.0.0.1:8000, log in, click **Inventory**. Run the tests with `python manage.py test`.

Environment variables: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (1/0), `DJANGO_ALLOWED_HOSTS` (comma separated),
`ALIFARIS_SITE_CODE` (short code of this shop, default `MAIN`), `ALIFARIS_ALLOW_NEGATIVE_STOCK` (1/0, default 1).

## What is in Inventory

| Area | Screens |
|---|---|
| Overview | Receipts, Deliveries, Internal transfers, PoS orders, low-stock warning |
| Operations | Receipts, deliveries and internal transfers (draft, validate, cancel); Physical inventory; Scrap; Landed costs; Replenishment (creates a draft receipt) |
| Products | Card and list views, search by name, reference or barcode, images, two prices, cost, reorder levels, tax type, ZRA fields |
| Reporting | Stock, Stock by location, Moves history, Moves analysis, Valuation |
| Configuration (staff) | Warehouses, Locations, Product categories, UoM and UoM categories, Contacts, ZRA settings, POS API tokens |
| Import Products (staff) | CSV or Excel; create, update or both; skips bad rows; optional opening stock and ZRA registration |
| ZRA Logs | Every request to the VSDC with request and response; failed items blocked before sending |
| Scan | Barcode or reference lookup, camera scanning where the browser supports it |

Every stock change is one row in an append-only ledger (`StockMove`); stock levels are running totals of it.

## ZRA Smart Invoice

Set the VSDC address, TPIN and branch under Configuration > ZRA Smart Invoice, tick Enabled, then use
**Register pending items** on the ZRA Logs screen. Items with an item class code that is not exactly 10 characters,
or a missing unit, are blocked locally and logged as "Not sent" with the reason, instead of being rejected by ZRA.
Endpoints and field names follow the `vsdcsdk` open-source SDK and have **not been tested against a live VSDC**:
check the first log entries after enabling it. Sales invoicing is expected to stay in your POS.

## POS API

Create a token under Configuration > POS API tokens and send `Authorization: Token <key>`. JSON in and out.

| Call | Purpose |
|---|---|
| `GET /api/v1/products/?q=&updated_since=` | Catalogue with price, tax type and on-hand |
| `GET /api/v1/stock/?ref=` or `?barcode=` | Stock level |
| `POST /api/v1/sales/` | `{"external_ref":"POS-1001","lines":[{"ref":"392","qty":2}]}` takes stock out. Sending the same `external_ref` again never deducts twice. The whole request fails if any line is bad. |
| `POST /api/v1/returns/` | Same shape; puts stock back |
| `GET/POST /api/v1/sync/moves/` | Site-to-site sync of ledger rows, idempotent by uuid |

If the POS shares this database directly, call the same functions in `inventory/services/stock.py` instead of the API.

## Several sites, offline

Each shop runs its own copy with its own `ALIFARIS_SITE_CODE`, so it keeps working without internet. Under
Configuration > Site sync, add the head-office server as a peer (its address and a POS API token created there).
`python manage.py sync_sites` (or the **Sync now** button) then:

1. copies new and changed **products** from head office to the shop,
2. pulls stock moves made at other sites, and
3. pushes this shop's moves to head office.

Moves carry a unique id, so a sync that is repeated or interrupted never counts anything twice, and progress is saved
after every step. Schedule it every few minutes with Windows Task Scheduler or cron. Sites must use the same location
names (the seed command creates `SHOP/Stock`, `Vendors`, `Customers`, `Inventory adjustment`, `Scrap`); a move for a
location or product the other site does not have fails the batch and is reported on the Site sync screen.
Stock quantities are never copied directly; they are always rebuilt from moves.

## Roles

- **Staff users** (managers): everything, including Configuration, Import and ZRA settings.
- **Storekeeper group** (created by `seed_inventory`): record receipts, deliveries, transfers, counts, scrap and edit products.
- **Any other logged-in user**: view only.

## Deploying

    pip install -r requirements.txt
    python manage.py collectstatic --noinput
    DJANGO_DEBUG=0 DJANGO_SECRET_KEY=<long random> DJANGO_ALLOWED_HOSTS=erp.example.com gunicorn config.wsgi

Static files are served by WhiteNoise. Uploaded product images live in `media/` and need to be served by your web
server (or put behind a proxy) when `DJANGO_DEBUG=0`. The database is SQLite in WAL mode, which is fine for one shop
with a POS and a few users; **back up `db.sqlite3` regularly** (copy it while the app is idle, or use `sqlite3 db.sqlite3 ".backup copy.db"`).
Move to PostgreSQL if one server will carry several busy shops. Put the site behind HTTPS before using it away from your own network.

## Mobile PWA and desktop

The site is installable on a phone (manifest and service worker). Pages you have opened stay readable offline; saving
changes on a phone needs a connection (a shop that must work offline runs its own server and syncs, above).
`desktop/` holds an Electron shell (see its README; syntax-checked but not yet built or run).

## Point of Sale (same system, same database)

The till lives at `/pos/` and is built into this project, so a sale is one transaction: order + payments + stock-out on the same ledger
the Inventory screens read, so on-hand is correct everywhere the moment a sale completes.

- **Roles:** staff = manager (price override, discounts above `ALIFARIS_MAX_CASHIER_DISCOUNT`%, refunds, sessions, report). Group **Cashier** = can sell. Created by `seed_inventory`.
- **Session & cash-up:** open with float, close with counted cash; expected cash accounts for change given and refunds.
- **Offline till:** the browser keeps the price list and queues sales on the device with a unique id; they upload automatically and can never be posted twice.
- **Receipts:** `/pos/orders/<id>/receipt/` is laid out for 80 mm paper. The desktop wrapper can print silently (`desktop/README.md`).
- **Refunds:** whole sale, manager only, money back the way it came in, stock returned once.
- **ZRA:** each sale/refund is queued (`Waiting`) and sent with `python manage.py zra_send_sales --loop` or the *Send now* button on the report. Selling never waits on ZRA. Items with a bad class code are blocked locally and shown as Failed. **Untested against a live VSDC.**
- **Sites:** `sync_sites` now also copies sales records between sites (for reporting); their stock effect travels as ledger moves, so nothing is deducted twice.
- Settings: `ALIFARIS_PRICES_INCLUDE_TAX` (default 1), `ALIFARIS_MAX_CASHIER_DISCOUNT` (default 10).

## Reports & BI (`/bi/`, managers only; the Dashboards tile)

- **Sales analysis:** filter by date range, department (category, including sub-categories), product name/ref/barcode, price range, cashier, site, customer, price tier and payment method; group time by day/week/month. Shows sales incl./excl. VAT, estimated margin, orders, average order, units, plus breakdowns by department, product, cashier and site, with CSV export.
- **Stock analysis:** stock value at cost and at selling price by department, filter by category/product/price, low-stock and out-of-stock views, CSV export.
- Margin uses each product's current cost (sale lines do not store cost yet), so treat it as an estimate.

### Charts and automatic insights (`/bi/insights/`)
- Charts are drawn in the browser with a small bundled script, so they work offline.
- **Reorder suggestions:** from the last 30 days' selling speed, lists products with under 14 days of stock and how much to order for 30 days. One click creates a **draft** receipt (nothing moves until validated).
- **Slow stock**, a 7-day **sales forecast** (same weekday over the last 4 weeks), and **flags** for high refund or discount rates per cashier, cash-up differences and ZRA failures.
- **Ask your data:** works with no key. Plain-English questions ("sales this month", "top products last month", "how many oil filters do we have", "price of brake pads", "sales by cashier this week", "refunds", "cash short last week", "stock value", "dead stock", "what should I reorder", "forecast") are answered straight from the database with read-only queries; dates, departments and product names are picked out of the sentence. If you also set `ANTHROPIC_API_KEY`, questions it cannot match are passed to Claude as totals only (untested against the live API).

### Manager dashboard (`/bi/`, the Dashboards tile)
Sales today against yesterday and the same day last week, month to date against the same days last month, stock value, items to reorder, ZRA queue and site-sync status, 14-day and by-hour charts, tills open now, payment mix, top products today and flags. Refreshes every minute. Detailed analysis moved to `/bi/sales/`.

## Branding
Logo, charcoal/red/grey palette and icons live in `launcher/static/launcher/brand/` and `brand.css` (loaded last, so one file controls the look). PWA icons and the desktop window icon are made from the logo's "A" mark. The receipt is still plain black-and-white text for thermal printers.
