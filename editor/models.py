"""The editor keeps one thing of its own: images that were uploaded into a form and are
not part of a course yet. Everything else it shows comes from the course's files, and
everything it saves goes through desk.handins."""

from django.conf import settings
from django.db import models


class Upload(models.Model):
    """An image someone added in the editor, already made fit for a course
    (editor/images.py), waiting for the text that shows it to be saved. Then it joins
    the course as a file like any other; until then, or if that never happens, it is
    here, for a week."""

    slug = models.SlugField(max_length=64)  # the course (one year of it)
    name = models.CharField(max_length=80)  # "<hash of its bytes>.webp"
    data = models.BinaryField()
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    # CASCADE: an upload that never became part of a course is the person's, and goes
    # with their account.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    # auto_now: set again whenever the same image is uploaded again, so that the
    # week it is kept for counts from the last time someone wanted it.
    added_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["slug", "name"], name="editor_upload_slug_name")
        ]

    def __str__(self):
        return f"{self.slug}/{self.name}"
