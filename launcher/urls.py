from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("app/<slug:slug>/", views.module, name="module"),
    path("login/", auth_views.LoginView.as_view(template_name="launcher/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
