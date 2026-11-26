"""Showcase page: every hub-ui control with static fake data, for opening and comparing screenshots."""

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render

from hub_ui import __version__

SAMPLES = ["-1234567.891", "1234567.891", "0.5", "-0.5", "0", "1.005", ""]


def showcase(request):
    """Render the showcase. Only with DEBUG on, or for staff users; otherwise 404."""
    user = getattr(request, "user", None)
    if not (settings.DEBUG or (user is not None and user.is_active and user.is_staff)):
        raise Http404
    try:
        width = int(request.GET.get("w", 0))
    except ValueError:
        width = 0
    page = Paginator(list(range(250)), 20).get_page(request.GET.get("page", 3))
    context = {
        "version": __version__,
        "samples": SAMPLES,
        "frame_width": width if 200 <= width <= 4000 else None,
        "page_obj": page,
        "pg_sizes": (20, 50, 100),
    }
    return render(request, "hub_ui/showcase.html", context)
