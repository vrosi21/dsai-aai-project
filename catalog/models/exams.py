from django.db import models
from django.urls import reverse

from .base import KeyedItem, SluggedItem
from .visibility import QuestionQuerySet, Shown


class Assessment(SluggedItem, Shown):
    """One exam you prepare for. Its scope (topics) decides which cards, problems, formulas
    and hooks show up on its page; only the plan, format and predictions are written by hand."""

    class Kind(models.TextChoices):
        MIDTERM = "midterm", "Midterm"
        FINAL = "final", "Final"
        COMPREHENSIVE = "comprehensive", "Comprehensive exam"
        QUIZ = "quiz", "Quiz"
        ORAL = "oral", "Oral exam"
        PROJECT = "project", "Project defence"
        OTHER = "other", "Assessment"

    kind = models.CharField(max_length=15, choices=Kind, default=Kind.OTHER)
    title = models.CharField(max_length=200)
    date = models.DateField(null=True, blank=True)

    topics = models.ManyToManyField("catalog.Topic", blank=True, related_name="assessments")
    lectures = models.ManyToManyField("catalog.Document", blank=True, related_name="assessments")
    # A comprehensive exam lists the partial exams it spans; symmetrical=False because
    # "comprehensive includes midterm" does not mean "midterm includes comprehensive".
    includes = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="included_in"
    )

    format_html = models.TextField(blank=True)
    weights = models.JSONField(default=list, blank=True)  # [{topic, title, weight, note}]
    plan = models.JSONField(default=dict, blank=True)  # {intro, phases[{…tasks[…]}], skip}
    predictions = models.JSONField(default=list, blank=True)  # [{q, why, likelihood, refs}]

    class Meta(SluggedItem.Meta):
        ordering = ["date", "order", "id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("catalog:assessment", args=[self.course.slug, self.slug])


class QuestionSet(SluggedItem, Shown):
    """An ordered list of questions: a practice set, a past paper or a mock exam."""

    class Kind(models.TextChoices):
        PRACTICE = "practice", "Practice set"
        PAST = "past", "Past paper"
        MOCK = "mock", "Mock exam"

    kind = models.CharField(max_length=10, choices=Kind, default=Kind.PRACTICE)
    title = models.CharField(max_length=200)
    assessment = models.ForeignKey(
        Assessment, null=True, blank=True, on_delete=models.CASCADE, related_name="papers"
    )
    # File name inside exams/<assessment>/; the slug itself is "<assessment>-<paper>".
    paper_slug = models.SlugField(max_length=80, blank=True)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="sets"
    )
    date = models.DateField(null=True, blank=True)
    duration_min = models.PositiveSmallIntegerField(null=True, blank=True)
    negative_marking = models.FloatField(default=0)
    total_points = models.FloatField(default=0)
    instructions_html = models.TextField(blank=True)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        if self.assessment_id:
            return reverse(
                "catalog:paper", args=[self.course.slug, self.assessment.slug, self.paper_slug]
            )
        return reverse("catalog:problem_set", args=[self.course.slug, self.slug])


class Question(KeyedItem):
    class Kind(models.TextChoices):
        OPEN = "open", "Open"
        MCQ = "mcq", "Single choice"
        MULTI = "multi", "Multiple choice"
        TF = "tf", "True / false"

    question_set = models.ForeignKey(
        QuestionSet, on_delete=models.CASCADE, related_name="questions"
    )
    kind = models.CharField(max_length=10, choices=Kind, default=Kind.OPEN)
    number = models.CharField(max_length=10)

    objects = QuestionQuerySet.as_manager()  # visible like its set
    title = models.CharField(max_length=200, blank=True)
    points = models.FloatField(null=True, blank=True)
    topic = models.ForeignKey(
        "catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL, related_name="questions"
    )

    prompt_html = models.TextField()
    parts = models.JSONField(default=list, blank=True)  # [{label, q, solution, points}]
    choices = models.JSONField(default=list, blank=True)  # [{text, correct, why}]
    correct = models.BooleanField(null=True, blank=True)  # true/false questions only
    solution_html = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)  # [{do, why}]
    answer_html = models.TextField(blank=True)
    source_html = models.TextField(blank=True)
    search_text = models.TextField(blank=True)

    def __str__(self):
        return self.key

    def get_absolute_url(self):
        return f"{self.question_set.get_absolute_url()}#{self.key}"

    def payload(self):
        return {
            "id": self.key,
            "number": self.number,
            "kind": self.kind,
            "title": self.title,
            "points": self.points,
            "topic": self.topic.key if self.topic else None,
            "prompt": self.prompt_html,
            "parts": self.parts,
            "choices": self.choices,
            "correct": self.correct,
            "solution": self.solution_html,
            "steps": self.steps,
            "answer": self.answer_html,
            "source": self.source_html,
        }
