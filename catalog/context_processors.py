from django.conf import settings
from django.utils.functional import SimpleLazyObject

from desk import notices

from . import news
from .mine import catalogue, mine
from .models import Course
from .nav import has_content


def site(request):
    """Variables every template gets. `nav_courses` is lazy: the function inside runs,
    and the database is asked, only if a template actually uses the list. Pages inside
    a course replace that part of the navigation and never do."""

    def current():
        # The courses this person chose. Of a course taught in several years only the
        # latest is listed, only while that one is active, and only once there is
        # something in it to open.
        active = Course.Status.ACTIVE
        latest = mine(request, catalogue(request))
        return [c for c in latest if c.status == active and has_content(c, request.user)]

    return {
        "nav_courses": SimpleLazyObject(current),
        # What the bell at the top shows. Lazy as well, and only used for someone signed
        # in: one query per page.
        "notices": SimpleLazyObject(lambda: notices.latest(request.user)),
        "debug": settings.DEBUG,
        # The number of the site's latest update: the tour and the news dialog send it
        # back when they are shown (catalog/welcome.py).
        "news_latest": news.LATEST,
    }
