from django.urls import path

from . import api

urlpatterns = [
    path("products/", api.products),
    path("stock/", api.stock_level),
    path("sales/", api.sales),
    path("returns/", api.sale_returns),
    path("sync/moves/", api.sync_moves),
]
