from django.urls import path
from . import views

app_name = 'advertisements'

urlpatterns = [
    # Main management page
    path('manage/', views.manage_advertisements, name='manage'),
    
    # Image management
    path('upload-image/', views.upload_image, name='upload_image'),
    path('delete-image/<int:image_id>/', views.delete_image, name='delete_image'),
    path('toggle-image/<int:image_id>/', views.toggle_image, name='toggle_image'),
    path('reorder-images/', views.reorder_images, name='reorder_images'),
    
    # Text management
    path('save-text/', views.save_text, name='save_text'),
    path('delete-text/<int:text_id>/', views.delete_text, name='delete_text'),
    path('toggle-text/<int:text_id>/', views.toggle_text, name='toggle_text'),
    
    # Settings
    path('update-settings/', views.update_settings, name='update_settings'),
    
    # API for dashboard
    path('api/<int:pg_id>/', views.get_advertisements, name='get_advertisements'),
]
