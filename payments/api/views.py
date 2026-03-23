import hashlib
import json
from decimal import Decimal

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from payments.api.serializers import PaymentRequestSerializer
from payments.models import LedgerEntry, OutboxEvent, Payment
from payments.services.split_calculator import calculate_payment


def _payload_hash(data: dict) -> str:
    """Deterministic SHA-256 hash of the canonical payload JSON."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _format_payment_response(payment: Payment) -> dict:
    entries = payment.ledger_entries.all()
    outbox = payment.outbox_event

    return {
        "payment_id": f"pmt_{str(payment.id)[:8]}",
        "status": payment.status,
        "gross_amount": float(payment.gross_amount),
        "platform_fee_amount": float(payment.platform_fee_amount),
        "net_amount": float(payment.net_amount),
        "receivables": [
            {
                "recipient_id": e.recipient_id,
                "role": e.role,
                "amount": float(e.amount),
            }
            for e in entries
        ],
        "outbox_event": {
            "type": outbox.type,
            "status": outbox.status,
        },
    }


class PaymentView(APIView):
    """
    POST /api/v1/payments

    Idempotency behaviour:
    - Same Idempotency-Key + same payload  → 200, return cached response.
    - Same Idempotency-Key + diff payload  → 409 Conflict.
    - No key                               → process normally (no idempotency).
    """

    def post(self, request):
        serializer = PaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        validated = serializer.validated_data
        idempotency_key = request.headers.get("Idempotency-Key")
        current_hash = _payload_hash(request.data)

        # ── Idempotency check ──────────────────────────────────────────────
        if idempotency_key:
            existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                if existing.payload_hash == current_hash:
                    return Response(_format_payment_response(existing), status=status.HTTP_200_OK)
                return Response(
                    {
                        "error": "conflict",
                        "detail": (
                            "Idempotency-Key already used with a different payload. "
                            "Use a new key or resubmit the original payload."
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        # ── Calculate ──────────────────────────────────────────────────────
        calc = calculate_payment(
            gross_amount=validated["amount"],
            payment_method=validated["payment_method"],
            installments=validated["installments"],
            splits=[
                {
                    "recipient_id": s["recipient_id"],
                    "role": s["role"],
                    "percent": s["percent"],
                }
                for s in validated["splits"]
            ],
        )

        # ── Persist atomically ─────────────────────────────────────────────
        with transaction.atomic():
            payment = Payment.objects.create(
                status=Payment.Status.CAPTURED,
                gross_amount=calc["gross_amount"],
                platform_fee_amount=calc["platform_fee_amount"],
                net_amount=calc["net_amount"],
                payment_method=validated["payment_method"],
                installments=validated["installments"],
                idempotency_key=idempotency_key or "",
                payload_hash=current_hash,
            )

            for entry in calc["receivables"]:
                LedgerEntry.objects.create(
                    payment=payment,
                    recipient_id=entry["recipient_id"],
                    role=entry["role"],
                    amount=entry["amount"],
                )

            outbox_payload = {
                "payment_id": str(payment.id),
                "gross_amount": str(calc["gross_amount"]),
                "platform_fee_amount": str(calc["platform_fee_amount"]),
                "net_amount": str(calc["net_amount"]),
                "payment_method": validated["payment_method"],
                "installments": validated["installments"],
                "receivables": [
                    {
                        "recipient_id": e["recipient_id"],
                        "role": e["role"],
                        "amount": str(e["amount"]),
                    }
                    for e in calc["receivables"]
                ],
            }
            OutboxEvent.objects.create(
                payment=payment,
                type="payment_captured",
                payload=outbox_payload,
                status=OutboxEvent.Status.PENDING,
            )

        return Response(_format_payment_response(payment), status=status.HTTP_201_CREATED)


class QuoteView(APIView):
    """
    POST /api/v1/checkout/quote
    Same calculation as /payments but does NOT persist anything.
    """

    def post(self, request):
        serializer = PaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        validated = serializer.validated_data
        calc = calculate_payment(
            gross_amount=validated["amount"],
            payment_method=validated["payment_method"],
            installments=validated["installments"],
            splits=[
                {
                    "recipient_id": s["recipient_id"],
                    "role": s["role"],
                    "percent": s["percent"],
                }
                for s in validated["splits"]
            ],
        )

        return Response(
            {
                "gross_amount": float(calc["gross_amount"]),
                "platform_fee_amount": float(calc["platform_fee_amount"]),
                "net_amount": float(calc["net_amount"]),
                "receivables": [
                    {
                        "recipient_id": e["recipient_id"],
                        "role": e["role"],
                        "amount": float(e["amount"]),
                    }
                    for e in calc["receivables"]
                ],
            },
            status=status.HTTP_200_OK,
        )
