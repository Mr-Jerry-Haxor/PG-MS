from django.shortcuts import redirect


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
