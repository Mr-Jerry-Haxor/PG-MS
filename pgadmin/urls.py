from django.urls import path
from . import views
from . import complaint_views
from . import whatsapp_cloud_views


urlpatterns = [
    path('my/', views.my_pg, name='pg_my'),
    path('my/<int:pg_id>/qr.pdf', views.quick_booking_qr_pdf, name='pg_quick_booking_qr_pdf'),
    path('tenants/', views.tenants, name='pg_tenants'),
    path('tenants/export/', views.tenants_export_excel, name='pg_tenants_export'),
    path('tenants/export/pdf/', views.tenants_export_pdf, name='pg_tenants_export_pdf'),
    # Async PDF generation endpoints
    path('tenants/export/pdf/async/start/', views.tenants_export_pdf_async_start, name='pg_tenants_export_pdf_async_start'),
    path('tenants/export/pdf/async/<str:task_id>/progress/', views.tenants_export_pdf_async_progress, name='pg_tenants_export_pdf_async_progress'),
    path('tenants/export/pdf/async/<str:task_id>/download/', views.tenants_export_pdf_async_download, name='pg_tenants_export_pdf_async_download'),
    path('tenants/export/pdf/async/<str:task_id>/cancel/', views.tenants_export_pdf_async_cancel, name='pg_tenants_export_pdf_async_cancel'),
    path('rooms/', views.rooms_list, name='pg_rooms'),
    path('rooms/new/', views.room_create, name='pg_room_create'),
    path('rooms/<int:pk>/edit/', views.room_edit, name='pg_room_edit'),
    path('rooms/<int:pk>/shares/', views.room_shares, name='pg_room_shares'),
    path('vehicles/search/', views.vehicle_search, name='pg_vehicle_search'),
    path('applications/', views.resident_applications, name='pg_resident_applications'),
    path('bookings/pending/', views.bookings_pending, name='pg_bookings_pending'),
    path('bookings/confirmed/', views.bookings_confirmed, name='pg_bookings_confirmed'),
    path('bookings/<int:booking_id>/approve/', views.booking_approve, name='pg_booking_approve'),
    path('bookings/<int:booking_id>/reject/', views.booking_reject, name='pg_booking_reject'),
    path('bookings/<int:booking_id>/delete/', views.booking_delete, name='pg_booking_delete'),
    path('bookings/<int:booking_id>/leave/', views.booking_leave_direct, name='pg_booking_leave_direct'),
    path('bookings/<int:booking_id>/swap/', views.booking_swap_room, name='pg_booking_swap_room'),
    path('bookings/<int:booking_id>/swap/options/rooms/', views.booking_swap_rooms_api, name='pg_booking_swap_rooms_api'),
    path('bookings/<int:booking_id>/swap/options/rooms/<int:room_id>/shares/', views.booking_swap_shares_api, name='pg_booking_swap_shares_api'),
    path('swap-check-conflict/', views.swap_check_conflict, name='pg_swap_check_conflict'),
    path('future-swaps/', views.future_swaps, name='pg_future_swaps'),
    path('future-swaps/<int:swap_id>/cancel/', views.cancel_future_swap, name='pg_cancel_future_swap'),
    # execute route removed - swaps only execute automatically on scheduled date
    path('bookings/<int:booking_id>/application-email/', views.application_email_send, name='pg_application_email_send'),
    path('booking/<int:booking_id>/join-date/', views.booking_joining_update, name='pg_booking_joining_update'),
    path('booking/<int:booking_id>/payment-date/', views.booking_payment_date_update, name='pg_booking_payment_date_update'),
    path('leaving/', views.leaving_requests, name='pg_leaving_requests'),
    path('leaving/<int:booking_id>/confirm/', views.leaving_confirm, name='pg_leaving_confirm'),
    path('leaving/<int:booking_id>/reject/', views.leaving_reject, name='pg_leaving_reject'),
    path('leaving/<int:booking_id>/delete/', views.leaving_delete, name='pg_leaving_delete'),
    # Enhanced leave management
    path('leave/requests/', views.leaving_requests, name='pg_leaving_requests_enhanced'),
    path('leave/<int:booking_id>/confirm/', views.confirm_leave, name='pg_confirm_leave'),
    path('leave/<int:booking_id>/reject/', views.reject_leave, name='pg_reject_leave'),
    path('leave/<int:booking_id>/edit-date/', views.edit_leave_date, name='pg_edit_leave_date'),
    path('leave/<int:booking_id>/mark-advance-returned/', views.mark_advance_returned, name='pg_mark_advance_returned'),
    path('leave/<int:booking_id>/edit-advance-amount/', views.edit_advance_returned_amount, name='pg_edit_advance_amount'),
    # Re-continue feature
    path('leave/<int:booking_id>/re-continue/', views.re_continue_booking, name='pg_re_continue'),
    # Old tenants archive
    path('old-tenants/', views.old_tenants, name='pg_old_tenants'),
    path('old-tenants/refresh/', views.refresh_old_tenants, name='pg_refresh_old_tenants'),
    # Future swap feature
    path('swap/create/<int:booking_id>/', views.create_future_swap, name='pg_create_future_swap'),
    path('swap/<int:swap_id>/approve/', views.approve_future_swap, name='pg_approve_future_swap'),
    path('swap/<int:swap_id>/reject/', views.reject_future_swap, name='pg_reject_future_swap'),
    path('swap/<int:swap_id>/execute/', views.execute_swap, name='pg_execute_swap'),
    path('applications/<int:app_id>/confirm/', views.application_confirm, name='pg_application_confirm'),
    path('applications/<int:app_id>/reject/', views.application_reject, name='pg_application_reject'),
    path('applications/<int:app_id>/refill/', views.application_refill_request, name='pg_application_refill'),
    path('applications/<int:app_id>/referral/', views.application_update_referral, name='pg_application_referral'),
    path('applications/<int:app_id>/pdf/', views.application_pdf, name='pg_application_pdf'),
    path('applications/<int:app_id>/admin-edit/', views.admin_application_edit, name='pg_admin_application_edit'),
    path('referrals/', views.pg_referrals, name='pg_referrals'),
    
    # Bed status sync
    path('sync-bed-statuses/', views.sync_bed_statuses, name='pg_sync_bed_statuses'),
    
    # WhatsApp group management
    path('whatsapp/', views.whatsapp_management, name='pg_whatsapp_management'),
    path('whatsapp/mark-sent/<int:booking_id>/', views.whatsapp_mark_sent, name='pg_whatsapp_mark_sent'),
    path('whatsapp/stats/', views.whatsapp_stats, name='pg_whatsapp_stats'),
    path('whatsapp/messages/', whatsapp_cloud_views.whatsapp_conversations, name='pg_whatsapp_conversations'),
    path('whatsapp/cloud/send/', whatsapp_cloud_views.whatsapp_cloud_send, name='pg_whatsapp_cloud_send'),
    
    # Complaint management
    path('complaints/', complaint_views.admin_complaints, name='admin_complaints'),
    path('complaints/<int:complaint_id>/', complaint_views.admin_complaint_detail, name='admin_complaint_detail'),
    path('complaints/<int:complaint_id>/comment/', complaint_views.admin_complaint_add_comment, name='admin_complaint_add_comment'),
    path('complaints/<int:complaint_id>/status/', complaint_views.admin_complaint_update_status, name='admin_complaint_update_status'),
    path('complaints/<int:complaint_id>/priority/', complaint_views.admin_complaint_update_priority, name='admin_complaint_update_priority'),
    path('complaints/comment/<int:comment_id>/edit/', complaint_views.admin_complaint_edit_comment, name='admin_complaint_edit_comment'),
    path('complaints/comment/<int:comment_id>/delete/', complaint_views.admin_complaint_delete_comment, name='admin_complaint_delete_comment'),
]
