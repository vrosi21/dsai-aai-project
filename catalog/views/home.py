from itertools import groupby

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from ..models import Card, Course, Document, Formula, Hook, Question, Term
from ..nav import course_data


def home(request):
    courses = list(Course.objects.all())
    for course in courses:
        data = course_data(course)
        course.counts = data["counts"]
    active = [c for c in courses if c.status == Course.Status.ACTIVE]
    archived = [c for c in courses if c.status == Course.Status.ARCHIVED]
    active.sort(key=lambda c: (c.year or "",), reverse=True)
    years = [
        {"year": year or "Courses", "courses": sorted(group, key=lambda c: (c.order, c.title))}
        for year, group in groupby(active, key=lambda c: c.year)
    ]
    return render(
        request,
        "catalog/home.html",
        {
            "years": years,
            "archived": archived,
            "manifests": [course_data(c)["manifest"] for c in courses],
            "active": "home",
        },
    )


def _snippet(text, query, radius=70):
    position = text.lower().find(query.lower())
    if position < 0:
        return text[: radius * 2].strip()
    start, end = max(0, position - radius), min(len(text), position + len(query) + radius)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def search(request):
    query = request.GET.get("q", "").strip()
    course_slug = request.GET.get("course", "")
    results = []
    if len(query) >= 2:
        scope = {"course__slug": course_slug} if course_slug else {}

        def found(model, *fields, related=()):
            condition = Q()
            for name in fields:
                # Q objects combine with | (OR) and & (AND) to build one WHERE clause.
                condition |= Q(**{f"{name}__icontains": query})
            return model.objects.filter(condition, **scope).select_related("course", *related)[:40]

        for doc in found(Document, "title", "search_text"):
            results.append((doc.get_kind_display(), doc.label, doc, doc.search_text))
        for card in found(Card, "search_text", related=("deck",)):
            results.append(("Flashcard", card.deck.title, card, card.search_text))
        for question in found(
            Question, "title", "search_text", related=("question_set__assessment",)
        ):
            label = question.title or f"Question {question.number}"
            results.append(
                (question.question_set.get_kind_display(), label, question, question.search_text)
            )
        for formula in found(Formula, "name", "search_text"):
            results.append(("Formula", formula.name, formula, formula.search_text))
        for term in found(Term, "term", "search_text"):
            results.append(("Glossary", term.term, term, term.search_text))
        for hook in found(Hook, "target", "search_text"):
            results.append(("Memory hook", hook.target, hook, hook.search_text))

    rows = [
        {
            "kind": kind,
            "title": title,
            "url": obj.get_absolute_url(),
            "course": obj.course,
            "snippet": _snippet(text, query),
        }
        for kind, title, obj, text in results
    ]
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "catalog/search.html",
        {
            "query": query,
            "course_slug": course_slug,
            "page": page,
            "total": len(rows),
            "courses": Course.objects.all(),
            "active": "search",
        },
    )


def settings_page(request):
    courses = list(Course.objects.all())
    for course in courses:
        course.counts = course_data(course)["counts"]
    return render(
        request,
        "catalog/settings.html",
        {
            "courses": courses,
            "manifests": [course_data(c)["manifest"] for c in courses],
            "active": "settings",
        },
    )
