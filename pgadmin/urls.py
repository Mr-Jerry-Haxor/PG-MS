from django.urls import path
from . import views


urlpatterns = [
    path('my/', views.my_pg, name='pg_my'),
    path('rooms/', views.rooms_list, name='pg_rooms'),
    path('rooms/new/', views.room_create, name='pg_room_create'),
    path('rooms/<int:pk>/edit/', views.room_edit, name='pg_room_edit'),
    path('rooms/<int:pk>/shares/', views.room_shares, name='pg_room_shares'),
    path('applications/', views.resident_applications, name='pg_resident_applications'),
    path('bookings/pending/', views.bookings_pending, name='pg_bookings_pending'),
    path('bookings/<int:booking_id>/approve/', views.booking_approve, name='pg_booking_approve'),
    path('bookings/<int:booking_id>/reject/', views.booking_reject, name='pg_booking_reject'),
    path('bookings/<int:booking_id>/application-email/', views.application_email_send, name='pg_application_email_send'),
    path('booking/<int:booking_id>/join-date/', views.booking_joining_update, name='pg_booking_joining_update'),
    path('leaving/', views.leaving_requests, name='pg_leaving_requests'),
    path('leaving/<int:booking_id>/confirm/', views.leaving_confirm, name='pg_leaving_confirm'),
    path('applications/<int:app_id>/confirm/', views.application_confirm, name='pg_application_confirm'),
    path('applications/<int:app_id>/reject/', views.application_reject, name='pg_application_reject'),
    path('applications/<int:app_id>/refill/', views.application_refill_request, name='pg_application_refill'),
    path('applications/<int:app_id>/pdf/', views.application_pdf, name='pg_application_pdf'),
]
