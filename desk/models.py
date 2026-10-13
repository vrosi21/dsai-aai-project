"""The owner's desk: what people send in, who writes which course, and the owner's switches.

Like accounts and progress, these rows exist nowhere else. A course is named by its
`series`, as text, for the same reasons as in accounts.Enrollment: the course tables are a
cache of files that can be rebuilt, and a series outlives one academic year of a course.
"""

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone

from ingest import handin


class Request(models.Model):
    """Something a person sent to whoever runs the site."""

    class Kind(models.TextChoices):
        CONTENT = "content", "Content for a course"
        BUG = "bug", "Something is wrong"
        IDEA = "idea", "An idea for the site"

    class Status(models.TextChoices):
        NEW = "new", "New"
        DONE = "done", "Done"
        DECLINED = "declined", "Not planned"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="requests"
    )
    kind = models.CharField(max_length=10, choices=Kind)
    series = models.SlugField(max_length=64, blank=True)  # the course it is about, if any
    message = models.TextField(max_length=2000)
    # The page they were on when they opened the form: where a fault was seen.
    page = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.NEW)
    # A line back from the owner (or the course's educator), shown to the person.
    answer = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        about = f" ({self.series})" if self.series else ""
        return f"{self.get_kind_display()}{about}"


class EducatorApplication(models.Model):
    """Someone offering to write a course. One per person and course; sending the form
    again updates it."""

    class Year(models.TextChoices):
        FIRST = "1", "1st year"
        SECOND = "2", "2nd year"
        THIRD = "3", "3rd year"
        OTHER = "other", "Finished, or other"

    class Tools(models.TextChoices):
        PAID = "paid", "A paid AI assistant (Claude, ChatGPT, Gemini or similar)"
        FREE = "free", "A free AI assistant"
        HAND = "hand", "By hand"
        UNSURE = "unsure", "Not sure yet"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for an answer"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="educator_applications"
    )
    series = models.SlugField(max_length=64)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40)
    year = models.CharField(max_length=10, choices=Year)
    tools = models.CharField(max_length=10, choices=Tools, default=Tools.UNSURE)
    note = models.TextField(max_length=1000, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "series"], name="desk_application_unique")
        ]

    def __str__(self):
        return f"{self.name} for {self.series}"

    @transaction.atomic
    def decide(self, status):
        """Answer the application. Accepting it is what makes the person the educator of
        the course; taking an acceptance back takes the role back with it.

        Both changes happen together or not at all (`transaction.atomic`), so there is
        never an accepted application without its educator.
        """
        self.status = status
        self.decided_at = None if status == self.Status.PENDING else timezone.now()
        self.save(update_fields=["status", "decided_at"])
        from . import notices

        if status != self.Status.PENDING:
            word = "accepted" if status == self.Status.ACCEPTED else "not accepted"
            notices.tell(
                [self.user],
                f"Your application to write `{self.series}` was {word}",
                "Your page is Educators in the menu." if word == "accepted" else "",
                url="/educators/",
            )
        role = Educator.objects.filter(user=self.user, series=self.series)
        if status == self.Status.ACCEPTED:
            _, new = Educator.objects.get_or_create(user=self.user, series=self.series)
            if new:
                # After the commit, so that the mail is never about a role that was
                # not given after all.
                transaction.on_commit(self._welcome)
        else:
            role.delete()

    def _welcome(self):
        from . import mail

        mail.send(
            [self.user],
            "You are an educator on DSAI Vault",
            f"Your application to write `{self.series}` was accepted.\n\n"
            "The next time you open the site it shows you how writing a course works. "
            "Your page is Educators in the menu; the guide has every step, from getting "
            f"the kit to handing in your work:\n\n{settings.SITE_URL}/educators/guide/",
        )


class Educator(models.Model):
    """The role: this person writes this course."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="educating"
    )
    series = models.SlugField(max_length=64)
    since = models.DateTimeField(auto_now_add=True)
    # The version of the course (its content hash) this person last took from the site
    # or put on it. When a hand-in is refused, it tells "you removed something" apart
    # from "the course moved on since your copy" (desk/handins.py).
    synced_version = models.CharField(max_length=64, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "series"], name="desk_educator_unique")
        ]

    def __str__(self):
        return f"{self.user} writes {self.series}"


class CourseSetting(models.Model):
    """The owner's switches for one course. Each has three positions: empty means "as the
    course's own file says", and yes or no overrides the file until it is emptied again.

    They live here and not on catalog.Course because that table is rebuilt from files.
    """

    series = models.SlugField(max_length=64, unique=True)
    # Whether students see the course at all (course.yaml: `enabled`).
    enabled = models.BooleanField(null=True, blank=True)
    # Whether someone is writing it, so its page need not ask (course.yaml: `has_educator`).
    has_educator = models.BooleanField(null=True, blank=True)

    def __str__(self):
        return self.series


class HandIn(models.Model):
    """What an educator did to a course, taken in by the site (desk/handins.py).

    One row is one step in the history of a course: who, when, what was added, changed
    and removed. `archive` is the whole course as it stood after that step, as a zip.
    That copy is the hand-in itself, and it is also the backup: the folder the site reads
    the course from is unpacked from it, and any earlier step can be fetched and handed
    in again.
    """

    class Status(models.TextChoices):
        LIVE = "live", "On the site"
        MERGED = "merged", "In the published files"
        WITHDRAWN = "withdrawn", "Taken off the site"

    class Via(models.TextChoices):
        KIT = "kit", "Handed in as a zip"
        SITE = "site", "Edited on the site"

    # SET_NULL: when a person deletes their account, what they added to a course stays,
    # without their name.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="handins"
    )
    series = models.SlugField(max_length=64)
    slug = models.SlugField(max_length=64)  # the year of the course it was handed in to
    status = models.CharField(max_length=10, choices=Status, default=Status.LIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)  # when it was merged or withdrawn
    # The fingerprint of the course before this step and after it. A hand-in is taken
    # only if it was built on the version the site has, so these link up into a chain.
    base_version = models.CharField(max_length=64, blank=True)
    version = models.CharField(max_length=64)
    new_files = models.JSONField(default=list)  # names
    removed_files = models.JSONField(default=list)  # names
    grown = models.JSONField(default=dict)  # name -> lines added
    shrunk = models.JSONField(default=dict)  # name -> lines removed or rewritten
    removed_ids = models.JSONField(default=list)  # ids that disappeared with it
    lines_added = models.PositiveIntegerField(default=0)
    lines_removed = models.PositiveIntegerField(default=0)
    archive = models.BinaryField()
    # How it came: a zip made by the kit, or the editor on the site. Editing on the site
    # is saved many times in a sitting; those saves are one step (desk/handins.py), which
    # is what `updated_at` (the last of them) and `saves` (how many) are about.
    via = models.CharField(max_length=4, choices=Via, default=Via.KIT)
    updated_at = models.DateTimeField(null=True, blank=True)
    saves = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["slug", "status"])]

    def __str__(self):
        return f"{self.slug} #{self.pk}: {self.summary}"

    @property
    def summary(self):
        if self.version == self.base_version:
            # Only a step made in the editor can end where it began: a zip that changes
            # nothing is not taken in.
            return "changed, and changed back"
        return handin.summary(
            len(self.new_files), self.lines_added, self.lines_removed, len(self.removed_files)
        )

    @property
    def removes(self):
        """Whether this step took something away: shown differently, looked at first."""
        return bool(self.lines_removed or self.removed_files)


class KitAnnouncement(models.Model):
    """The owner told educators that a version of the educator kit is out (desk/kit.py).
    Until a version is announced, nobody is told to pull it."""

    number = models.PositiveIntegerField(unique=True)
    announced_at = models.DateTimeField(auto_now_add=True)
    recipients = models.PositiveIntegerField(default=0)  # how many were e-mailed

    class Meta:
        ordering = ["-number"]

    def __str__(self):
        return f"Kit version {self.number}"


# Where a line under the bell may lead: a page of this site ("/courses/rl/", but not
# "//elsewhere") or a full https address. Nothing else is a link, so what the owner types
# into an announcement can never become a script in somebody's browser.
site_link = RegexValidator(
    r"^(/(?!/)\S*|https://\S+)$",
    "Write a page of this site, like /courses/rl/, or a full address that starts with https://",
)


class Announcement(models.Model):
    """Something the owner tells people, written in the admin (desk/admin.py).

    Saving one leaves a notification under the bell of everyone it is for. Those rows
    point back here, which is what makes an announcement correctable: changing it changes
    every copy, and deleting it takes every copy away (desk/notices.py).
    """

    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Students and educators"
        EDUCATORS = "educators", "Educators only"

    title = models.CharField(
        max_length=160, help_text="One line. It is what people see first, under the bell."
    )
    body = models.TextField(
        "text",
        max_length=1000,
        blank=True,
        help_text="A few sentences at most. Plain text; a new line stays a new line.",
    )
    url = models.CharField(
        "link",
        max_length=300,
        blank=True,
        validators=[site_link],
        help_text="Optional: what opens when it is clicked. A page of this site, like "
        "/courses/rl/, or a full address that starts with https://",
    )
    audience = models.CharField("for", max_length=10, choices=Audience, default=Audience.EVERYONE)
    # The course whose people it is for, by its series, as text like everywhere in this
    # app. Empty: it is not about one course, and goes to everyone the line above names.
    series = models.SlugField("course", max_length=64, blank=True)
    created_at = models.DateTimeField("announced", auto_now_add=True)
    recipients = models.PositiveIntegerField("told", default=0)  # how many people

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Notification(models.Model):
    """A line the site left for one person: an answer arrived, a course they write
    changed, a new version of the kit is out, the owner announced something. Shown under
    the bell at the top of every page (desk/notices.py)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=1000, blank=True)
    url = models.CharField(max_length=300, blank=True)  # a path on this site
    # Set for a copy of an announcement. CASCADE: when the announcement is deleted, the
    # database deletes its copies with it, which is how one is taken back.
    announcement = models.ForeignKey(
        Announcement, null=True, blank=True, on_delete=models.CASCADE, related_name="copies"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return self.title
