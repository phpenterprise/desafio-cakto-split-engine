import uuid
from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        CAPTURED = "captured"

    class Method(models.TextChoices):
        PIX = "pix"
        CARD = "card"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CAPTURED)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee_amount = models.DecimalField(max_digits=12, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=Method.choices)
    installments = models.PositiveSmallIntegerField(default=1)
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    payload_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"pmt_{str(self.id)[:8]}"


class LedgerEntry(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="ledger_entries")
    recipient_id = models.CharField(max_length=255)
    role = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_entries"

    def __str__(self):
        return f"{self.recipient_id} | {self.amount}"


class OutboxEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PUBLISHED = "published"

    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="outbox_event")
    type = models.CharField(max_length=100, default="payment_captured")
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outbox_events"

    def __str__(self):
        return f"{self.type} | {self.status}"
