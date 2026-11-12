from django.shortcuts import get_object_or_404, render

from ..models import Document
from .common import load_course, scope_topics


def formulas(request, course):
    course, context = load_course(request, course, "formulas")
    sections = course.formulasections.prefetch_related("formulas").select_related("topic")
    topics, label = scope_topics(request, course)
    if topics is not None:
        sections = sections.filter(topic__in=topics)
    context.update(sections=sections, scope_label=label)
    return render(request, "catalog/formulas.html", context)


def glossary(request, course):
    course, context = load_course(request, course, "glossary")
    terms = course.terms.select_related("topic")
    topics, label = scope_topics(request, course)
    if topics is not None:
        terms = terms.filter(topic__in=topics)
    context.update(terms=terms, scope_label=label)
    return render(request, "catalog/glossary.html", context)


def cheatsheet(request, course):
    course, context = load_course(request, course, "cheatsheet")
    context["doc"] = get_object_or_404(
        course.documents.visible_to(request.user), kind=Document.Kind.SHEET
    )
    return render(request, "catalog/cheatsheet.html", context)
