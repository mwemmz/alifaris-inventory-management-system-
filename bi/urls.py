from django.urls import path

from . import views

app_name = "bi"
urlpatterns = [
    path("", views.sales, name="sales"),
    path("stock/", views.stock_view, name="stock"),
]
