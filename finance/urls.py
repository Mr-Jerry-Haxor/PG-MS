from django.urls import path
from . import views


urlpatterns = [
    path('fees/', views.fees_list, name='fees_list'),
    path('fees/new/', views.fees_edit, name='fees_new'),
    path('fees/<int:pk>/', views.fees_edit, name='fees_edit'),

    path('payments/', views.payments_list, name='payments_list'),
    path('payments/new/', views.payments_edit, name='payments_new'),
    path('payments/<int:pk>/', views.payments_edit, name='payments_edit'),
    path('payments/<int:pk>/delete/', views.payments_delete, name='payments_delete'),

    # Monthly dashboard & reminders
    path('monthly/', views.monthly_dashboard, name='finance_monthly'),
    path('monthly/export.csv', views.monthly_export_csv, name='finance_monthly_export_csv'),
    path('monthly/export-segments.csv', views.monthly_export_segments_csv, name='finance_monthly_export_segments_csv'),
    path('monthly/export.pdf', views.monthly_export_pdf, name='finance_monthly_export_pdf'),
    path('monthly/export.xlsx', views.monthly_export_excel, name='finance_monthly_export_excel'),
    path('monthly/remind/<int:user_id>/', views.monthly_remind, name='finance_monthly_remind'),
    path('monthly/remind-bulk/', views.monthly_bulk_remind, name='finance_monthly_bulk_remind'),
    path('monthly/quick-payment/', views.monthly_quick_payment, name='finance_monthly_quick_payment'),
    path('monthly/update-payment-date/', views.monthly_update_payment_date, name='finance_monthly_update_payment_date'),
    path('monthly/referral/<int:credit_id>/apply/', views.referral_credit_apply, name='finance_referral_credit_apply'),
    path('monthly/referral/<int:credit_id>/remove/', views.referral_credit_remove, name='finance_referral_credit_remove'),
    path('monthly/<int:user_id>/export-segments.csv', views.monthly_export_segments_user_csv, name='finance_monthly_export_segments_user_csv'),

    # Ledger
    path('ledger/<int:user_id>/', views.ledger_view, name='finance_ledger'),
    path('ledger/<int:user_id>/export.pdf', views.ledger_export_pdf, name='finance_ledger_export_pdf'),

    path('expenditure/', views.expenditure_list, name='expenditure_list'),
    path('expenditure/new/', views.expenditure_edit, name='expenditure_new'),
    path('expenditure/<int:pk>/', views.expenditure_edit, name='expenditure_edit'),
    path('expenditure/export.pdf', views.expenditure_export_pdf, name='expenditure_export_pdf'),
]
