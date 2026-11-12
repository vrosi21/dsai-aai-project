from django.db import models
from django.urls import reverse

from .base import CourseItem
from .visibility import Shown


class Document(CourseItem, Shown):
    """A page of prose written in Markdown: lecture notes, a lab, a homework, a guide."""

    class Kind(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LAB = "lab", "Lab"
        HOMEWORK = "homework", "Homework"
        GUIDE = "guide", "Guide"
        SHEET = "sheet", "Cheat sheet"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        COMPLETE = "complete", "Complete"

    # URL segment and plural label for each kind that has a list page.
    SECTIONS = {
        Kind.LECTURE: ("lectures", "Lectures"),
        Kind.LAB: ("labs", "Labs"),
        Kind.HOMEWORK: ("homework", "Homework"),
        Kind.GUIDE: ("guides", "Guides"),
    }

    # Name of the URL pattern for each kind's list page (see catalog/urls.py).
    LIST_URL_NAMES = {
        Kind.LECTURE: "lectures",
        Kind.LAB: "labs",
        Kind.HOMEWORK: "homework_list",
        Kind.GUIDE: "guides",
    }

    kind = models.CharField(max_length=10, choices=Kind)
    slug = models.SlugField(max_length=80)
    number = models.PositiveSmallIntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    date = models.DateField(null=True, blank=True)
    due = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.COMPLETE)
    topics = models.ManyToManyField("catalog.Topic", blank=True, related_name="documents")

    # Rendered once by sync_content, so a page view is a template render and nothing more.
    body_html = models.TextField(blank=True)
    takeaways = models.JSONField(default=list, blank=True)
    toc = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    reading_minutes = models.PositiveSmallIntegerField(default=0)
    search_text = models.TextField(blank=True)

    class Meta(CourseItem.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["course", "kind", "slug"], name="catalog_document_course_kind_slug"
            )
        ]

    def __str__(self):
        return self.label

    @property
    def label(self):
        if self.number is not None and self.kind != self.Kind.GUIDE:
            return f"{self.get_kind_display()} {self.number} · {self.title}"
        return self.title

    @property
    def progress_key(self):
        return f"doc:{self.kind}:{self.slug}"

    def get_absolute_url(self):
        if self.kind == self.Kind.SHEET:
            return reverse("catalog:cheatsheet", args=[self.course.slug])
        return reverse(f"catalog:{self.kind}", args=[self.course.slug, self.slug])
