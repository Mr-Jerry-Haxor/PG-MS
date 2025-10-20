from django.urls import path
from . import views


urlpatterns = [
    path('', views.dashboard, name='sa_dashboard'),
    path('pgs/', views.pgs, name='sa_pgs'),
    path('pgs/new/', views.pg_new, name='sa_pg_new'),
    path('pgs/<int:pg_id>/edit/', views.pg_edit, name='sa_pg_edit'),
    path('pgs/<int:pg_id>/admins/', views.pg_manage_admins, name='sa_pg_admins'),
    path('users/', views.users, name='siteadmin_users'),
    path('bookings/', views.bookings, name='siteadmin_bookings'),
    path('payments/', views.payments, name='siteadmin_payments'),
    path('expenditures/', views.expenditures, name='siteadmin_expenditures'),
    path('applications/', views.applications, name='siteadmin_applications'),
    path('applications/bulk-refill/', views.bulk_refill_applications, name='siteadmin_bulk_refill'),
]
