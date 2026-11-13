from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from pos import api as pos_api
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("inventory/", include("inventory.urls")),
    path("pos/", include("pos.urls")),
    path("bi/", include("bi.urls")),
    path("api/v1/sync/orders/", pos_api.sync_orders),
    path("api/v1/", include("inventory.api_urls")),
    path("", include("launcher.urls")),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
