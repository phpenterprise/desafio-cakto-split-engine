from decimal import Decimal, InvalidOperation
from rest_framework import serializers


class SplitSerializer(serializers.Serializer):
    recipient_id = serializers.CharField(max_length=255)
    role = serializers.CharField(max_length=50)
    percent = serializers.DecimalField(max_digits=5, decimal_places=2)

    def validate_percent(self, value):
        if value <= 0 or value > 100:
            raise serializers.ValidationError("percent must be between 0 (exclusive) and 100.")
        return value


class PaymentRequestSerializer(serializers.Serializer):
    amount = serializers.CharField()
    currency = serializers.CharField(max_length=3)
    payment_method = serializers.ChoiceField(choices=["pix", "card"])
    installments = serializers.IntegerField(required=False, default=1)
    splits = SplitSerializer(many=True)

    def validate_amount(self, value):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise serializers.ValidationError("Invalid amount format.")
        if amount <= 0:
            raise serializers.ValidationError("amount must be greater than 0.")
        return amount

    def validate_currency(self, value):
        if value.upper() != "BRL":
            raise serializers.ValidationError("Only BRL is supported.")
        return value.upper()

    def validate_installments(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError("installments must be between 1 and 12.")
        return value

    def validate_splits(self, value):
        if not (1 <= len(value) <= 5):
            raise serializers.ValidationError("splits must have between 1 and 5 recipients.")
        total = sum(s["percent"] for s in value)
        if total != Decimal("100"):
            raise serializers.ValidationError(
                f"Sum of split percentages must be 100 (got {total})."
            )
        return value

    def validate(self, data):
        method = data.get("payment_method")
        installments = data.get("installments", 1)
        if method == "pix" and installments != 1:
            raise serializers.ValidationError(
                {"installments": "PIX does not support installments."}
            )
        if method == "card" and not (1 <= installments <= 12):
            raise serializers.ValidationError(
                {"installments": "CARD installments must be between 1 and 12."}
            )
        return data
