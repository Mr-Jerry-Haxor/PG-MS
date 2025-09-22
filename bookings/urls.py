from django.urls import path
from . import views


urlpatterns = [
    path('request/<int:room_id>/<int:share_no>/', views.request_booking, name='request_booking'),
    path('aadhaar/<int:booking_id>/', views.aadhaar_submit, name='aadhaar_submit'),
    path('leaving/<int:booking_id>/', views.leaving_intimation, name='leaving_intimation'),
    path('detail/<int:booking_id>/', views.booking_detail, name='booking_detail'),
    path('application/<int:booking_id>/', views.application_fill, name='application_fill'),
    path('application/me/', views.my_application, name='my_application'),
    # Quick booking flow by PG slug
    path('pg/<slug:pgslug>/', views.pg_quick_booking, name='pg_quick_booking'),
    path('pg/<slug:pgslug>/api/rooms/', views.pg_quick_rooms, name='pg_quick_rooms'),
    path('pg/<slug:pgslug>/api/rooms/<int:room_id>/shares/', views.pg_quick_shares, name='pg_quick_shares'),
]
