"""Write a Plan to the database.

Rows are matched to existing ones by their natural key (a card's `id`, a lecture's file
name), updated in place, and whatever is no longer in the files is deleted. Updating in
place keeps primary keys stable, so anything that later points at a card by foreign key —
per-user progress, once there are accounts — survives every re-import.

Keys starting with `ref_` in a row name another row; they are turned into real foreign keys
here and never set as attributes.
"""

from django.db import transaction
from django.utils import timezone

from catalog import models, visibility


def _upsert(model, scope, key_fields, rows):
    """Returns ({key: object}, [objects no longer present in the files])."""
    existing = {}
    for obj in model.objects.filter(**scope):
        existing[tuple(getattr(obj, name) for name in key_fields)] = obj
    kept = {}
    for row in rows:
        key = tuple(row[name] for name in key_fields)
        obj = existing.pop(key, None) or model(**scope)
        for name, value in row.items():
            if not name.startswith("ref_"):
                setattr(obj, name, value)
        obj.save()
        kept[key[0] if len(key) == 1 else key] = obj
    return kept, list(existing.values())


@transaction.atomic
def write(plan, content_hash):
    """All or nothing: if anything fails, the course is left exactly as it was."""
    course, _ = models.Course.objects.update_or_create(
        slug=plan.course["slug"],
        defaults={name: value for name, value in plan.course.items() if name != "slug"},
    )
    scope = {"course": course}
    stale = []  # deleted last, after every child has been moved to its new parent

    topics, gone = _upsert(models.Topic, scope, ["key"], plan.topics)
    stale.append(gone)

    def with_topic(row, **extra):
        return {**row, "topic": topics.get(row.get("ref_topic")), **extra}

    documents, gone = _upsert(models.Document, scope, ["kind", "slug"], plan.documents)
    stale.append(gone)
    for row in plan.documents:
        document = documents[(row["kind"], row["slug"])]
        document.topics.set([topics[key] for key in row["ref_topics"] if key in topics])

    decks, gone = _upsert(
        models.Deck,
        scope,
        ["slug"],
        [
            with_topic(row, lecture=documents.get(("lecture", row["ref_lecture"])))
            for row in plan.decks
        ],
    )
    stale.append(gone)
    cards, gone = _upsert(
        models.Card,
        scope,
        ["key"],
        [with_topic(row, deck=decks[row["ref_deck"]]) for row in plan.cards],
    )
    stale.append(gone)

    assessments, gone = _upsert(models.Assessment, scope, ["slug"], plan.assessments)
    stale.append(gone)
    for row in plan.assessments:
        assessment = assessments[row["slug"]]
        assessment.topics.set([topics[key] for key in row["ref_topics"] if key in topics])
        assessment.lectures.set([documents[("lecture", slug)] for slug in row["ref_lectures"]])
        assessment.includes.set([assessments[slug] for slug in row["ref_includes"]])

    sets, gone = _upsert(
        models.QuestionSet,
        scope,
        ["slug"],
        [with_topic(row, assessment=assessments.get(row["ref_assessment"])) for row in plan.sets],
    )
    stale.append(gone)
    _, gone = _upsert(
        models.Question,
        scope,
        ["key"],
        [with_topic(row, question_set=sets[row["ref_set"]]) for row in plan.questions],
    )
    stale.append(gone)

    sections, gone = _upsert(
        models.FormulaSection, scope, ["slug"], [with_topic(row) for row in plan.formula_sections]
    )
    stale.append(gone)
    _, gone = _upsert(
        models.Formula,
        scope,
        ["key"],
        [{**row, "section": sections[row["ref_section"]]} for row in plan.formulas],
    )
    stale.append(gone)
    _, gone = _upsert(models.Term, scope, ["key"], [with_topic(row) for row in plan.terms])
    stale.append(gone)

    _, gone = _upsert(
        models.Hook,
        scope,
        ["key"],
        [with_topic(row, card=cards.get(row["ref_card"])) for row in plan.hooks],
    )
    stale.append(gone)
    palaces, gone = _upsert(
        models.Palace, scope, ["slug"], [with_topic(row) for row in plan.palaces]
    )
    stale.append(gone)
    for slug, palace in palaces.items():
        rows = [row for row in plan.loci if row["ref_palace"] == slug]
        _, gone = _upsert(models.Locus, {"palace": palace}, ["key"], rows)
        stale.append(gone)

    for group in reversed(stale):
        for obj in group:
            if obj.pk is not None:
                obj.delete()

    # Public, unlisted or private: from the folder each item came from and the owner's
    # switches. In the same transaction, so nothing private is ever shown for a moment.
    visibility.apply(course)

    course.content_hash = content_hash
    course.synced_at = timezone.now()
    course.save(update_fields=["content_hash", "synced_at"])
    return course
