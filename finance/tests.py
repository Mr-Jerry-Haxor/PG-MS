from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from pgadmin.models import PG

from .models import Payment
from .views import (
    _early_collection_for_user_pg_month,
    _monthly_metric_cohort,
    _resolve_status,
)


class MonthlyDashboardMetricTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='monthly-user', email='monthly@example.com'
        )
        self.pg = PG.objects.create(name='Monthly Test PG', address='Test address')
        self.month_start = date(2026, 7, 1)
        self.month_end = date(2026, 7, 31)

    def test_metric_cohorts_are_mutually_exclusive(self):
        self.assertEqual(
            _monthly_metric_cohort(
                [{'start': date(2026, 6, 1), 'end': None}],
                self.month_start,
                self.month_end,
            ),
            'active',
        )
        self.assertEqual(
            _monthly_metric_cohort(
                [{'start': date(2026, 6, 1), 'end': date(2026, 7, 20)}],
                self.month_start,
                self.month_end,
            ),
            'leaving',
        )
        self.assertEqual(
            _monthly_metric_cohort(
                [{'start': date(2026, 7, 5), 'end': date(2026, 7, 20)}],
                self.month_start,
                self.month_end,
            ),
            'joining',
        )

    def test_early_collection_resets_when_due_date_arrives(self):
        due_date = date(2026, 7, 10)
        Payment.objects.create(
            user=self.user,
            pg=self.pg,
            amount=5000,
            date=date(2026, 7, 9),
            from_date=self.month_start,
            status='success',
            type='fee',
        )

        before_due = _early_collection_for_user_pg_month(
            self.user, self.pg, self.month_start, self.month_end, due_date, date(2026, 7, 9)
        )
        on_due = _early_collection_for_user_pg_month(
            self.user, self.pg, self.month_start, self.month_end, due_date, due_date
        )

        self.assertEqual(before_due, 5000.0)
        self.assertEqual(on_due, 0.0)
        self.assertEqual(
            _resolve_status(5000, 5000, self.month_start, due_date, due_date)[0],
            'paid',
        )

# Create your tests here.
