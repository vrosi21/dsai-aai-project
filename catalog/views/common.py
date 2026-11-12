from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.functional import SimpleLazyObject

from desk.access import may_write
from desk.switches import switches
from ingest import live
from ingest.discover import newest_mtime

from .. import editions
from ..models import Course, Visibility
from ..models.visibility import is_owner
from ..nav import course_data


def load_course(request, slug, active=""):
    """Fetch a course (404 if unknown) plus the context every page inside it shares.

    `active` is the key of the navigation entry to highlight. A course the owner has not
    opened yet is "no such page" to everyone but the people writing it and staff, who see
    it with a note saying so: that is how a course is looked at before it opens."""
    course = get_object_or_404(Course, slug=slug)
    is_open = switches(request).is_open(course)
    # One query for someone signed in who is not staff: do they write this course?
    can_edit = may_write(request.user, course.series or course.slug)
    if not is_open and not can_edit:
        raise Http404
    data = course_data(course, request.user)
    context = {
        "course": course,
        "hue": course.hue,
        "nav": data["nav"],
        "counts": data["counts"],
        "manifest": data["manifest"],
        "active": active,
        "closed": not is_open,
        # For the people who write the course: its pages offer to be edited, and show
        # their edit buttons once that is switched on (editor.views.switch keeps the
        # choice in the session).
        "can_edit": can_edit,
        "editing": can_edit and bool(request.session.get("editing")),
        # The owner sees everything, marked when it is not public, and on an item's page
        # the switch between public, unlisted and private (catalog/_shown.html).
        "owner": is_owner(request.user),
        "shown_choices": Visibility.choices,
        # The years this course exists in, newest first, for the switch at the top.
        # Lazy: only the course's front page shows the switch, so only it pays the query.
        "editions": SimpleLazyObject(lambda: editions.of(course)),
        # The years it could exist in and does not: offered to those who may add one.
        # Lazy as well, and worked out from the list above, so it costs no query more.
        "missing_years": SimpleLazyObject(
            lambda: editions.missing(course, among=list(context["editions"])) if can_edit else []
        ),
        "stale": _files_changed(course),
    }
    return course, context


def _files_changed(course):
    """Development aid: true when the course's files are newer than the last sync."""
    if not settings.DEBUG or course.synced_at is None:
        return False
    folder = live.folder(course.slug)
    return folder is not None and newest_mtime(folder) > course.synced_at.timestamp() + 1


def scope_topics(request, course):
    """Topics selected by ?scope=<exam> or ?topic=<id>, with a label; (None, "") when absent.

    Lets one page (cards, formulas, hooks…) be narrowed to what an exam covers."""
    scope, topic = request.GET.get("scope"), request.GET.get("topic")
    if scope:
        assessment = get_object_or_404(course.assessments.visible_to(request.user), slug=scope)
        return list(assessment.topics.all()), assessment.title
    if topic:
        found = get_object_or_404(course.topics, key=topic)
        return [found], found.title
    return None, ""
