"""What each item of a course is: public, unlisted or private (catalog/models/visibility.py).

    apply(course)          after a sync: every item from its folder and the owner's switches
    switch(course, ref, s) the owner switching one item, on its page
    ref_of(item)           an item's name in the switches, as content links to it

An item written in the course's private/ folder is private, and cannot be switched: that
folder may hold graded work, which must never become visible by a wrong click. Anything
else is public unless the owner switched it (desk.models.Visibility). A paper of an exam is
what its exam is.
"""

from django.db import transaction
from django.utils import timezone

from .models import Assessment, Deck, Document, Palace, QuestionSet, Visibility

SHEET = "cheatsheet"


def ref_of(item):
    if isinstance(item, Document):
        return SHEET if item.kind == Document.Kind.SHEET else f"{item.kind}:{item.slug}"
    if isinstance(item, Deck):
        return f"deck:{item.slug}"
    if isinstance(item, QuestionSet) and not item.assessment_id:
        return f"problems:{item.slug}"
    if isinstance(item, Assessment):
        return f"exam:{item.slug}"
    if isinstance(item, Palace):
        return f"palace:{item.slug}"
    raise TypeError(f"{type(item).__name__} has no visibility of its own")


def find(course, ref):
    """The item of `course` named `ref` (as ref_of names it), or None."""
    if ref == SHEET:
        return course.documents.filter(kind=Document.Kind.SHEET).first()
    kind, _colon, slug = ref.partition(":")
    if kind in Document.SECTIONS:
        return course.documents.filter(kind=kind, slug=slug).first()
    found = {
        "deck": course.decks,
        "problems": course.questionsets.filter(assessment__isnull=True),
        "exam": course.assessments,
        "palace": course.palaces,
    }.get(kind)
    return found.filter(slug=slug).first() if found is not None and slug else None


def items(course):
    """Every item of the course that has a visibility of its own."""
    yield from course.documents.all()
    yield from course.decks.all()
    yield from course.questionsets.filter(assessment__isnull=True)
    yield from course.assessments.all()
    yield from course.palaces.all()


def _switches(course):
    from desk.models import Visibility as Switch  # desk depends on catalog, not the reverse

    return dict(Switch.objects.filter(course=course.slug).values_list("ref", "state"))


def _state(item, switches):
    if item.from_private:
        return Visibility.PRIVATE
    return switches.get(ref_of(item), Visibility.PUBLIC)


@transaction.atomic
def apply(course):
    switches = _switches(course)
    for item in items(course):
        state = _state(item, switches)
        if item.visibility != state:
            item.visibility = state
            item.save(update_fields=["visibility"])
        if isinstance(item, Assessment):
            item.papers.exclude(visibility=state).update(visibility=state)


class Locked(ValueError):
    """The item is in the private/ folder: private, and not to be switched."""


@transaction.atomic
def switch(course, item, state):
    from desk.models import Visibility as Switch

    if item.from_private:
        raise Locked(ref_of(item))
    if state == Visibility.PUBLIC:
        Switch.objects.filter(course=course.slug, ref=ref_of(item)).delete()
    else:
        Switch.objects.update_or_create(
            course=course.slug, ref=ref_of(item), defaults={"state": state}
        )
    apply(course)
    course.shown_at = timezone.now()
    course.save(update_fields=["shown_at"])
