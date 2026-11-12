from django.db import models
from django.urls import reverse

from .base import KeyedItem, SluggedItem
from .visibility import CardQuerySet, Shown


class Deck(SluggedItem, Shown):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="decks"
    )
    lecture = models.ForeignKey(
        "catalog.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="decks"
    )

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("catalog:deck", args=[self.course.slug, self.slug])


class Card(KeyedItem):
    class Kind(models.TextChoices):
        BASIC = "basic", "Question and answer"
        STEPS = "steps", "Step-by-step derivation"
        CLOZE = "cloze", "Fill in the blanks"

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="cards")
    kind = models.CharField(max_length=10, choices=Kind, default=Kind.BASIC)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="cards"
    )
    high_yield = models.BooleanField(default=False)

    objects = CardQuerySet.as_manager()  # visible like its deck

    # For a cloze card, front_html is the whole sentence with each blank in <span class="cloze">.
    front_html = models.TextField()
    back_html = models.TextField(blank=True)
    note_html = models.TextField(blank=True)
    hook_html = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)  # [{"do": html, "why": html}]
    search_text = models.TextField(blank=True)

    def __str__(self):
        return self.key

    def get_absolute_url(self):
        return f"{reverse('catalog:deck', args=[self.course.slug, self.deck.slug])}?card={self.key}"

    def payload(self):
        """What the flashcard player needs, as plain data for json_script."""
        return {
            "id": self.key,
            "deck": self.deck.slug,
            "deckTitle": self.deck.title,
            "topic": self.topic.key if self.topic else None,
            "kind": self.kind,
            "hy": self.high_yield,
            "front": self.front_html,
            "back": self.back_html,
            "note": self.note_html,
            "hook": self.hook_html,
            "steps": self.steps,
        }
