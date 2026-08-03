from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from pgadmin.models import PG, PGAdmin

from .models import Expenditure, Payment, PaymentChangeLog
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


class FinanceRecordAttributionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='finance-admin', email='finance-admin@example.com', password='test-password'
        )
        self.resident = User.objects.create_user(
            username='finance-resident', email='resident@example.com'
        )
        self.pg = PG.objects.create(name='Finance Audit PG', address='Test address')
        PGAdmin.objects.create(user=self.admin, pg=self.pg)
        self.client.force_login(self.admin)

    def test_new_expenditure_stores_creator(self):
        response = self.client.post(reverse('expenditure_new'), {
            'amount': '250.00',
            'date': '2026-08-03',
            'notes': 'Cleaning supplies',
        })

        self.assertRedirects(response, reverse('expenditure_list'))
        self.assertEqual(Expenditure.objects.get().created_by, self.admin)

    def test_payment_edit_records_editor_time_and_field_changes(self):
        payment = Payment.objects.create(
            user=self.resident,
            pg=self.pg,
            amount='1000.00',
            date=date(2026, 8, 3),
            status='success',
            mode='upi',
            type='fee',
            notes='Original note',
            created_by=self.admin,
        )

        response = self.client.post(reverse('payments_edit', args=[payment.id]), {
            'user': self.resident.id,
            'amount': '1200.00',
            'date': '2026-08-03',
            'status': 'success',
            'mode': 'cash',
            'type': 'fee',
            'notes': 'Updated note',
        })

        self.assertRedirects(response, reverse('payments_list'))
        change = PaymentChangeLog.objects.get(payment=payment)
        self.assertEqual(change.updated_by, self.admin)
        self.assertIn('Amount', change.changes)
        self.assertEqual(change.changes['Amount']['before'], '1000.00')
        self.assertEqual(change.changes['Amount']['after'], '1200.00')

# Create your tests here.
