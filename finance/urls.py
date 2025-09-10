from django.urls import path
from . import views


urlpatterns = [
    path('fees/', views.fees_list, name='fees_list'),
    path('fees/new/', views.fees_edit, name='fees_new'),
    path('fees/<int:pk>/', views.fees_edit, name='fees_edit'),

    path('payments/', views.payments_list, name='payments_list'),
    path('payments/new/', views.payments_edit, name='payments_new'),
    path('payments/<int:pk>/', views.payments_edit, name='payments_edit'),

    path('expenditure/', views.expenditure_list, name='expenditure_list'),
    path('expenditure/new/', views.expenditure_edit, name='expenditure_new'),
    path('expenditure/<int:pk>/', views.expenditure_edit, name='expenditure_edit'),
]
