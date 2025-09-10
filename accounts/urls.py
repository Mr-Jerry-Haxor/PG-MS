from django.urls import path
from .views import profile_view, onboarding, google_onetap


urlpatterns = [
    path('profile/', profile_view, name='profile'),
    path('onboarding/', onboarding, name='onboarding'),
    path('onetap/google/', google_onetap, name='google_onetap'),
]
