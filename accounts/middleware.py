from django.shortcuts import redirect
from django.urls import reverse


class OnboardingRequiredMiddleware:
    """
    If a logged-in user's profile is missing a phone number, force them
    to complete onboarding before accessing the rest of the site.
    Selfie is no longer required at onboarding.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only for authenticated users
        if request.user.is_authenticated:
            try:
                profile = request.user.profile
            except Exception:
                profile = None

            # Paths that are allowed without onboarding
            allowed_prefixes = [
                reverse('onboarding'),
                reverse('profile'),
                reverse('account_logout'),
                '/accounts/',  # allauth
                '/static/',
                '/notifications',
            ]

            path = request.path
            # Only enforce phone collection during onboarding; selfie removed from required fields
            if profile and (not profile.phone):
                if not any(path.startswith(p) for p in allowed_prefixes):
                    return redirect('onboarding')

        response = self.get_response(request)
        return response


class HideAllauthMiddleware:
    """Block direct browsing of /accounts/* pages from users.

    Allows only specific endpoints required for the Google OAuth flow and logout.
    """
    ALLOW_SUFFIXES = [
        '/accounts/google/login/',
        '/accounts/google/login/callback/',
        '/accounts/logout/',
        '/accounts/login/',  # will redirect to google provider
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith('/accounts/'):
            # If hitting generic login, force Google provider
            if path == '/accounts/login/':
                # Preserve original ?next= param (added by login_required) when redirecting to provider
                qs = request.META.get('QUERY_STRING')
                target = '/accounts/google/login/'
                if qs:
                    target = f"{target}?{qs}"
                return redirect(target)
            if path == '/accounts/google/login/' and request.user.is_authenticated:
                # Already logged in; honor next parameter if present
                next_url = request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                from django.urls import reverse
                return redirect(reverse('dashboard'))
            if not any(path.startswith(s) for s in self.ALLOW_SUFFIXES):
                return redirect('home')
        return self.get_response(request)
