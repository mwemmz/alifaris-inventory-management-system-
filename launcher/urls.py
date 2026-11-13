from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("app/<slug:slug>/", views.module, name="module"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
    path("offline/", views.offline, name="offline"),
    path("login/", auth_views.LoginView.as_view(template_name="launcher/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
