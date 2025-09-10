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
from core.views import dashboard, notifications, notification_read, notifications_mark_all
from core.views import dashboard

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('accounts/', include('allauth.socialaccount.urls')),
    path('user/', include('accounts.urls')),
    path('pg/', include('pgadmin.urls')),
    path('b/', include('bookings.urls')),
    path('f/', include('finance.urls')),
    path('sa/', include('siteadmin.urls')),
    path('', TemplateView.as_view(template_name='home.html'), name='home'),
    path('dashboard/', dashboard, name='dashboard'),
    path('notifications/', notifications, name='notifications'),
    path('notifications/<int:pk>/read/', notification_read, name='notification_read'),
    path('notifications/mark-all/', notifications_mark_all, name='notifications_mark_all'),
]
