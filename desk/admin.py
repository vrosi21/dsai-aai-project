"""The owner's desk in the admin: what people sent, who offers to write a course, who does.

Unlike the course tables, these rows are real data and are worked on here: a request is
answered, an application is accepted or declined, an educator is added or removed. What a
person wrote is shown but cannot be edited; only the answer to it can.

The switches for a course (open or closed, has an educator or asks for one) are actions on
the Courses list (catalog/admin.py), beside the courses they are about.
"""

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from catalog import editions
from catalog.models import Course

from .models import Educator, EducatorApplication, Request


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

    def _mark(self, request, queryset, status):
        count = queryset.update(status=status)
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
    search_fields = ["name", "phone", "github", "user__email"]
    list_select_related = ["user"]
    actions = ["accept", "decline"]

    fieldsets = [
        (None, {"fields": ["course", "name", "year", "phone", "contact", "github_profile"]}),
        ("How they would write it", {"fields": ["tools", "note"]}),
        (
            "Your answer",
            {
                "fields": ["status", "created_at", "decided_at"],
                "description": "Accepting makes this person the educator of the course: "
                "its page stops asking for one, and their Educators page shows them how "
                "to get the educator kit and the files of their course. If the kit's "
                "repository is private, inviting them to it on GitHub is a separate step.",
            },
        ),
    ]
    readonly_fields = [
        "course",
        "name",
        "year",
        "phone",
        "contact",
        "github_profile",
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

    @admin.display(description="GitHub")
    def github_profile(self, row):
        if not row.github:
            return "Not given"
        # format_html escapes the name; the form only lets through what GitHub allows.
        return format_html(
            '<a href="https://github.com/{0}" rel="noopener noreferrer">{0}</a>', row.github
        )

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
