"""Who may see an item of a course: everyone, whoever has its address, or the owner alone.

    public     listed, searched, counted, and open to every signed-in person
    unlisted   open to whoever has its address, but in no list, search, count or review
    private    the owner's alone: to everyone else it does not exist

Lectures and the other documents, decks, problem sets, exams (with their papers) and
memory palaces each have a visibility; a card, a question or a stop of a palace has the
one of what it belongs to. It is set by every sync (catalog.visibility.apply): an item
written in the course's `private/` folder is always private, and any other is public
unless the owner switched it (desk.models.Visibility).

Views ask through the querysets below, never the field itself:

    course.decks.listed_for(request.user)   lists, counts, search, the review pool
    course.decks.visible_to(request.user)   a page of its own, opened by its address

The owner (a staff member) sees everything, in lists too, with a mark on what is not
public.
"""

from django.db import models


class Visibility(models.TextChoices):
    PUBLIC = "public", "Public"
    UNLISTED = "unlisted", "Unlisted"
    PRIVATE = "private", "Private"


def is_owner(user):
    return bool(getattr(user, "is_staff", False))


class ShownQuerySet(models.QuerySet):
    """Filters by the visibility of the item itself, or of what it belongs to."""

    # The path from this model to the field, e.g. "deck__" for a card.
    through = ""

    def visible_to(self, user):
        if is_owner(user):
            return self
        return self.exclude(**{f"{self.through}visibility": Visibility.PRIVATE})

    def listed_for(self, user):
        if is_owner(user):
            return self
        return self.filter(**{f"{self.through}visibility": Visibility.PUBLIC})


class CardQuerySet(ShownQuerySet):
    through = "deck__"


class QuestionQuerySet(ShownQuerySet):
    through = "question_set__"


class LocusQuerySet(ShownQuerySet):
    through = "palace__"


class HookQuerySet(models.QuerySet):
    """A hook written in memory/hooks.yaml belongs to the whole course; one written on a
    flashcard is seen where that card is."""

    def listed_for(self, user):
        if is_owner(user):
            return self
        return self.filter(
            models.Q(card__isnull=True) | models.Q(card__deck__visibility=Visibility.PUBLIC)
        )


class Shown(models.Model):
    """The two fields every item with a visibility has. Abstract: no table of its own."""

    visibility = models.CharField(
        max_length=10, choices=Visibility, default=Visibility.PUBLIC, db_index=True
    )
    # Written in the course's private/ folder: private whatever is switched, and never
    # among the files that travel to the course's educators.
    from_private = models.BooleanField(default=False)

    objects = ShownQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_public(self):
        return self.visibility == Visibility.PUBLIC

    @property
    def shown_ref(self):
        """The item's name in the owner's switches, e.g. `deck:measure`."""
        from ..visibility import ref_of  # catalog/visibility.py imports the models

        return ref_of(self)
