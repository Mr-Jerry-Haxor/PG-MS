from django.urls import path
from . import views

urlpatterns = [
    path('', views.employee_list, name='employee_list'),
    path('create/', views.employee_create, name='employee_create'),
    path('<int:pk>/', views.employee_detail, name='employee_detail'),
    path('<int:pk>/update/', views.employee_update, name='employee_update'),
    path('<int:pk>/delete/', views.employee_delete, name='employee_delete'),
    path('<int:employee_pk>/ledger/add/', views.ledger_add, name='ledger_add'),
    path('ledger/<int:pk>/delete/', views.ledger_delete, name='ledger_delete'),
]
