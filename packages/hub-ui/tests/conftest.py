import django
from django.conf import settings


def pytest_configure():
    if settings.configured:
        return
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test",
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=["django.contrib.staticfiles", "hub_ui"],
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
            }
        ],
        USE_TZ=True,
    )
    django.setup()
