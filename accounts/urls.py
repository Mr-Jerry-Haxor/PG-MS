from django.urls import path
from .views import profile_view, google_onetap
from .complaint_views import my_complaints, create_complaint, complaint_detail


urlpatterns = [
    path('profile/', profile_view, name='profile'),
    path('onetap/google/', google_onetap, name='google_onetap'),
    
    # User complaints
    path('complaints/', my_complaints, name='my_complaints'),
    path('complaints/create/', create_complaint, name='create_complaint'),
    path('complaints/<int:complaint_id>/', complaint_detail, name='complaint_detail'),
]
