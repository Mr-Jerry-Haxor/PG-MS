from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

from .forms import ProfileForm, OnboardingForm
from django.conf import settings
from core.drive import drive_upload
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
import requests
from django.contrib.auth import login as auth_login, get_user_model
from allauth.socialaccount.models import SocialAccount

User = get_user_model()


@login_required
def profile_view(request):
    profile = request.user.profile
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            obj = form.save(commit=False)
            selfie_file = request.FILES.get('selfie')
                # Selfie removed from onboarding per updated requirements
            obj.save()
            messages.success(request, "Profile updated.")
            return redirect('profile')
    else:
        form = ProfileForm(instance=profile)
    return render(request, 'accounts/profile.html', {"form": form, "profile": profile})


@login_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    profile = request.user.profile
    if request.method == 'POST':
        form = OnboardingForm(request.POST, request.FILES)
        if form.is_valid():
            # Update user's names as well
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.save(update_fields=['first_name', 'last_name'])
            profile.phone = form.cleaned_data['phone']
            # Selfie capture moved to post-approval application flow
                # Selfie removed from onboarding; may be added later in application process
            profile.save(update_fields=['phone'])
            messages.success(request, "Thanks! You're all set.")
            return redirect('dashboard')
    else:
        form = OnboardingForm(initial={
            'first_name': request.user.first_name or '',
            'last_name': request.user.last_name or '',
            'phone': profile.phone if profile.phone else ''
        })
    return render(request, 'accounts/onboarding.html', {"form": form, "profile": profile})
from django.shortcuts import render

# Create your views here.

@require_http_methods(["POST"])
def google_onetap(request):
    """Accept Google One Tap credential (ID token), verify, and sign user in.
    Expects JSON { credential: <id_token> }.
    """
    import json
    try:
        body = json.loads(request.body or '{}')
        token = body.get('credential')
        if not token:
            return JsonResponse({'ok': False, 'error': 'missing_token'}, status=400)
        # Verify token via Google tokeninfo
        resp = requests.get('https://oauth2.googleapis.com/tokeninfo', params={'id_token': token}, timeout=5)
        if resp.status_code != 200:
            return JsonResponse({'ok': False, 'error': 'invalid_token'}, status=400)
        data = resp.json()
        email = data.get('email')
        email_verified = data.get('email_verified') in (True, 'true', '1', 1)
        if not email or not email_verified:
            return JsonResponse({'ok': False, 'error': 'unverified_email'}, status=400)
        user, created = User.objects.get_or_create(email__iexact=email, defaults={'email': email, 'username': email.split('@')[0]})
        if created:
            # Ensure profile created by signal
            pass
        # Attach social account if missing (lightweight record)
        if not SocialAccount.objects.filter(user=user, provider='google').exists():
            SocialAccount.objects.create(user=user, provider='google', uid=data.get('sub') or email, extra_data=data)
        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return JsonResponse({'ok': True, 'created': created})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': 'server_error', 'detail': str(e)[:200]}, status=500)
