from django.urls import path

from . import views as v

app_name = "inventory"

urlpatterns = [
    path("", v.overview, name="overview"),
    path("warehouse/", v.warehouse, name="warehouse"),
    path("operations/<slug:kind>/", v.picking_list, name="picking_list"),
    path("operations/<slug:kind>/new/", v.picking_new, name="picking_new"),
    path("transfer/<int:pk>/", v.picking_detail, name="picking_detail"),
    path("transfer/<int:pk>/<slug:action>/", v.picking_action, name="picking_action"),
    path("physical-inventory/", v.physical_inventory, name="physical_inventory"),
    path("scrap/", v.scrap_list, name="scrap"),
    path("scrap/new/", v.scrap_new, name="scrap_new"),
    path("landed-costs/", v.landed_list, name="landed"),
    path("landed-costs/new/", v.landed_new, name="landed_new"),
    path("landed-costs/<int:pk>/", v.landed_detail, name="landed_detail"),
    path("replenishment/", v.replenishment, name="replenishment"),
    path("products/", v.products, name="products"),
    path("products/new/", v.product_edit, name="product_new"),
    path("products/<int:pk>/", v.product_edit, name="product_edit"),
    path("products/<int:pk>/zra/", v.product_zra, name="product_zra"),
    path("reporting/stock/", v.report_stock, name="report_stock"),
    path("reporting/locations/", v.report_locations, name="report_locations"),
    path("reporting/moves/", v.report_moves, name="report_moves"),
    path("reporting/analysis/", v.report_analysis, name="report_analysis"),
    path("reporting/valuation/", v.report_valuation, name="report_valuation"),
    path("config/sync/", v.sync_peers, name="sync"),
    path("config/zra/", v.zra_settings, name="zra_settings"),
    path("config/api-tokens/", v.tokens, name="tokens"),
    path("config/<slug:key>/", v.crud_list, name="crud_list"),
    path("config/<slug:key>/new/", v.crud_edit, name="crud_new"),
    path("config/<slug:key>/<int:pk>/", v.crud_edit, name="crud_edit"),
    path("import/", v.import_products, name="import"),
    path("import/template.csv", v.import_template, name="import_template"),
    path("zra-logs/", v.zra_logs, name="zra_logs"),
    path("zra-logs/register-pending/", v.zra_register_pending, name="zra_register_pending"),
    path("zra-logs/<int:pk>/", v.zra_log_detail, name="zra_log_detail"),
    path("scan/", v.scan, name="scan"),
    path("lookup/", v.lookup, name="lookup"),
]
