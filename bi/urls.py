from django.urls import path

from . import views

app_name = "bi"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sales/", views.sales, name="sales"),
    path("insights/", views.insights_view, name="insights"),
    path("ask/", views.ask_view, name="ask"),
    path("draft-order/", views.draft_order, name="draft_order"),
    path("stock/", views.stock_view, name="stock"),
]
