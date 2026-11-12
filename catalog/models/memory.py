from django.db import models
from django.urls import reverse

from .base import KeyedItem, SluggedItem
from .visibility import HookQuerySet, LocusQuerySet, Shown


class Hook(KeyedItem):
    """A mnemonic. Written either in memory/hooks.yaml or directly on a flashcard;
    sync_content gathers both kinds here so the Memory page shows every hook in one place."""

    class Kind(models.TextChoices):
        PHRASE = "phrase", "Phrase"
        ACRONYM = "acronym", "Acronym"
        IMAGE = "image", "Image"
        STORY = "story", "Story"
        RHYME = "rhyme", "Rhyme"
        PEG = "peg", "Peg"

    kind = models.CharField(max_length=10, choices=Kind, default=Kind.PHRASE)
    target = models.CharField(max_length=300)  # what the hook brings back
    hook_html = models.TextField()
    explain_html = models.TextField(blank=True)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="hooks"
    )
    card = models.ForeignKey(
        "catalog.Card", null=True, blank=True, on_delete=models.CASCADE, related_name="hooks"
    )
    refs = models.JSONField(default=list, blank=True)  # [{label, url}]
    search_text = models.TextField(blank=True)

    objects = HookQuerySet.as_manager()  # one written on a card is seen where the card is

    def __str__(self):
        return self.target

    def get_absolute_url(self):
        return f"{reverse('catalog:memory', args=[self.course.slug])}#{self.key}"


class Palace(SluggedItem, Shown):
    """A memory palace: a route through a familiar place, one vivid image per stop."""

    title = models.CharField(max_length=200)
    encodes_html = models.TextField(blank=True)
    intro_html = models.TextField(blank=True)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="palaces"
    )

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("catalog:palace", args=[self.course.slug, self.slug])


class Locus(models.Model):
    palace = models.ForeignKey(Palace, on_delete=models.CASCADE, related_name="loci")
    key = models.SlugField(max_length=64)
    order = models.PositiveIntegerField(default=0)
    place = models.CharField(max_length=200)
    image_html = models.TextField()
    meaning_html = models.TextField()
    refs = models.JSONField(default=list, blank=True)

    objects = LocusQuerySet.as_manager()  # visible like its palace

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = "loci"
        constraints = [
            models.UniqueConstraint(fields=["palace", "key"], name="catalog_locus_palace_key")
        ]

    def __str__(self):
        return self.place

    def payload(self):
        return {
            "id": self.key,
            "place": self.place,
            "image": self.image_html,
            "meaning": self.meaning_html,
            "refs": self.refs,
        }
