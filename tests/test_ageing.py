from datetime import date, timedelta

import pytest

from revert.core.ageing import Bucket, age_invoices, bucket_for, buyer_ageing, days_overdue
from revert.core.models import Buyer, Invoice

AS_OF = date(2026, 10, 30)


@pytest.mark.parametrize("days,bucket", [
    (-1, Bucket.NOT_DUE), (-30, Bucket.NOT_DUE),
    (0, Bucket.D0_30), (1, Bucket.D0_30), (30, Bucket.D0_30),
    (31, Bucket.D31_60), (60, Bucket.D31_60),
    (61, Bucket.D61_90), (90, Bucket.D61_90),
    (91, Bucket.D90_PLUS), (400, Bucket.D90_PLUS),
])
def test_bucket_boundaries(days, bucket):
    assert bucket_for(days) == bucket


def test_days_overdue():
    assert days_overdue(date(2026, 10, 1), AS_OF) == 29
    assert days_overdue(AS_OF, AS_OF) == 0
    assert days_overdue(date(2026, 11, 5), AS_OF) == -6


def _inv(id, number, due_days_ago, total, balance):
    return Invoice(id=id, number=number, buyer_id=1, date=AS_OF - timedelta(days=due_days_ago + 30),
                   due_date=AS_OF - timedelta(days=due_days_ago), total_paise=total, balance_paise=balance)


def test_paid_invoices_are_not_aged():
    invs = [_inv(1, "A", 45, 10_000_00, 0), _inv(2, "B", 45, 10_000_00, 4_000_00)]
    aged = age_invoices(invs, AS_OF)
    assert [a.number for a in aged] == ["B"]
    assert aged[0].balance_paise == 4_000_00  # pending, not the invoice total


def test_buyer_ageing_aggregates_by_bucket():
    b = Buyer(id=1, ledger_name="X", credit_limit_paise=50_000_00)
    b.invoices = [
        _inv(1, "NOTDUE", -5, 10_000_00, 10_000_00),
        _inv(2, "D10", 10, 20_000_00, 20_000_00),
        _inv(3, "D45", 45, 15_000_00, 5_000_00),     # part paid
        _inv(4, "D75", 75, 7_000_00, 7_000_00),
        _inv(5, "D120", 120, 3_000_00, 3_000_00),
        _inv(6, "PAID", 200, 99_000_00, 0),
    ]
    ba = buyer_ageing(b, AS_OF)
    assert ba.buckets == {
        Bucket.NOT_DUE: 10_000_00, Bucket.D0_30: 20_000_00, Bucket.D31_60: 5_000_00,
        Bucket.D61_90: 7_000_00, Bucket.D90_PLUS: 3_000_00,
    }
    assert ba.total_outstanding_paise == 45_000_00
    assert ba.total_overdue_paise == 35_000_00
    assert ba.oldest_days_overdue == 120
    assert ba.invoices[0].number == "D120"  # most overdue first
