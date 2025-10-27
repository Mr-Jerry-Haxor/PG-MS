from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'finance'

    def ready(self):
        try:
            import finance.signals  # noqa: F401
        except Exception:
            # Signals should not break app initialization if optional dependencies fail
            pass
