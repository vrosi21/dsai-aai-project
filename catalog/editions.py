"""The same course in different academic years.

One `Course` row is one year of teaching: its own folder, its own content, its own
progress. Rows that share `series` are editions of one course (MLPR 2025/26 and, once it
is written, MLPR 2026/27). This module answers the two questions pages ask about that, so
no view or template works it out for itself:

    latest(courses)   one course per series, the most recent year   dashboard, navigation
    of(course)        every year of this course, newest first        the switch on its page

A new year is a new folder whose course.yaml names the `series`; nothing here changes.

And it knows which years there can be: from the programme's first (a setting) to the
one running now, which follows from today's date.

    current_year()    "2026-27" in October 2026
    years()           every year a course can exist in, newest first
    missing(course)   those of them this course has no edition for
"""

from django.conf import settings
from django.utils import timezone

from .models import Course


def _series(course):
    # `series` is filled in by sync_content. A row from before that, or one built by hand
    # in a test, stands for itself.
    return course.series or course.slug


def _recency(course):
    # Years are written "2025-26", so comparing them as text puts them in order, and a
    # course without a year counts as the oldest. The slug only breaks ties.
    return (course.year, course.slug)


def _standing(course):
    # A year that has something in it comes before one that is still empty: a year
    # someone has just started must not hide last year's notes from the dashboard.
    return (bool(course.has_content), *_recency(course))


def latest(courses):
    """Keep one course per series: the most recent year that has content, or, if none
    has, the most recent year. Order is preserved."""
    newest = {}
    for course in courses:
        best = newest.get(_series(course))
        if best is None or _standing(course) > _standing(best):
            newest[_series(course)] = course
    return [course for course in courses if newest[_series(course)] is course]


def year_from(start):
    """ "2026-27" for the academic year that begins in 2026."""
    return f"{start}-{(start + 1) % 100:02d}"


def current_year(today=None):
    """The academic year `today` falls in. It begins in settings.ACADEMIC_YEAR_STARTS
    (October): September 2026 is still 2025-26, October 2026 is 2026-27."""
    today = today or timezone.localdate()
    began = today.year if today.month >= settings.ACADEMIC_YEAR_STARTS else today.year - 1
    return year_from(began)


def years(today=None):
    """Every academic year a course can exist in, newest first: from the programme's
    first (settings.FIRST_ACADEMIC_YEAR) to the current one. Nothing is listed here by
    hand, so next October there is one more."""
    first, last = int(settings.FIRST_ACADEMIC_YEAR[:4]), int(current_year(today)[:4])
    return [year_from(start) for start in range(last, first - 1, -1)]


def missing(course, today=None, among=None):
    """The years `course`'s series has no edition for, newest first. `among` is the list
    of its editions, for a caller that has it already."""
    have = {edition.year for edition in (of(course) if among is None else among)}
    return [year for year in years(today) if year not in have]


def of(course):
    """Every edition of `course`'s series, newest first. Always includes `course` itself."""
    if not course.series:
        return [course]
    return sorted(Course.objects.filter(series=course.series), key=_recency, reverse=True)
