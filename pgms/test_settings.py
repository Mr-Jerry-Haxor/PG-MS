from .settings import *  # noqa: F401,F403

# The repository virtual environment's Pillow wheel targets Python 3.13. This
# lets the suite run under the available Python 3.12 interpreter in CI/dev.
SILENCED_SYSTEM_CHECKS = ['fields.E210']

# Tests should resolve newly added source assets directly without requiring a
# production collectstatic manifest to have been generated first.
STORAGES = {
    **STORAGES,
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}
