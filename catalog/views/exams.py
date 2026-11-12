from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from ..models import QuestionSet
from .common import load_course
from .practice import paper_context


def exams(request, course):
    course, context = load_course(request, course, "exams")
    context["assessments"] = course.assessments.listed_for(request.user).prefetch_related(
        "topics", "papers"
    )
    return render(request, "catalog/exams.html", context)


def assessment(request, course, assessment):
    course, context = load_course(request, course, "exams")
    who = request.user
    exam = get_object_or_404(course.assessments.visible_to(who), slug=assessment)
    topics = list(exam.topics.all())
    # Rows reached through the exam do not carry their course; join it in for their URLs.
    # A paper is what its exam is, so whoever sees the exam sees its papers.
    papers = list(exam.papers.select_related("course"))

    # Everything below is derived from the exam's scope: nobody lists these by hand.
    in_scope = course.cards.listed_for(who).filter(topic__in=topics)
    context.update(
        exam=exam,
        topics=topics,
        lectures=exam.lectures.listed_for(who).select_related("course"),
        includes=exam.includes.listed_for(who).select_related("course"),
        past_papers=[p for p in papers if p.kind == QuestionSet.Kind.PAST],
        mock_papers=[p for p in papers if p.kind == QuestionSet.Kind.MOCK],
        n_cards=in_scope.count(),
        n_key_cards=in_scope.filter(high_yield=True).count(),
        problem_sets=course.questionsets.listed_for(who)
        .filter(kind=QuestionSet.Kind.PRACTICE)
        .filter(Q(topic__in=topics) | Q(questions__topic__in=topics))
        .distinct(),
        n_formulas=course.formulas.filter(section__topic__in=topics).count(),
        n_terms=course.terms.filter(topic__in=topics).count(),
        n_hooks=course.hooks.listed_for(who).filter(topic__in=topics).count(),
        exam_data={
            "course": course.slug,
            "slug": exam.slug,
            "date": exam.date.isoformat() if exam.date else None,
            "phases": len(exam.plan.get("phases", [])),
            "topics": [topic.key for topic in topics],
            "papers": [paper.slug for paper in papers],
        },
    )
    return render(request, "catalog/assessment.html", context)


def paper(request, course, assessment, paper):
    course, context = load_course(request, course, "exams")
    question_set = get_object_or_404(
        course.questionsets.visible_to(request.user).select_related("assessment"),
        assessment__slug=assessment,
        paper_slug=paper,
    )
    # A mock opens ready to sit; a past paper opens worked, unless the link says otherwise.
    default = "sit" if question_set.kind == QuestionSet.Kind.MOCK else "study"
    mode = request.GET.get("mode", default)
    if mode not in ("sit", "study"):
        mode = default
    context.update(paper_context(course, question_set, mode), assessment=question_set.assessment)
    return render(request, "catalog/question_set.html", context)
