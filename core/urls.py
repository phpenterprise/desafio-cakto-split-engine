from django.http import JsonResponse
from django.urls import path, include


def healthcheck(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", healthcheck, name="healthcheck"),
    path("api/v1/", include("payments.api.urls")),
]
