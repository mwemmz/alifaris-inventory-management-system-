from django.urls import path

from . import views

app_name = "pos"
urlpatterns = [
    path("", views.terminal, name="terminal"),
    path("catalog.json", views.catalog, name="catalog"),
    path("sale/", views.sale_api, name="sale_api"),
    path("session/open/", views.session_open, name="session_open"),
    path("session/close/", views.session_close, name="session_close"),
    path("session/<int:pk>/", views.session_close, name="session_close"),
    path("sessions/", views.session_list, name="session_list"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/receipt/", views.receipt, name="receipt"),
    path("orders/<int:pk>/refund/", views.refund, name="refund"),
    path("zra/flush/", views.zra_flush, name="zra_flush"),
    path("report/", views.report, name="report"),
    path("customers/", views.customers, name="customers"),
    path("customers/<int:pk>/", views.customer_statement, name="customer_statement"),
]
