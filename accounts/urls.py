from django.urls import path
from .views import profile_view, google_onetap


urlpatterns = [
    path('profile/', profile_view, name='profile'),
    path('onetap/google/', google_onetap, name='google_onetap'),
]
