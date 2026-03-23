"""
Split Calculator — core financial logic.

Precision strategy:
- All monetary values are kept as Python Decimal objects throughout the pipeline.
- Amounts received from the API (strings like "297.00") are converted to Decimal
  immediately; floats are never used for money.
- Fee and split amounts are computed with full Decimal precision and only rounded
  to 2 decimal places at the final step, using ROUND_DOWN (floor), which favours
  the platform and avoids over-distributing.
- Penny distribution: after flooring each recipient's share we compute the
  remainder (net - sum_of_floored_shares). That remainder, which is always
  0 or a small positive number of cents, is added to the first recipient in the
  split list. This guarantees: sum(receivables) == net_amount exactly.
"""

from decimal import Decimal, ROUND_DOWN

CENT = Decimal("0.01")

# ── Fee schedule ──────────────────────────────────────────────────────────────

def calculate_fee_rate(payment_method: str, installments: int) -> Decimal:
    """Return the platform fee rate as a Decimal fraction (e.g. 0.0399)."""
    method = payment_method.lower()
    if method == "pix":
        return Decimal("0")
    if method == "card":
        if installments == 1:
            return Decimal("0.0399")
        # 2x–12x: 4.99% + 2% per extra installment above 1
        extra = (installments - 1) * 2  # percentage points
        rate = Decimal("4.99") + Decimal(str(extra))
        return (rate / Decimal("100")).quantize(Decimal("0.000001"))
    raise ValueError(f"Unsupported payment method: {payment_method!r}")


def calculate_amounts(gross_amount: Decimal, payment_method: str, installments: int):
    """
    Return (platform_fee_amount, net_amount) as Decimals rounded to 2 places.

    Fee is rounded down (platform never over-charges the seller).
    Net = gross - fee_amount (exact, no further rounding needed because
    gross and fee are both 2-decimal values).
    """
    fee_rate = calculate_fee_rate(payment_method, installments)
    fee_amount = (gross_amount * fee_rate).quantize(CENT, rounding=ROUND_DOWN)
    net_amount = gross_amount - fee_amount
    return fee_amount, net_amount


# ── Split distribution ────────────────────────────────────────────────────────

def distribute_split(net_amount: Decimal, splits: list[dict]) -> list[dict]:
    """
    Distribute net_amount among recipients according to their percentages.

    Algorithm:
    1. Compute each share as net * (percent / 100), floored to CENT.
    2. Sum all floored shares.
    3. The penny remainder (net - sum) is given to the first recipient.
       This ensures sum(amounts) == net_amount exactly.

    Returns a list of dicts: [{"recipient_id": ..., "role": ..., "amount": Decimal}, ...]
    """
    results = []
    total_allocated = Decimal("0")

    for split in splits:
        percent = Decimal(str(split["percent"]))
        share = (net_amount * percent / Decimal("100")).quantize(CENT, rounding=ROUND_DOWN)
        results.append({
            "recipient_id": split["recipient_id"],
            "role": split["role"],
            "amount": share,
        })
        total_allocated += share

    # Give any remaining cent(s) to the first recipient
    remainder = net_amount - total_allocated
    if remainder > 0:
        results[0]["amount"] += remainder

    return results


# ── Public entry point ────────────────────────────────────────────────────────

def calculate_payment(gross_amount: Decimal, payment_method: str, installments: int, splits: list[dict]) -> dict:
    """
    Compute all amounts for a payment.

    Returns a dict with:
        gross_amount, platform_fee_amount, net_amount, receivables
    """
    fee_amount, net_amount = calculate_amounts(gross_amount, payment_method, installments)
    receivables = distribute_split(net_amount, splits)

    return {
        "gross_amount": gross_amount,
        "platform_fee_amount": fee_amount,
        "net_amount": net_amount,
        "receivables": receivables,
    }
