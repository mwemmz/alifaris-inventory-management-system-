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

Each shop runs its own copy with its own `ALIFARIS_SITE_CODE`, so it keeps working without internet. Copy ledger rows
between sites with `/api/v1/sync/moves/` (pull with `since` and `exclude_site`, push to the other side). Sites must use
the same product references and the same location names. A scheduled sync job is not included yet.

## Mobile PWA and desktop

The site is installable on a phone (manifest and service worker). Pages you have opened stay readable offline; saving
changes needs a connection. `desktop/` holds an Electron shell (see its README; not yet built or run).
