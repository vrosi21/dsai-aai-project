from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from desk.switches import switches

from ..mine import chosen
from ..models import Document, QuestionSet
from ..welcome import welcome
from .common import load_course


def course(request, course):
    course, context = load_course(request, course, "overview")
    who = request.user
    documents = {kind: [] for kind in Document.SECTIONS}
    # defer(): leave the big rendered columns in the database; a list only shows titles.
    # listed_for(): what this person may find in a list (catalog/models/visibility.py).
    listed = (
        course.documents.listed_for(who)
        .exclude(kind=Document.Kind.SHEET)
        .defer("body_html", "search_text")
    )
    for document in listed:
        documents[document.kind].append(document)
    context.update(
        sections=[
            {
                "kind": kind,
                "title": title,
                "documents": documents[kind],
                "list_url": reverse(f"catalog:{Document.LIST_URL_NAMES[kind]}", args=[course.slug]),
            }
            for kind, (_segment, title) in Document.SECTIONS.items()
            if documents[kind]
        ],
        # annotate() adds a computed column: here, one COUNT per deck in the same query.
        decks=course.decks.listed_for(who).annotate(n_cards=Count("cards")).select_related("topic"),
        problem_sets=course.questionsets.listed_for(who)
        .filter(kind=QuestionSet.Kind.PRACTICE)
        .annotate(n_questions=Count("questions")),
        assessments=course.assessments.listed_for(who).annotate(n_papers=Count("papers")),
        topics=course.topics.all(),
        palaces=course.palaces.listed_for(who),
        # Whether this course is on the visitor's dashboard: decides which of the two
        # buttons (Add, Remove) the page shows. None when nobody is signed in.
        on_dashboard=None if chosen(request) is None else course.series in chosen(request),
        # What the page says about itself at the bottom (or instead of content, when
        # there is none): whether someone writes this course, and whether this is the
        # year being taught now. An earlier year is finished and asks for nothing.
        has_educator=switches(request).has_educator(course),
        is_current=context["editions"][0].pk == course.pk,
        welcome=welcome(request),
    )
    return render(request, "catalog/course.html", context)


def topic(request, course, key):
    course, context = load_course(request, course, "overview")
    topic = get_object_or_404(course.topics, key=key)
    who = request.user
    context.update(
        topic=topic,
        # Reached through the topic, these rows do not know their course yet; select_related
        # joins it in, so get_absolute_url() costs no extra query per row.
        documents=topic.documents.listed_for(who)
        .exclude(kind=Document.Kind.SHEET)
        .select_related("course"),
        # A deck belongs to the topic if it is tagged with it or holds a card that is.
        decks=course.decks.listed_for(who)
        .filter(Q(topic=topic) | Q(cards__topic=topic))
        .distinct(),
        n_cards=topic.cards.listed_for(who).count(),
        problem_sets=course.questionsets.listed_for(who)
        .filter(kind=QuestionSet.Kind.PRACTICE)
        .filter(Q(topic=topic) | Q(questions__topic=topic))
        .distinct(),
        n_formulas=course.formulas.filter(section__topic=topic).count(),
        n_terms=topic.terms.count(),
        n_hooks=topic.hooks.listed_for(who).count(),
        palaces=topic.palaces.listed_for(who).select_related("course"),
        assessments=topic.assessments.listed_for(who).select_related("course"),
    )
    return render(request, "catalog/topic.html", context)
