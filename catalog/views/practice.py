from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from ..models import QuestionSet
from .common import load_course, scope_topics


def cards(request, course):
    course, context = load_course(request, course, "cards")
    context["decks"] = (
        course.decks.listed_for(request.user)
        .annotate(n_cards=Count("cards"))
        .select_related("topic", "lecture")
    )
    return render(request, "catalog/cards.html", context)


def _player(request, course, title, queryset, **extra):
    # select_related follows the foreign keys in the same query, so payload() can read
    # card.deck and card.topic without a query per card.
    cards = [card.payload() for card in queryset.select_related("deck", "topic")]
    return {
        "course": course.slug,
        "title": title,
        "cards": cards,
        "filter": request.GET.get("filter", "all"),
        "focus": request.GET.get("card", ""),
        **extra,
    }


def deck(request, course, slug):
    course, context = load_course(request, course, "cards")
    deck = get_object_or_404(
        course.decks.visible_to(request.user).select_related("topic", "lecture"), slug=slug
    )
    context.update(
        deck=deck,
        heading=deck.title,
        player=_player(request, course, deck.title, deck.cards.all(), mode="deck"),
    )
    return render(request, "catalog/deck.html", context)


def review(request, course):
    """The player over every card of the course, optionally narrowed to an exam or topic."""
    course, context = load_course(request, course, "cards")
    # Only what is listed: an unlisted deck is studied from its own page, not here.
    queryset = course.cards.listed_for(request.user)
    topics, label = scope_topics(request, course)
    heading = "All cards"
    if topics is not None:
        queryset = queryset.filter(topic__in=topics)
        heading = f"Cards for {label}"
    context.update(
        deck=None,
        heading=heading,
        player=_player(request, course, heading, queryset, mode="review"),
    )
    return render(request, "catalog/deck.html", context)


def problems(request, course):
    course, context = load_course(request, course, "problems")
    context["problem_sets"] = (
        course.questionsets.listed_for(request.user)
        .filter(kind=QuestionSet.Kind.PRACTICE)
        .annotate(n_questions=Count("questions"))
        .select_related("topic")
    )
    return render(request, "catalog/problems.html", context)


def paper_context(course, question_set, mode):
    """Context shared by problem sets and exam papers: the questions for the template to
    print, and the same questions as data for the script that marks them."""
    questions = list(question_set.questions.select_related("topic"))
    return {
        "question_set": question_set,
        "questions": questions,
        # Buttons for marking an open question after sitting a paper: label, share of points.
        "award_steps": [("0", 0), ("¼", 0.25), ("½", 0.5), ("¾", 0.75), ("Full", 1)],
        "paper": {
            "course": course.slug,
            "slug": question_set.slug,
            "kind": question_set.kind,
            "mode": mode,
            "duration": question_set.duration_min,
            "negative": question_set.negative_marking,
            "questions": [question.payload() for question in questions],
        },
    }


def problem_set(request, course, slug):
    course, context = load_course(request, course, "problems")
    question_set = get_object_or_404(
        course.questionsets.visible_to(request.user).select_related("topic"),
        slug=slug,
        kind=QuestionSet.Kind.PRACTICE,
    )
    context.update(paper_context(course, question_set, "study"), assessment=None)
    return render(request, "catalog/question_set.html", context)
