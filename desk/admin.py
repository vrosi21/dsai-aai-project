"""The owner's desk in the admin: what people sent, who offers to write a course, who does.

Unlike the course tables, these rows are real data and are worked on here: a request is
answered, an application is accepted or declined, an educator is added or removed. What a
person wrote is shown but cannot be edited; only the answer to it can.

The switches for a course (open or closed, has an educator or asks for one) are actions on
the Courses list (catalog/admin.py), beside the courses they are about.
"""

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from catalog import editions
from catalog.models import Course

from . import handins, kit, mail, notices
from .forms import course_choices
from .models import (
    Announcement,
    Educator,
    EducatorApplication,
    HandIn,
    KitAnnouncement,
    Request,
)


def course_titles():
    """series -> "CODE · Title", from the latest year of each course."""
    courses = editions.latest(list(Course.objects.all()))
    return {course.series: f"{course.code} · {course.title}" for course in courses}


class CourseColumn:
    """Shows the course a row is about by its title. Rows store the series as text, so
    the title is looked up; a series whose course is gone is shown as it is."""

    @admin.display(description="Course", ordering="series")
    def course(self, row):
        if not row.series:
            return "The site"
        return course_titles().get(row.series, row.series)

    @admin.display(description="From", ordering="user__email")
    def who(self, row):
        return row.user.get_full_name() or row.user.email or row.user.username


@admin.register(Request)
class RequestAdmin(CourseColumn, admin.ModelAdmin):
    list_display = ["created_at", "kind", "course", "excerpt", "who", "status"]
    list_filter = ["status", "kind", "series"]
    search_fields = ["message", "user__email", "user__first_name", "user__last_name"]
    list_select_related = ["user"]
    list_per_page = 50
    actions = ["mark_done", "mark_declined", "mark_new"]

    fieldsets = [
        (None, {"fields": ["kind", "course", "who", "contact", "created_at", "seen_on"]}),
        ("What they wrote", {"fields": ["message"]}),
        (
            "Your answer",
            {
                "fields": ["status", "answer"],
                "description": "The status and the answer are shown to the person on "
                "their Ask or report page.",
            },
        ),
    ]
    readonly_fields = ["kind", "course", "who", "contact", "created_at", "seen_on", "message"]

    def has_add_permission(self, request):
        return False  # requests are sent from the site, by the person asking

    @admin.display(description="Message")
    def excerpt(self, row):
        return row.message if len(row.message) <= 90 else f"{row.message[:90]}…"

    @admin.display(description="E-mail")
    def contact(self, row):
        return row.user.email or "-"

    @admin.display(description="Sent from the page")
    def seen_on(self, row):
        # Text that a browser sent: only ever shown, never made into a link.
        return row.page or "-"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.changed_data:  # the status or the answer: the person is told
            from .views import answered

            answered(obj)

    def _mark(self, request, queryset, status):
        from .views import answered

        rows = list(queryset.select_related("user"))
        count = queryset.update(status=status)
        if status != Request.Status.NEW:
            for row in rows:
                row.status = status
                answered(row)
        label = Request.Status(status).label.lower()
        self.message_user(request, f"Marked {count} request{'' if count == 1 else 's'} as {label}.")

    @admin.action(description="Mark as done")
    def mark_done(self, request, queryset):
        self._mark(request, queryset, Request.Status.DONE)

    @admin.action(description="Mark as not planned")
    def mark_declined(self, request, queryset):
        self._mark(request, queryset, Request.Status.DECLINED)

    @admin.action(description="Mark as new again")
    def mark_new(self, request, queryset):
        self._mark(request, queryset, Request.Status.NEW)


@admin.register(EducatorApplication)
class EducatorApplicationAdmin(CourseColumn, admin.ModelAdmin):
    list_display = ["created_at", "name", "course", "year", "tools", "phone", "status"]
    list_filter = ["status", "year", "tools", "series"]
    search_fields = ["name", "phone", "user__email"]
    list_select_related = ["user"]
    actions = ["accept", "decline"]

    fieldsets = [
        (None, {"fields": ["course", "name", "year", "phone", "contact"]}),
        ("How they would write it", {"fields": ["tools", "note"]}),
        (
            "Your answer",
            {
                "fields": ["status", "created_at", "decided_at"],
                "description": "Accepting makes this person the educator of the course: "
                "its page stops asking for one, and their Educators page shows them how "
                "to get the educator kit and the files of their course.",
            },
        ),
    ]
    readonly_fields = [
        "course",
        "name",
        "year",
        "phone",
        "contact",
        "tools",
        "note",
        "created_at",
        "decided_at",
    ]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Account")
    def contact(self, row):
        return f"{row.user.get_full_name() or row.user.username} · {row.user.email or 'no e-mail'}"

    def save_model(self, request, obj, form, change):
        # The one thing the form changes is the status, and a change of status is a
        # decision: it goes through decide(), which also gives or takes the role.
        if "status" in form.changed_data:
            obj.decide(obj.status)
        else:
            super().save_model(request, obj, form, change)

    def _decide(self, request, queryset, status, verb):
        for application in queryset:
            application.decide(status)
        count = queryset.count()
        self.message_user(
            request,
            f"{verb} {count} application{'' if count == 1 else 's'}.",
            messages.SUCCESS,
        )

    @admin.action(description="Accept: make them the educator of the course")
    def accept(self, request, queryset):
        self._decide(request, queryset, EducatorApplication.Status.ACCEPTED, "Accepted")

    @admin.action(description="Decline")
    def decline(self, request, queryset):
        self._decide(request, queryset, EducatorApplication.Status.DECLINED, "Declined")


class EducatorForm(forms.ModelForm):
    """Adding an educator by hand: the course is picked from the courses there are, not
    typed, so a slip of the keyboard cannot make someone the educator of nothing."""

    class Meta:
        model = Educator
        fields = ["user", "series"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"] = forms.ChoiceField(
            label="Course", choices=sorted(course_titles().items(), key=lambda pair: pair[1])
        )


@admin.register(Educator)
class EducatorAdmin(CourseColumn, admin.ModelAdmin):
    form = EducatorForm
    list_display = ["who", "course", "since"]
    list_filter = ["series"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "series"]
    list_select_related = ["user"]
    # A search box instead of a drop-down of every account (uses the Users admin's search).
    autocomplete_fields = ["user"]


@admin.register(HandIn)
class HandInAdmin(CourseColumn, admin.ModelAdmin):
    """What educators did to a course, step by step: a zip handed in, or a sitting of
    edits made on the site. Each one went live as it was made. From here it can be
    downloaded (to take into your own course files with import_course) and, if it should
    not be on the site, taken off again."""

    list_display = ["created_at", "course", "summary", "who", "via", "status"]
    list_filter = ["status", "via", "series"]
    search_fields = ["slug", "user__email", "user__first_name", "user__last_name"]
    list_select_related = ["user"]
    actions = ["take_off"]
    fields = [
        "course",
        "who",
        "via",
        "created_at",
        "updated_at",
        "saves",
        "status",
        "settled_at",
        "summary",
        "changed",
        "zip",
    ]
    readonly_fields = fields

    def get_queryset(self, request):
        # What each step kept would be read just to draw a list: leave it in the database.
        return super().get_queryset(request).light()

    def has_add_permission(self, request):
        return False  # hand-ins are uploaded on the site, by the educator

    def has_delete_permission(self, request, obj=None):
        return False  # a row is part of a course's history; taking it off is an action

    @admin.display(description="From", ordering="user__email")
    def who(self, row):
        if row.user is None:
            return "Someone who has left"
        return row.user.get_full_name() or row.user.email or row.user.username

    @admin.display(description="Files")
    def changed(self, row):
        # Not named `files`: that is a column of the model (every file the step kept),
        # and the admin shows a model's field in preference to a method of this name.
        new = [(name, "new") for name in row.new_files]
        grown = [(name, f"+{lines} lines") for name, lines in row.grown.items()]
        shrunk = [
            (name, f"{lines} lines removed or rewritten") for name, lines in row.shrunk.items()
        ]
        gone = [(name, "removed") for name in row.removed_files]
        rows = new + grown + shrunk + gone
        return format_html_join("", "<div><code>{}</code> {}</div>", rows) or "-"

    @admin.display(description="The course after it")
    def zip(self, row):
        return format_html(
            '<a href="{}">Download the zip</a> and take it in with '
            "<code>manage.py import_course</code>, then publish.",
            reverse("desk:handin_zip", args=[row.pk]),
        )

    @admin.action(description="Take off the site (and every later hand-in for the course)")
    def take_off(self, request, queryset):
        live = (
            queryset.filter(status=HandIn.Status.LIVE).select_related("user").order_by("created_at")
        )
        if not live:
            self.message_user(request, "None of these is on the site.", messages.WARNING)
            return
        for record in live:
            record.refresh_from_db()
            if record.status != HandIn.Status.LIVE:
                continue  # went with an earlier one of the same course
            report = handins.withdraw(record, by=request.user)
            if record.user:
                mail.send(
                    [record.user],
                    "A hand-in of yours was taken off DSAI Vault",
                    f"Your hand-in for `{record.slug}` of {record.created_at:%d %B %Y} "
                    f"({record.summary}) was taken off the site, together with any later "
                    "one for that course. Before you hand in again, download the course "
                    "files and put your work on top of them; you will be told what was "
                    "wrong.",
                )
            level = messages.SUCCESS if report is None or report.ok else messages.ERROR
            self.message_user(request, f"{record.slug}: taken off the site.", level)


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "body", "url", "audience", "series"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The model keeps the course as text. The form offers the courses there are, open
        # to students or not (one may be for the people writing a course before it opens).
        # The field is absent once it can no longer be changed.
        if "series" in self.fields:
            courses = editions.latest(list(Course.objects.all()))
            self.fields["series"] = forms.ChoiceField(
                label="Course",
                required=False,
                choices=course_choices(courses, "Every course"),
                help_text="Optional. Choose a course to tell only its people: those who have "
                "it on their dashboard and those who write it. With Educators only, those "
                "who write it.",
            )


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    """Something to tell people. Adding one is announcing it: it goes under the bell of
    everyone it is for, at once. Changing it corrects every copy, deleting it takes every
    copy away."""

    form = AnnouncementForm
    list_display = ["title", "audience", "course", "created_at", "recipients"]
    list_filter = ["audience", "series"]
    search_fields = ["title", "body"]

    @admin.display(description="Course", ordering="series")
    def course(self, row):
        return course_titles().get(row.series, row.series) if row.series else "Every course"

    def get_fieldsets(self, request, obj=None):
        said = (
            "Saving puts this under the bell of everyone it is for, at once. A mistake can "
            "be mended afterwards: change it here and every copy changes; delete it and it "
            "is gone from every bell. It says what you write and nothing else: no name is "
            "attached to it."
        )
        if obj is None:
            fields = ["title", "body", "url", "audience", "series"]
        else:
            fields = ["title", "body", "url", "audience", "course", "created_at", "recipients"]
        return [(None, {"fields": fields, "description": said})]

    def get_readonly_fields(self, request, obj=None):
        # Who it is for is decided when it is announced: afterwards the copies are out.
        return ["audience", "course", "created_at", "recipients"] if obj is not None else []

    def save_model(self, request, obj, form, change):
        # save_model is the admin's hook around saving one object: the place for what
        # must happen together with it.
        super().save_model(request, obj, form, change)
        if change:
            count = notices.correct(obj)
            self.message_user(request, f"Corrected under {count} bell(s).")
            return
        obj.recipients = notices.announce(obj)
        obj.save(update_fields=["recipients"])
        self.message_user(request, f"Announced: it is under {obj.recipients} bell(s) now.")


class KitAnnouncementForm(forms.ModelForm):
    class Meta:
        model = KitAnnouncement
        fields = ["number"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        told = set(KitAnnouncement.objects.values_list("number", flat=True))
        self.fields["number"] = forms.TypedChoiceField(
            label="Version",
            coerce=int,
            choices=[
                (version.number, f"{version.number}: {version.title}")
                for version in kit.VERSIONS
                if version.number not in told
            ],
            help_text="Announce a version only once the kit's repository has it: educators "
            "are told to run git pull.",
        )


@admin.register(KitAnnouncement)
class KitAnnouncementAdmin(admin.ModelAdmin):
    """Adding a row here is the announcement: educators see a notice on their page and
    are e-mailed. The versions themselves are written in desk/kit.py."""

    form = KitAnnouncementForm
    list_display = ["number", "announced_at", "recipients"]

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.recipients = kit.tell_educators(obj.number)
        super().save_model(request, obj, form, change)
        version = next(v for v in kit.VERSIONS if v.number == obj.number)
        notices.tell(
            notices.all_educators(),
            f"Educator kit version {obj.number}: run git pull",
            version.title,
            url="/educators/",
        )
        self.message_user(
            request,
            f"Educators now see the notice. {obj.recipients} e-mail(s) were sent"
            + ("." if mail.configured() else ": no mail server is configured."),
        )
