from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from .common import load_course, scope_topics


def memory(request, course):
    course, context = load_course(request, course, "memory")
    who = request.user
    hooks = course.hooks.listed_for(who).select_related("topic", "card__deck")
    palaces = course.palaces.listed_for(who).annotate(n_loci=Count("loci")).select_related("topic")
    topics, label = scope_topics(request, course)
    if topics is not None:
        hooks = hooks.filter(topic__in=topics)
        palaces = palaces.filter(topic__in=topics)
    context.update(hooks=hooks, palaces=palaces, scope_label=label)
    return render(request, "catalog/memory.html", context)


def palace(request, course, slug):
    course, context = load_course(request, course, "memory")
    palace = get_object_or_404(
        course.palaces.visible_to(request.user).select_related("topic"), slug=slug
    )
    loci = list(palace.loci.all())
    context.update(
        palace=palace,
        loci=loci,
        walk={
            "course": course.slug,
            "palace": palace.slug,
            "title": palace.title,
            "loci": [locus.payload() for locus in loci],
        },
    )
    return render(request, "catalog/palace.html", context)
