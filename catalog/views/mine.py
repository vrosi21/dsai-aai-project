"""Choosing courses: the page shown after the first sign-in, and the button on a course."""

from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Enrollment
from accounts.views import safe_next
from progress.models import Record

from ..mine import catalogue, chosen
from ..models import Course
from ..nav import course_data, has_content
from .home import dashboard_groups


def _studied(user):
    """Series this person already has progress in.

    Offered ready-ticked to someone who used the site before courses could be chosen, so
    that their dashboard does not start out empty.
    """
    slugs = Record.objects.filter(user=user).values_list("course", flat=True).distinct()
    return set(Course.objects.filter(slug__in=list(slugs)).values_list("series", flat=True))


def choose(request):
    """Tick the courses you are taking. One page for the first time and for every later change.

    A plain HTML form: the browser sends one `series` value per ticked box, the view checks
    them against the courses that exist, stores the choice and redirects. Redirecting after
    a successful POST is what keeps a reload from sending the form again.
    """
    if not request.user.is_authenticated:  # the open site has no dashboards to fill
        return redirect("catalog:home")

    courses = catalogue(request)
    available = {course.series for course in courses}
    picked = chosen(request)
    first_time = not picked
    error = ""

    if request.method == "POST":
        wanted = set(request.POST.getlist("series")) & available
        if wanted:
            # A choice whose course is not there right now is kept, not silently dropped.
            Enrollment.objects.replace(request.user, wanted | (picked - available))
            return redirect("catalog:home")
        error = "Tick at least one course. Your dashboard is made of them."
        ticked = set()
    else:
        ticked = picked or _studied(request.user)

    for course in courses:
        course.counts = course_data(course, request.user)["counts"]
        course.ready = has_content(course, request.user)
    groups, folded = dashboard_groups(courses)
    if folded:
        groups.append({"title": "Archived", "note": "", "courses": folded})
    return render(
        request,
        "catalog/choose.html",
        {
            "groups": groups,
            "ticked": ticked,
            "first_time": first_time,
            "error": error,
            "active": "choose",
        },
    )


@require_POST
def enroll(request):
    """Put one course on the signed-in person's dashboard, or take it off.

    Posted by the button on a course's own page; `next` says where to go back to.
    """
    series = request.POST.get("series", "")
    if request.user.is_authenticated:
        if request.POST.get("action") == "remove":
            Enrollment.objects.filter(user=request.user, series=series).delete()
        elif any(course.series == series for course in catalogue(request)):
            # Only a course that is there to be seen can be added. get_or_create:
            # pressing the button twice must not make two rows.
            Enrollment.objects.get_or_create(user=request.user, series=series)
    return redirect(safe_next(request))
