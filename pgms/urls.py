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
from pgadmin.whatsapp_cloud_views import whatsapp_cloud_webhook

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
    path('webhooks/whatsapp/cloud/', whatsapp_cloud_webhook, name='whatsapp_cloud_webhook'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

def handler404(request, exception):  # type: ignore
    from pathlib import PurePosixPath
    from django.http import HttpResponseNotFound, JsonResponse
    from django.shortcuts import redirect
    from django.contrib import messages

    # A global 404 handler also receives missing asset, AJAX, browser-probe,
    # and service-worker requests. Redirecting those requests queues a Django
    # message for each failure, so several unrelated 404s appear as duplicate
    # toasts on the user's next page. Only redirect genuine document navigation.
    fetch_dest = (request.headers.get('Sec-Fetch-Dest') or '').lower()
    fetch_mode = (request.headers.get('Sec-Fetch-Mode') or '').lower()
    accept = (request.headers.get('Accept') or '').lower()
    suffix = PurePosixPath(request.path).suffix.lower()
    non_document_suffixes = {
        '.css', '.js', '.map', '.json', '.xml', '.txt', '.ico',
        '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg',
        '.woff', '.woff2', '.ttf', '.eot', '.pdf',
    }
    is_document_navigation = (
        fetch_dest == 'document'
        or fetch_mode == 'navigate'
        or (not fetch_dest and 'text/html' in accept)
        or (not fetch_dest and not fetch_mode and not accept and suffix not in non_document_suffixes)
    )

    if not is_document_navigation:
        if 'application/json' in accept:
            return JsonResponse({'error': 'Not found.'}, status=404)
        return HttpResponseNotFound('Not found.')

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
