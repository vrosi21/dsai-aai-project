"""What every page inside a course needs: its navigation and a manifest for the browser.

Both depend only on the course's content, so they are built once per sync and cached under
the course's content hash. A new sync changes the hash, which makes a new cache key — there
is nothing to invalidate. They are built twice: once with what everyone sees (the public
items) and once with all of it, for the owner. When the owner switches an item, the
course's `shown_at` changes, and with it the key.
"""

from django.core.cache import cache
from django.urls import reverse

from .models import Document
from .models.visibility import is_owner


def course_data(course, user=None):
    """The navigation, counts and manifest of a course as `user` sees it."""
    owner = is_owner(user)
    shown = course.shown_at.timestamp() if course.shown_at else 0
    key = f"catalog:course:{course.slug}:{course.content_hash}:{shown}:{int(owner)}"
    return cache.get_or_set(key, lambda: _build(course, owner), None)


def has_content(course, user=None):
    """Whether anything has been written up for the course yet, that `user` may see.

    A course with nothing in it has no navigation at all, so that is the test. The
    dashboard and the list of courses in the rail only offer courses that pass it.
    """
    return bool(course_data(course, user)["nav"])


def _build(course, owner):
    slug = course.slug

    def listed(items):
        # The owner's navigation lists everything; everyone else's what is public.
        return items if owner else items.listed_for(None)

    docs = list(listed(course.documents).values("kind", "slug", "number", "title", "order"))
    by_kind = {}
    for doc in docs:
        by_kind.setdefault(doc["kind"], []).append(doc)

    cards = list(
        listed(course.cards).values("key", "kind", "high_yield", "deck__slug", "topic__key")
    )
    decks = list(listed(course.decks).values("slug", "title", "topic__key"))
    sets = list(
        listed(course.questionsets).values(
            "id", "slug", "paper_slug", "title", "kind", "assessment__slug"
        )
    )
    questions = {}
    for row in listed(course.questions).values("key", "question_set_id", "topic__key"):
        questions.setdefault(row["question_set_id"], []).append(row)
    exams = list(listed(course.assessments).values("id", "slug", "title", "kind", "date", "plan"))
    exam_topics = {}
    for row in listed(course.assessments).values("slug", "topics__key"):
        if row["topics__key"]:
            exam_topics.setdefault(row["slug"], []).append(row["topics__key"])

    counts = {
        "lectures": len(by_kind.get("lecture", [])),
        "labs": len(by_kind.get("lab", [])),
        "homework": len(by_kind.get("homework", [])),
        "guides": len(by_kind.get("guide", [])),
        "sheet": len(by_kind.get("sheet", [])),
        # Everything with a rating at its foot: what the "Reading" share of progress counts.
        "reading": sum(len(by_kind.get(kind, [])) for kind in Document.SECTIONS),
        "decks": len(decks),
        "cards": len(cards),
        "problem_sets": sum(1 for s in sets if s["kind"] == "practice"),
        "questions": sum(len(v) for v in questions.values()),
        "exams": len(exams),
        "formulas": course.formulas.count(),
        "terms": course.terms.count(),
        "hooks": listed(course.hooks).count(),
        "palaces": listed(course.palaces).count(),
    }

    def item(key, label, icon, url_name, count):
        return {
            "key": key,
            "label": label,
            "icon": icon,
            "url": reverse(f"catalog:{url_name}", args=[slug]),
            "count": count,
        }

    groups = [
        (
            "Learn",
            [
                item("lectures", "Lectures", "lecture", "lectures", counts["lectures"]),
                item("labs", "Labs", "lab", "labs", counts["labs"]),
                item("homework", "Homework", "homework", "homework_list", counts["homework"]),
                item("guides", "Guides", "guide", "guides", counts["guides"]),
            ],
        ),
        (
            "Practice",
            [
                item("cards", "Flashcards", "cards", "cards", counts["cards"]),
                item("problems", "Problems", "problems", "problems", counts["problem_sets"]),
            ],
        ),
        ("Exams", [item("exams", "Exam prep", "exam", "exams", counts["exams"])]),
        (
            "Reference",
            [
                item("formulas", "Formulas", "formulas", "formulas", counts["formulas"]),
                item("glossary", "Glossary", "glossary", "glossary", counts["terms"]),
                item("cheatsheet", "Cheat sheet", "sheet", "cheatsheet", counts["sheet"]),
            ],
        ),
        (
            "Memory",
            [
                item(
                    "memory",
                    "Hooks & palaces",
                    "memory",
                    "memory",
                    counts["hooks"] + counts["palaces"],
                )
            ],
        ),
    ]
    # A section the course has no content for is simply not offered.
    nav = [{"title": title, "items": [i for i in items if i["count"]]} for title, items in groups]
    nav = [group for group in nav if group["items"]]

    def set_url(row):
        if row["assessment__slug"]:
            return reverse("catalog:paper", args=[slug, row["assessment__slug"], row["paper_slug"]])
        return reverse("catalog:problem_set", args=[slug, row["slug"]])

    manifest = {
        "slug": slug,
        "title": course.title,
        "code": course.code,
        "hue": course.hue,
        "status": course.status,
        "url": course.get_absolute_url(),
        "topics": list(course.topics.values("key", "title")),
        "cards": [
            {
                "id": c["key"],
                "deck": c["deck__slug"],
                "topic": c["topic__key"],
                "hy": c["high_yield"],
                "kind": c["kind"],
            }
            for c in cards
        ],
        "decks": [
            {
                "slug": d["slug"],
                "title": d["title"],
                "topic": d["topic__key"],
                "url": reverse("catalog:deck", args=[slug, d["slug"]]),
            }
            for d in decks
        ],
        "docs": [
            {
                "key": f"doc:{d['kind']}:{d['slug']}",
                "kind": d["kind"],
                "number": d["number"],
                "title": d["title"],
                "url": reverse(f"catalog:{d['kind']}", args=[slug, d["slug"]]),
            }
            for d in sorted(docs, key=lambda d: (d["kind"], d["order"]))
            if d["kind"] in Document.SECTIONS
        ],
        "sets": [
            {
                "slug": s["slug"],
                "title": s["title"],
                "kind": s["kind"],
                "exam": s["assessment__slug"],
                "url": set_url(s),
                "questions": [q["key"] for q in questions.get(s["id"], [])],
            }
            for s in sets
        ],
        "exams": [
            {
                "slug": e["slug"],
                "title": e["title"],
                "kind": e["kind"],
                "date": e["date"].isoformat() if e["date"] else None,
                "phases": len((e["plan"] or {}).get("phases", [])),
                "topics": exam_topics.get(e["slug"], []),
                "url": reverse("catalog:assessment", args=[slug, e["slug"]]),
            }
            for e in exams
        ],
        "urls": {
            "cards": reverse("catalog:cards", args=[slug]),
            "review": reverse("catalog:review", args=[slug]),
        },
    }
    return {"nav": nav, "counts": counts, "manifest": manifest}
