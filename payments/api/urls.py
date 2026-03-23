from django.urls import path
from payments.api.views import PaymentView, QuoteView

urlpatterns = [
    path("payments", PaymentView.as_view(), name="payments"),
    path("checkout/quote", QuoteView.as_view(), name="quote"),
]
