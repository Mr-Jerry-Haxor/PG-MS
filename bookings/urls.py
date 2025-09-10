from django.urls import path
from . import views


urlpatterns = [
    path('availability/', views.availability, name='availability'),
    path('request/<int:room_id>/<int:share_no>/', views.request_booking, name='request_booking'),
    path('aadhaar/<int:booking_id>/', views.aadhaar_submit, name='aadhaar_submit'),
    path('leaving/<int:booking_id>/', views.leaving_intimation, name='leaving_intimation'),
    path('detail/<int:booking_id>/', views.booking_detail, name='booking_detail'),
    path('application/<int:booking_id>/', views.application_fill, name='application_fill'),
    path('application/me/', views.my_application, name='my_application'),
]
