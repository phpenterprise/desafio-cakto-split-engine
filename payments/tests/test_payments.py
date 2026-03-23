"""
Automated tests for the Split Engine.

Covers all 5 required scenarios:
1. PIX com taxa zero, split 100%, soma bate certinho.
2. CARD 3x, split 70/30, soma receivables = net.
3. Caso com arredondamento: sobra/falta 0.01, regra do centavo funciona.
4. Idempotência: mesma key não duplica registros.
5. Idempotência: mesma key + payload diferente retorna 409 Conflict.

Bonus:
6. CARD 1x fee correctness.
7. CARD 12x fee correctness (max installments).
8. Validation: splits sum != 100.
9. Validation: PIX with installments.
10. Validation: amount <= 0.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from payments.models import LedgerEntry, OutboxEvent, Payment
from payments.services.split_calculator import calculate_payment, calculate_fee_rate


PAYMENTS_URL = "/api/v1/payments"
QUOTE_URL = "/api/v1/checkout/quote"


# ── Unit tests: split_calculator ──────────────────────────────────────────────

class TestFeeCalculation(TestCase):
    def test_pix_fee_is_zero(self):
        rate = calculate_fee_rate("pix", 1)
        self.assertEqual(rate, Decimal("0"))

    def test_card_1x_fee(self):
        rate = calculate_fee_rate("card", 1)
        self.assertEqual(rate, Decimal("0.0399"))

    def test_card_2x_fee(self):
        # 4.99% + 2% = 6.99%
        rate = calculate_fee_rate("card", 2)
        self.assertAlmostEqual(float(rate), 0.0699, places=4)

    def test_card_3x_fee(self):
        # 4.99% + 4% = 8.99%
        rate = calculate_fee_rate("card", 3)
        self.assertAlmostEqual(float(rate), 0.0899, places=4)

    def test_card_12x_fee(self):
        # 4.99% + 22% = 26.99%
        rate = calculate_fee_rate("card", 12)
        self.assertAlmostEqual(float(rate), 0.2699, places=4)


# ── Integration tests: API endpoints ─────────────────────────────────────────

class TestPaymentAPI(TestCase):
    def setUp(self):
        self.client = APIClient()

    # ── Test 1: PIX, taxa zero, split 100% ────────────────────────────────

    def test_pix_zero_fee_single_recipient(self):
        """PIX: no fee, net == gross, receivable == net."""
        payload = {
            "amount": "100.00",
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [{"recipient_id": "seller_1", "role": "producer", "percent": 100}],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json",
                                HTTP_IDEMPOTENCY_KEY="pix-test-1")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        self.assertEqual(data["platform_fee_amount"], 0.0)
        self.assertEqual(data["net_amount"], 100.0)
        self.assertEqual(data["gross_amount"], 100.0)
        self.assertEqual(len(data["receivables"]), 1)
        self.assertEqual(data["receivables"][0]["amount"], 100.0)

        # DB: exactly 1 payment, 1 ledger entry, 1 outbox event
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(OutboxEvent.objects.count(), 1)
        self.assertEqual(OutboxEvent.objects.first().status, "pending")

    # ── Test 2: CARD 3x, split 70/30, soma receivables == net ─────────────

    def test_card_3x_split_70_30(self):
        """CARD 3x: fee=8.99%, split 70/30, sum(receivables) == net."""
        payload = {
            "amount": "297.00",
            "currency": "BRL",
            "payment_method": "card",
            "installments": 3,
            "splits": [
                {"recipient_id": "producer_1", "role": "producer", "percent": 70},
                {"recipient_id": "affiliate_9", "role": "affiliate", "percent": 30},
            ],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json",
                                HTTP_IDEMPOTENCY_KEY="card-3x-test")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        gross = Decimal(str(data["gross_amount"]))
        fee = Decimal(str(data["platform_fee_amount"]))
        net = Decimal(str(data["net_amount"]))

        # gross - fee == net
        self.assertEqual(gross - fee, net)

        # sum of receivables == net
        total_receivables = sum(Decimal(str(r["amount"])) for r in data["receivables"])
        self.assertEqual(total_receivables, net)

        # 2 ledger entries
        self.assertEqual(LedgerEntry.objects.count(), 2)

    # ── Test 3: Rounding — penny rule works ───────────────────────────────

    def test_rounding_penny_rule(self):
        """
        R$10.00, PIX (0% fee), split 3 ways at 33/33/34%.
        Exact shares: 3.30 / 3.30 / 3.40
        Floored sum = 3.30 + 3.30 + 3.40 = 10.00 (exact, no remainder needed here).

        Use a trickier case: R$10.00, split 3×33.33... — use integer percents 34/33/33.
        34% → 3.40, 33% → 3.30, 33% → 3.30  → sum = 10.00 ✓

        Truly tricky: R$1.00 split 3 ways 33/33/34.
        33% of 1.00 = 0.33 (floor), 33% → 0.33, 34% → 0.34 → sum = 1.00 ✓

        Even trickier: R$10.00 split 3 ways, PIX, 1/1/98%.
        1% → 0.10, 1% → 0.10, 98% → 9.80 → sum = 10.00 ✓

        Let's use a case that DOES produce a remainder:
        R$10.00, CARD 1x (3.99% fee)
        fee = floor(10.00 * 0.0399) = floor(0.399) = 0.39
        net = 10.00 - 0.39 = 9.61
        Split 3 ways at 33/33/34%:
          33% of 9.61 = 3.1713 → floor → 3.17
          33% of 9.61 = 3.1713 → floor → 3.17
          34% of 9.61 = 3.2674 → floor → 3.26
          sum = 3.17 + 3.17 + 3.26 = 9.60
          remainder = 9.61 - 9.60 = 0.01  → added to first recipient
          final: [3.18, 3.17, 3.26] → sum = 9.61 ✓
        """
        payload = {
            "amount": "10.00",
            "currency": "BRL",
            "payment_method": "card",
            "installments": 1,
            "splits": [
                {"recipient_id": "r1", "role": "producer", "percent": 33},
                {"recipient_id": "r2", "role": "affiliate", "percent": 33},
                {"recipient_id": "r3", "role": "co_producer", "percent": 34},
            ],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json",
                                HTTP_IDEMPOTENCY_KEY="rounding-test")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        net = Decimal(str(data["net_amount"]))
        total = sum(Decimal(str(r["amount"])) for r in data["receivables"])
        self.assertEqual(total, net, "Sum of receivables must equal net exactly")

    # ── Test 4: Idempotency — same key, same payload, no duplicates ────────

    def test_idempotency_same_key_same_payload_no_duplicate(self):
        """Same Idempotency-Key + same payload → 200, no duplicate records."""
        payload = {
            "amount": "50.00",
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [{"recipient_id": "seller", "role": "producer", "percent": 100}],
        }
        key = "idem-key-001"

        resp1 = self.client.post(PAYMENTS_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp1.status_code, 201)

        resp2 = self.client.post(PAYMENTS_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp2.status_code, 200)

        # Still only 1 payment in the database
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(OutboxEvent.objects.count(), 1)

        # Response is the same payment
        self.assertEqual(resp1.json()["payment_id"], resp2.json()["payment_id"])

    # ── Test 5: Idempotency — same key, different payload → 409 ───────────

    def test_idempotency_same_key_different_payload_returns_conflict(self):
        """Same Idempotency-Key + different payload → 409 Conflict."""
        key = "idem-key-002"
        payload_1 = {
            "amount": "50.00",
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [{"recipient_id": "seller", "role": "producer", "percent": 100}],
        }
        payload_2 = {
            "amount": "99.99",  # different amount
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [{"recipient_id": "seller", "role": "producer", "percent": 100}],
        }

        resp1 = self.client.post(PAYMENTS_URL, payload_1, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp1.status_code, 201)

        resp2 = self.client.post(PAYMENTS_URL, payload_2, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(resp2.json()["error"], "conflict")

        # Still only 1 payment
        self.assertEqual(Payment.objects.count(), 1)

    # ── Bonus: Validation tests ───────────────────────────────────────────

    def test_validation_splits_sum_not_100(self):
        payload = {
            "amount": "100.00",
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [
                {"recipient_id": "r1", "role": "producer", "percent": 60},
                {"recipient_id": "r2", "role": "affiliate", "percent": 30},
            ],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json")
        self.assertEqual(resp.status_code, 422)

    def test_validation_pix_with_installments(self):
        payload = {
            "amount": "100.00",
            "currency": "BRL",
            "payment_method": "pix",
            "installments": 3,
            "splits": [{"recipient_id": "r1", "role": "producer", "percent": 100}],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json")
        self.assertEqual(resp.status_code, 422)

    def test_validation_amount_zero(self):
        payload = {
            "amount": "0.00",
            "currency": "BRL",
            "payment_method": "pix",
            "splits": [{"recipient_id": "r1", "role": "producer", "percent": 100}],
        }
        resp = self.client.post(PAYMENTS_URL, payload, format="json")
        self.assertEqual(resp.status_code, 422)

    def test_quote_does_not_persist(self):
        """Quote endpoint returns calculation without creating DB records."""
        payload = {
            "amount": "200.00",
            "currency": "BRL",
            "payment_method": "card",
            "installments": 1,
            "splits": [{"recipient_id": "seller", "role": "producer", "percent": 100}],
        }
        resp = self.client.post(QUOTE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("net_amount", resp.json())
        self.assertEqual(Payment.objects.count(), 0)
