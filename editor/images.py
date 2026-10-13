"""Images added in the editor: made fit for a course before they are kept.

Whatever someone uploads (a phone photo of a whiteboard, a screenshot, a plot) comes out
as one thing: a WebP no wider or taller than `SIDE`, turned the way it was taken, and
without the data a camera writes into a photo (where and when it was taken, on which
phone). That is done once, here, so a page never ships a 6 MB photo, and nobody
publishes their home address inside one.

    upload  ->  prepare()  ->  an Upload row (kept aside)  ->  the course, when the
                                                              text that shows it is saved

Kept aside, because an image belongs to a course only once some text refers to it: the
form is not saved yet when the picture is chosen. `attach` is what moves it: called on
the course's files as a save is about to be made, it adds the uploads the new text
refers to, into the `assets/` folder beside the file, which is where the format wants an
image. An upload nothing came to refer to is forgotten after `KEPT`.

A drawing as SVG is kept as it is (it is already small and sharp at any size), provided
it carries no script: the same rule as for an SVG in a hand-in.
"""

import hashlib
import io
import posixpath
import re
from datetime import timedelta

from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from ingest import handin

from .models import Upload

MAX_UPLOAD = 8 * 1024 * 1024  # bytes of the file as it is sent
MAX_PIXELS = 40_000_000  # width x height: a 40-megapixel photo, and no more
MAX_SVG = 1024 * 1024
SIDE = 1600  # the longer side of what is kept, in pixels
QUALITY = 82  # of a photo; a drawing is kept exactly when that is smaller
KEPT = timedelta(days=7)
FORMATS = ["JPEG", "PNG", "WEBP", "GIF"]
# An image in Markdown: ![what it shows](assets/name.webp). Only this folder, only a
# plain file name: what the editor itself writes.
REFERENCE = re.compile(r"\]\(\s*(assets/[A-Za-z0-9][A-Za-z0-9._-]*)")

# Pillow refuses to open what claims to be far larger than this (a "decompression
# bomb": a few kilobytes that unpack to gigabytes).
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class Unusable(ValueError):
    """The file cannot be taken as an image. The message says why, for the person."""


def _svg(data):
    if len(data) > MAX_SVG:
        raise Unusable("An SVG drawing can be 1 MB at most.")
    head = data[:2048].lower()
    if b"<svg" not in head or b"<!entity" in data.lower():
        raise Unusable("This is not an SVG drawing the site can show.")
    if handin.SVG_ACTIVE.search(data):
        raise Unusable("This SVG carries a script, which a course image may not.")
    return data, ".svg", 0, 0


def prepare(data):
    """An uploaded file as the image the course keeps: (bytes, ending, width, height).
    Raises Unusable for anything that is not a picture it can take."""
    if len(data) > MAX_UPLOAD:
        raise Unusable(f"An image can be {MAX_UPLOAD // 1024 // 1024} MB at most.")
    if data.lstrip()[:5].lower() in (b"<?xml", b"<svg ") or data.lstrip()[:4].lower() == b"<svg":
        return _svg(data)
    try:
        # `formats` keeps Pillow to the four kinds a browser's file picker offers; it
        # can read many more, each one more code that would meet untrusted bytes.
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        if image.width * image.height > MAX_PIXELS:
            raise Unusable("This image has more pixels than a 40-megapixel photo.")
        if getattr(image, "is_animated", False):
            raise Unusable("This is an animation. Upload a still image.")
        image.load()
    except Unusable:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise Unusable(
            "This is not an image the site can read (JPEG, PNG, WebP, GIF or SVG)."
        ) from error

    # A phone stores a portrait photo lying on its side with a note saying "turn me".
    # Turn it for real, because the note is about to be dropped with the rest.
    image = ImageOps.exif_transpose(image)
    profile = image.info.get("icc_profile")  # how its colours are meant: kept, it is not personal
    image = image.convert("RGBA" if _transparent(image) else "RGB")
    image.thumbnail((SIDE, SIDE), Image.Resampling.LANCZOS)

    def encoded(**options):
        buffer = io.BytesIO()
        # Nothing is passed along but the pixels and the colour profile: no EXIF, no
        # GPS position, no comments.
        image.save(buffer, "WEBP", icc_profile=profile, method=4, **options)
        return buffer.getvalue()

    photo = encoded(quality=QUALITY)
    exact = encoded(lossless=True, quality=70)
    # A screenshot or a drawing is smaller kept exactly, and sharper; a photo is not.
    return (exact if len(exact) <= len(photo) else photo), ".webp", image.width, image.height


def _transparent(image):
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def keep(user, course, data):
    """Prepare an uploaded file and keep it aside for this course. Returns the Upload."""
    made, ending, width, height = prepare(data)
    name = hashlib.sha256(made).hexdigest()[:16] + ending
    # What nothing came to refer to in a week is let go of, here, when the next one comes.
    Upload.objects.filter(added_at__lt=timezone.now() - KEPT).delete()
    upload, _new = Upload.objects.update_or_create(
        slug=course.slug,
        name=name,
        defaults={"data": made, "user": user, "width": width, "height": height},
    )
    return upload


def staged(course):
    """The names of the images kept aside for a course."""
    return set(Upload.objects.filter(slug=course.slug).values_list("name", flat=True))


def attach(course, files, name):
    """Add to `files` (the course, as it is about to be saved) the uploads that the file
    `name` refers to and the course does not have yet, beside that file."""
    data = files.get(name)
    if data is None or not name.lower().endswith(tuple(handin.TEXT)):
        return
    folder = posixpath.dirname(name)
    wanted = {}
    for reference in REFERENCE.findall(data.decode("utf-8", errors="replace")):
        target = posixpath.join(folder, reference) if folder else reference
        if target not in files:
            wanted[posixpath.basename(reference)] = target
    if not wanted:
        return
    for upload in Upload.objects.filter(slug=course.slug, name__in=list(wanted)):
        files[wanted[upload.name]] = bytes(upload.data)
