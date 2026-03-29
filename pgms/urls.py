"""
URL configuration for pgms project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static
from core.views import (
    dashboard,
    notifications,
    notification_read,
    notifications_mark_all,
    home,
    service_worker,
    register_fcm_token,
    unregister_fcm_token,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('accounts/', include('allauth.socialaccount.urls')),
    path('user/', include('accounts.urls')),
    path('pg/', include('pgadmin.urls')),
    path('b/', include('bookings.urls')),
    path('f/', include('finance.urls')),
    path('sa/', include('siteadmin.urls')),
    path('employees/', include('employee.urls')),
    path('ads/', include('advertisements.urls')),
    path('service-worker.js', service_worker, name='service_worker'),
    path('', home, name='home'),
    path('dashboard/', dashboard, name='dashboard'),
    path('notifications/', notifications, name='notifications'),
    path('notifications/<int:pk>/read/', notification_read, name='notification_read'),
    path('notifications/mark-all/', notifications_mark_all, name='notifications_mark_all'),
    path('notifications/fcm/register/', register_fcm_token, name='register_fcm_token'),
    path('notifications/fcm/unregister/', unregister_fcm_token, name='unregister_fcm_token'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

def handler404(request, exception):  # type: ignore
    from django.shortcuts import redirect
    from django.contrib import messages
    if request.user.is_authenticated:
        messages.info(request, "Page not found. Redirected to your dashboard.")
        return redirect('dashboard')
    messages.info(request, "Page not found. Redirected to home.")
    return redirect('home')

def handler500(request):  # type: ignore
    from django.shortcuts import redirect
    from django.contrib import messages
    # Try to show a friendly message; may silently fail if sessions/messages unusable.
    try:
        if getattr(request, 'user', None) and request.user.is_authenticated:
            messages.error(request, "Unexpected server error. Redirected to your dashboard.")
            return redirect('dashboard')
        messages.error(request, "Unexpected server error. Redirected to home.")
    except Exception:
        pass
    return redirect('home')
