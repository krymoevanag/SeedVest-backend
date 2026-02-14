from django.urls import path
from .views import InitiateMpesaPaymentView, mpesa_callback, MpesaTransactionStatusView

urlpatterns = [
    path("mpesa/pay/", InitiateMpesaPaymentView.as_view(), name="mpesa-pay"),
    path("mpesa/callback/", mpesa_callback, name="mpesa-callback"),
    path("mpesa/status/<str:checkout_request_id>/", MpesaTransactionStatusView.as_view(), name="mpesa-status"),
]
