"""The pages of the desk: Ask or report, and everything about educators."""

from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from catalog import editions
from catalog import visibility as shown
from catalog.mine import by_semester, catalogue
from catalog.models import Course, Visibility
from catalog.nav import course_data, has_content
from catalog.welcome import educator_state
from ingest import handin, live

from . import handins, kit, mail, notices
from .access import may_write
from .forms import REQUEST_FORMS, EducatorApplicationForm
from .models import Educator, EducatorApplication, HandIn, Request
from .notify import notify
from .switches import switches

# The most one person may send in a day. Far above honest use; it keeps one account from
# filling the owner's list (and phone).
DAILY_REQUESTS = 10
# And the most hand-ins. Each one is checked in full and kept.
DAILY_HANDINS = 20


def signed_in(view):
    """These pages write rows that belong to a person. On the open site, where nobody is
    signed in, they lead back to the start."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("catalog:home")
        return view(request, *args, **kwargs)

    return wrapper


def _titles(request):
    """series -> course, for every course a visitor can see."""
    return {course.series: course for course in catalogue(request)}


def _admin_url(request, obj):
    name = f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change"
    return request.build_absolute_uri(reverse(name, args=[obj.pk]))


def _tell_owner(title, message, *, url, level, key):
    """The owner's bot, and where mail is set up the site's own address: that something
    arrived, and the link to it in the admin. Never who sent it or what it says.

    Call it from transaction.on_commit: after the row is safely stored, never before.
    """
    notify(title, message, level=level, url=url, dedupe_key=key)
    mail.send_owner(title, message, url)


def _course_of(path, courses):
    """The course a page belongs to, read from its address (/courses/rl/cards/…)."""
    parts = path.strip("/").split("/")
    slug = parts[1] if len(parts) > 1 and parts[0] == "courses" else ""
    return next((course for course in courses if course.slug == slug), None)


@signed_in
def ask(request):
    """Ask for content, report a fault, or suggest something.

    Three forms, one per kind (desk/forms.py), because the three need different things
    asked. The page shows one at a time: `?kind=` says which, and a form posts its own
    kind back, so the same view checks it with the same class that drew it.
    """
    courses = catalogue(request)
    by_series = _titles(request)
    wanted = request.POST.get("kind") or request.GET.get("kind", "")
    kind = wanted if wanted in REQUEST_FORMS else Request.Kind.CONTENT
    form_class = REQUEST_FORMS[kind][0]
    # Where they came from, and the course that was on their screen: kept when they
    # switch to another kind, and used to fill in what the form can already know.
    came_from = (request.POST.get("page") or request.GET.get("from", ""))[:300]
    named = request.POST.get("series") or request.GET.get("course", "")
    about = next((c for c in courses if named in (c.slug, c.series)), None)
    about = about or _course_of(came_from, courses)
    today = Request.objects.filter(
        user=request.user, created_at__gte=timezone.now() - timedelta(days=1)
    ).count()

    if request.method == "POST":
        form = form_class(request.POST, courses=courses)
        if today >= DAILY_REQUESTS:
            form.add_error(None, "You have sent a lot today. Come back tomorrow.")
        elif form.is_valid():
            # commit=False: build the row without saving, to add what the form does not
            # carry (who is asking), then save.
            sent = form.save(commit=False)
            sent.user = request.user
            sent.save()
            course = by_series.get(sent.series)
            title = {
                Request.Kind.CONTENT: "New request: content",
                Request.Kind.BUG: "New report: something is wrong",
                Request.Kind.IDEA: "New idea for the site",
            }[sent.kind] + (f" ({course.title})" if course else "")
            url = _admin_url(request, sent)
            # Under the bell: the owner, and for a course whoever writes it. They are
            # told what kind of thing arrived, not by whom.
            notices.tell(
                notices.owners(), title, url=reverse("admin:desk_request_change", args=[sent.pk])
            )
            heard = {
                Request.Kind.CONTENT: "Content asked for in {course}",
                Request.Kind.BUG: "Something reported wrong in {course}",
            }.get(sent.kind)
            if course and heard:
                notices.tell(
                    notices.educators_of(sent.series, but=request.user),
                    heard.format(course=course.title),
                    "Read it, and answer it, on the course's page.",
                    url=_desk_url(sent.series, "asked"),
                )
            # After the row is safely stored, and never before: see desk/notify.py.
            transaction.on_commit(
                lambda: _tell_owner(
                    title,
                    "Sent with Ask or report. Open the admin to read it.",
                    level="warning" if sent.kind == Request.Kind.BUG else "info",
                    url=url,
                    key=f"dsai-request-{sent.pk}",
                )
            )
            return redirect(f"{reverse('desk:ask')}?sent=1")
    else:
        form = form_class(
            courses=courses,
            initial={"series": about.series if about else "", "page": came_from},
        )

    keep = {"course": about.series if about else "", "from": came_from}
    keep = {name: value for name, value in keep.items() if value}
    kinds = [
        {
            "value": value,
            "label": value.label,
            "hint": hint,
            # urlencode() builds the part after the "?", escaping whatever needs it.
            "url": f"{reverse('desk:ask')}?{urlencode({'kind': value, **keep})}",
            "current": value == kind,
        }
        for value, (_, hint) in REQUEST_FORMS.items()
    ]
    mine = list(Request.objects.filter(user=request.user)[:10])
    for item in mine:
        item.course = by_series.get(item.series)
    return render(
        request,
        "desk/ask.html",
        {
            "form": form,
            "kind": kind,
            "kinds": kinds,
            "mine": mine,
            "sent": "sent" in request.GET,
            "active": "ask",
        },
    )


def _open_to(request, writes=()):
    """The courses someone can offer to write: every open course they do not write
    already, by semester and then in the order of the dashboard. A course may have any
    number of educators, so one that has some is offered too; each is marked `vacant`
    when nobody writes it yet."""
    gate = switches(request)
    courses = [course for course in catalogue(request) if course.series not in writes]
    for course in courses:
        course.vacant = not gate.has_educator(course)
    return sorted(courses, key=lambda c: (c.semester or 99, c.order, c.title))


def _seeking(request, writes=()):
    """Those of them nobody is writing yet."""
    return [course for course in _open_to(request, writes) if course.vacant]


def _writes(user):
    return set(Educator.objects.filter(user=user).values_list("series", flat=True))


@signed_in
def apply(request):
    """Offer to write one course or several. Each course is an application of its own,
    answered on its own; sending the form again for a course updates that application."""
    courses = _open_to(request, _writes(request.user))
    mine = {offer.series: offer for offer in EducatorApplication.objects.filter(user=request.user)}
    asked = request.GET.get("course", "")
    wanted = next((c.series for c in courses if asked in (c.series, c.slug)), "")
    # Asked for by name, and not among those that can be applied for.
    unavailable = bool(asked) and not wanted and request.method == "GET"

    if request.method == "POST":
        form = EducatorApplicationForm(request.POST, courses=courses)
        if form.is_valid():
            # All of them or none: atomic() makes the saves inside it one transaction.
            with transaction.atomic():
                offers = form.offers(request.user, mine)
            by_series = {course.series: course for course in courses}
            for offer in offers:
                notices.tell(
                    notices.owners(),
                    f"Educator application: {by_series[offer.series].title}",
                    url=reverse("admin:desk_educatorapplication_change", args=[offer.pk]),
                )
            # The bot rings once for what one person sent at once.
            if len(offers) == 1:
                title = f"Educator application: {by_series[offers[0].series].title}"
                text = "Someone offers to write this course. Open the admin to read it."
                url = _admin_url(request, offers[0])
            else:
                title = f"Educator applications: {len(offers)} courses"
                text = (
                    "Someone offers to write "
                    + ", ".join(by_series[offer.series].title for offer in offers)
                    + ". Each is an application of its own in the admin."
                )
                url = request.build_absolute_uri(
                    reverse("admin:desk_educatorapplication_changelist") + "?status__exact=pending"
                )
            key = f"dsai-educator-{offers[0].pk}"
            transaction.on_commit(
                lambda: _tell_owner(title, text, level="success", url=url, key=key)
            )
            return redirect(f"{reverse('desk:educators')}?applied={len(offers)}")
    else:
        # What they said about themselves the last time is offered again; the note is
        # about one course, so it comes back only with that course's own application.
        last = mine.get(wanted) or next(iter(mine.values()), None)
        initial = {
            "series": [wanted] if wanted else [],
            "name": last.name if last else request.user.get_full_name(),
            "phone": last.phone if last else "",
            "year": last.year if last else None,
            "tools": last.tools if last else EducatorApplication.Tools.UNSURE,
            "note": mine[wanted].note if wanted in mine else "",
        }
        form = EducatorApplicationForm(initial=initial, courses=courses)

    ticked = set(form["series"].value() or [])
    for course in courses:
        course.ticked = course.series in ticked
        course.offer = mine.get(course.series)
    return render(
        request,
        "desk/apply.html",
        {
            "form": form,
            "groups": by_semester(courses),
            "existing": mine.get(wanted),
            "unavailable": unavailable,
            "active": "educators",
        },
    )


def _written_by(series):
    """The current year of each of these courses, open to students or not."""
    return editions.latest(list(Course.objects.filter(series__in=series)))


_may_write = may_write  # the name these views have always used for it


def _desk_url(series, part=""):
    """The address of a course's own page for its educators, and of a part of it."""
    return reverse("desk:course", args=[series]) + (f"#{part}" if part else "")


def _copy_state(role, course):
    """How this person's copy of the course stands against the site.

    "none": never downloaded. "current": what they last took from the site, or gave it,
    is what the site has. "differs": the course on the site is another version now
    (someone else handed in, or a hand-in was taken back), so a hand-in built on that
    copy would be refused (desk/handins.py); better to know before the work than after.
    """
    if role is None or not role.synced_version:
        return "none"
    folder = live.folder(course.slug)
    if folder is None:
        return "none"
    return "current" if role.synced_version == handin.content_version(folder) else "differs"


def _desks(request, roles, *, handed="", refused=None):
    """The courses of `roles` ({series: Educator or None}) with everything their pages
    show: what is on the site, what was asked, what was handed in, how the copy stands.

    Read once for all of them: one query for the requests, one for the hand-ins.
    """
    series = list(roles)
    asked = Request.objects.filter(series__in=series, status=Request.Status.NEW).exclude(
        kind=Request.Kind.IDEA
    )
    # light(): what each step kept stays in the database; a list needs its summary.
    steps = HandIn.objects.filter(series__in=series).light().select_related("user")
    desks = []
    # Their courses are read directly, not from the catalogue: an educator may be
    # writing a course that students cannot see yet.
    for course in _written_by(series):
        name = course.series
        course.counts = course_data(course)["counts"]
        course.ready = has_content(course)
        course.asked = [item for item in asked if item.series == name]
        course.timeline = [step for step in steps if step.series == name][:30]
        course.last = course.timeline[0] if course.timeline else None
        # The newest step, if it is on the site and theirs, can be taken back by them.
        newest = next((s for s in course.timeline if s.status == HandIn.Status.LIVE), None)
        course.undo = newest if newest and newest.user_id == request.user.pk else None
        course.handed = next((s for s in course.timeline if str(s.pk) == handed), None)
        course.refused = refused if refused and name in roles else None
        course.copy = _copy_state(roles[name], course)
        course.desk_url = _desk_url(name)
        desks.append(course)
    return sorted(desks, key=lambda c: (c.semester or 99, c.order, c.title))


def _waiting(desks):
    """What an educator should look at first, most pressing first: questions people are
    waiting on, then a copy that is no longer what the site has, then a course with
    nothing in it (unless it is already named for one of the others)."""
    rows = []
    for course in desks:
        count = len(course.asked)
        if count:
            said = "1 request to answer" if count == 1 else f"{count} requests to answer"
            rows.append({"course": course, "text": said, "url": _desk_url(course.series, "asked")})
    for course in desks:
        if course.copy == "differs":
            rows.append(
                {
                    "course": course,
                    "text": "Your copy is not what the site has",
                    "url": _desk_url(course.series, "files"),
                }
            )
    named = {row["course"].pk for row in rows}
    for course in desks:
        if not course.ready and not course.timeline and course.pk not in named:
            rows.append(
                {"course": course, "text": "Nothing written yet", "url": _desk_url(course.series)}
            )
    return rows


def _offers(request):
    """This person's applications, each with the course it is for."""
    by_series = _titles(request)
    offers = list(EducatorApplication.objects.filter(user=request.user))
    for offer in offers:
        offer.course = by_series.get(offer.series)
    return [offer for offer in offers if offer.course]


def _become(request, writes=()):
    """The page for someone who writes no course (yet): what it takes, the courses
    looking for someone, and their own applications."""
    offers = _offers(request)
    mine = {offer.series: offer for offer in offers}
    courses = _open_to(request, writes)
    for course in courses:
        course.offer = mine.get(course.series)
    return render(
        request,
        "desk/become.html",
        {
            "offers": offers,
            "courses": courses,
            "seeking": [course for course in courses if course.vacant],
            "writes": bool(writes),
            "applied": request.GET.get("applied"),
            "active": "educators",
        },
    )


@signed_in
def educators(request):
    """Educators, in the menu. For someone who writes courses it is their dashboard: what
    is waiting, and a card per course that leads to that course's own page. For everyone
    else it says what an educator does and which courses need one."""
    roles = {role.series: role for role in Educator.objects.filter(user=request.user)}
    if not roles:
        return _become(request)
    desks = _desks(request, roles)
    state = educator_state(request.user)
    # Applications that are still open; an accepted one is a course above.
    offers = [o for o in _offers(request) if o.status != EducatorApplication.Status.ACCEPTED]
    return render(
        request,
        "desk/educators.html",
        {
            "desks": desks,
            "waiting": _waiting(desks),
            "offers": offers,
            # Every open course they do not write can be applied for; how many of
            # those have nobody at all is said beside the button.
            "more": len(_open_to(request, roles)),
            "seeking": len(_seeking(request, roles)),
            "kit": settings.EDUCATOR_KIT_URL,
            "kit_version": kit.VERSIONS[0] if kit.VERSIONS else None,
            # Versions of the kit this person has not been told about yet.
            "kit_news": kit.news_for(state["kit_seen"]),
            "applied": request.GET.get("applied"),
            "welcome": {"educator": True, "educator_tour": state["offer"]},
            "active": "educators",
        },
    )


@signed_in
def open_courses(request):
    """The courses nobody writes yet, for someone who already writes one and may take
    another. (Someone who writes none sees the same page under Educators.)"""
    return _become(request, writes=_writes(request.user))


def _course_page(request, series, *, refused=None):
    """One course, for the people who write it: the form that takes a hand-in, what was
    handed in so far, their copy of the files, what was asked.

    `refused` is a handins.Refused when a hand-in has just been turned down: the page is
    shown again with the reason above the form.
    """
    if not _may_write(request.user, series):
        raise Http404
    role = Educator.objects.filter(user=request.user, series=series).first()
    desks = _desks(request, {series: role}, handed=request.GET.get("handed", ""), refused=refused)
    if not desks:
        raise Http404
    return render(
        request,
        "desk/course.html",
        {
            "course": desks[0],
            "kit": settings.EDUCATOR_KIT_URL,
            "taken_back": "taken_back" in request.GET,
            "welcome": {"educator": role is not None},
            "active": "educators",
        },
    )


@signed_in
def course(request, series):
    return _course_page(request, series)


@signed_in
def guide(request):
    """Writing a course, start to finish: the same steps as the kit's README, on the site,
    with the address of the kit. Anyone signed in may read it: it is also what someone
    thinking of applying wants to know."""
    return render(
        request,
        "desk/guide.html",
        {
            "kit": settings.EDUCATOR_KIT_URL,
            "kit_version": kit.VERSIONS[0] if kit.VERSIONS else None,
            "welcome": {"educator": educator_state(request.user)["educator"]},
            "active": "educators",
        },
    )


@signed_in
@require_POST
def files(request, series):
    """The files of a course as one zip (course.yaml and content/), for the person who
    writes it: what they unpack into their copy of the educator kit.

    It is what the site shows at this moment, hand-ins included, so it is never more than
    they can already see, and raw material is not part of it: there is none on the
    server. A POST, because it also records which version this person now has.
    """
    current = _written_by([series]) if _may_write(request.user, series) else []
    folder = live.folder(current[0].slug) if current else None
    if folder is None:
        raise Http404
    handins.remember(request.user, series, handin.content_version(folder))
    response = HttpResponse(handin.pack(folder), content_type="application/zip")
    # "attachment" makes the browser save the file instead of trying to show it.
    response["Content-Disposition"] = f'attachment; filename="{folder.name}.zip"'
    return response


@signed_in
@require_POST
def hand_in(request, series):
    """Take in the zip an educator uploads for their course (desk/handins.py). Accepted,
    it is on the site before the next page loads; refused, the page says exactly why."""
    current = _written_by([series]) if _may_write(request.user, series) else []
    if not current:
        raise Http404
    course = current[0]
    upload = request.FILES.get("zip")
    try:
        if upload is None:
            raise handins.Refused("bad", ["No file arrived. Choose the zip that pack.py made."])
        today = HandIn.objects.filter(
            user=request.user, created_at__gte=timezone.now() - timedelta(days=1)
        ).count()
        if today >= DAILY_HANDINS:
            raise handins.Refused("busy", ["That is a lot of hand-ins for one day. Try tomorrow."])
        # The box on the form: "what this removes or rewrites is meant".
        confirmed = request.POST.get("removals") == "yes"
        record = handins.take(request.user, course, upload, confirmed=confirmed)
    except handins.Refused as refusal:
        return _course_page(request, series, refused=refusal)

    url = _admin_url(request, record)
    transaction.on_commit(
        lambda: _tell_owner(
            f"Hand-in on the site: {course.title}",
            f"{record.summary}. It is live. Open the admin to see it or to take it back.",
            # One that removed something is worth a look sooner.
            level="warning" if record.removes else "success",
            url=url,
            key=f"dsai-handin-{record.pk}",
        )
    )
    return redirect(f"{reverse('desk:course', args=[series])}?handed={record.pk}#hand-in")


@signed_in
@require_POST
def take_back(request, pk):
    """An educator takes back their own hand-in, if it is the newest one on the site
    for its course: the course returns to the version before it. (The owner can take
    off any hand-in, in the admin.)"""
    record = get_object_or_404(HandIn, pk=pk, status=HandIn.Status.LIVE)
    newest = HandIn.objects.filter(slug=record.slug, status=HandIn.Status.LIVE).first()
    if record.user_id != request.user.pk or newest.pk != record.pk:
        raise Http404
    handins.withdraw(record, by=request.user)
    notices.tell(
        notices.owners(),
        f"A hand-in for `{record.slug}` was taken back by its educator",
        url=reverse("admin:desk_handin_change", args=[record.pk]),
    )
    return redirect(f"{reverse('desk:course', args=[record.series])}?taken_back=1#history")


@signed_in
def handin_zip(request, pk):
    """One step of a course's history as a zip: the whole course as it stood after it.

    For the course's educators it is the backup: an earlier version to look at, or to
    unpack and hand in again. For the owner it is what `manage.py import_course` takes;
    theirs carries a note saying which version of their own files it was built on.
    """
    record = get_object_or_404(HandIn, pk=pk)
    if not _may_write(request.user, record.series):
        raise Http404
    # The zip is made here, from the files the step kept: the same files always give
    # the same zip (ingest.handin.pack_files), so it is as if it had been stored.
    note = None
    if request.user.is_staff:
        note = {"handin": record.pk, "version": record.version, "base": handins.root_base(record)}
    data = handin.pack_files(record.slug, handins.kept(record), note)
    response = HttpResponse(data, content_type="application/zip")
    name = f"{record.slug}-version-{record.pk}.zip"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@signed_in
@require_POST
def resolve(request, pk):
    """An educator marks a request about their course as done, with a line of answer."""
    item = get_object_or_404(Request, pk=pk)
    if not (item.series and _may_write(request.user, item.series)):
        raise Http404
    item.status = Request.Status.DONE
    item.answer = request.POST.get("answer", "").strip()[:1000]
    item.save(update_fields=["status", "answer"])
    answered(item)
    return redirect(_desk_url(item.series, "asked"))


def answered(item):
    """Tell the person who sent a request that it now has an answer or a new state."""
    notices.tell(
        [item.user],
        f"Your request is {item.get_status_display().lower()}",
        item.answer or f"{item.get_kind_display()}: {item.message[:120]}",
        url=reverse("desk:ask"),
    )


@signed_in
def notifications(request):
    """Everything under the bell, for reading at length."""
    rows = list(request.user.notifications.all()[:100])
    return render(request, "desk/notifications.html", {"rows": rows, "active": "notifications"})


@signed_in
@require_POST
def notifications_read(request):
    """Posted when the bell is opened (static/js/components/bell.js): what was shown
    counts as read, on every device."""
    notices.mark_read(request.user)
    return HttpResponse(status=204)


@require_POST
def visibility(request, course):
    """The owner making one item of a course public, unlisted or private (its page has the
    switch). Anyone else is told there is no such page, as for everything private."""
    if not request.user.is_staff:
        raise Http404
    course = get_object_or_404(Course, slug=course)
    item = shown.find(course, request.POST.get("ref", ""))
    state = request.POST.get("state", "")
    if item is None or state not in Visibility.values:
        raise Http404
    try:
        shown.switch(course, item, state)
    except shown.Locked:
        # What is in private/ stays private: that folder may hold graded work.
        return HttpResponse("This is in the private folder and stays private.", status=409)
    return redirect(item.get_absolute_url())
