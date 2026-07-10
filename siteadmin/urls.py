from django.urls import path
from . import views


urlpatterns = [
    path('', views.dashboard, name='sa_dashboard'),
    path('super-admin/', views.super_admin_dashboard, name='super_admin_dashboard'),
    path('pgs/', views.pgs, name='sa_pgs'),
    path('pgs/new/', views.pg_new, name='sa_pg_new'),
    path('pgs/<int:pg_id>/edit/', views.pg_edit, name='sa_pg_edit'),
    path('pgs/<int:pg_id>/admins/', views.pg_manage_admins, name='sa_pg_admins'),
    path('pgs/<int:pg_id>/delete/', views.pg_delete, name='sa_pg_delete'),
    path('pgs/<int:pg_id>/admins/<int:admin_id>/permissions/', views.pg_admin_permissions, name='sa_pg_admin_permissions'),
    path('api/admins/<int:admin_id>/permissions/', views.pg_admin_permissions_api, name='sa_pg_admin_permissions_api'),
    path('users/', views.users, name='siteadmin_users'),
    path('bookings/', views.bookings, name='siteadmin_bookings'),
    path('payments/', views.payments, name='siteadmin_payments'),
    path('expenditures/', views.expenditures, name='siteadmin_expenditures'),
    path('applications/', views.applications, name='siteadmin_applications'),
    path('applications/bulk-refill/', views.bulk_refill_applications, name='siteadmin_bulk_refill'),
]
