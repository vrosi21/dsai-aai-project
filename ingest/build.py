"""Turn a validated bundle into the rows that will be written to the database.

Two passes, because a link in one file can point at something defined in another:

1. `index()` walks every file, records what exists (ids, file names, titles) and checks that
   every cross-reference resolves: topics, lecture links, duplicate ids, exam scopes.
2. `plan()` renders the Markdown — now able to resolve links — and produces plain dicts.

Nothing here touches the database; store.py does that.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from django.utils.text import slugify

from .markdown import Rendered, strip_markdown
from .refs import Index, RefError, _missing
from .yamlio import dotted


@dataclass
class Plan:
    course: dict
    topics: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    decks: list = field(default_factory=list)
    cards: list = field(default_factory=list)
    assessments: list = field(default_factory=list)
    sets: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    formula_sections: list = field(default_factory=list)
    formulas: list = field(default_factory=list)
    terms: list = field(default_factory=list)
    hooks: list = field(default_factory=list)
    palaces: list = field(default_factory=list)
    loci: list = field(default_factory=list)

    def counts(self):
        def docs(kind):
            return sum(1 for d in self.documents if d["kind"] == kind)

        def sets(*kinds):
            return sum(1 for s in self.sets if s["kind"] in kinds)

        return {
            "lectures": docs("lecture"),
            "labs": docs("lab"),
            "homework": docs("homework"),
            "guides": docs("guide"),
            "decks": len(self.decks),
            "cards": len(self.cards),
            "problem sets": sets("practice"),
            "exams": len(self.assessments),
            "papers": sets("past", "mock"),
            "questions": len(self.questions),
            "formulas": len(self.formulas),
            "terms": len(self.terms),
            "hooks": len(self.hooks),
            "palaces": len(self.palaces),
        }


def _shorten(text, limit=90):
    text = " ".join(text.replace("{{", "").replace("}}", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _number(doc):
    """A document's number: the `number` field, else leading digits of the file name."""
    if doc.spec.number is not None:
        return doc.spec.number
    match = re.match(r"^(\d+)", doc.slug)
    return int(match.group(1)) if match else None


class Builder:
    def __init__(self, bundle, report):
        self.bundle = bundle
        self.files = bundle.files
        self.report = report
        self.course = bundle.course.spec
        self.topic_ids = [topic.id for topic in self.course.topics]
        self.ids: dict[str, str] = {}  # id -> where it was first used
        self.scopes: dict[str, tuple[list, list]] = {}  # exam -> (topic ids, lecture slugs)
        self.card_labels: dict[str, str] = {}
        self.index_: Index | None = None
        self.renderer = None
        self.resolver = None

    # ------------------------------------------------------------------ small helpers

    def _line(self, parsed, path):
        return parsed.loaded.line(path)

    def _error(self, parsed, path, message, hint=""):
        self.report.error(
            parsed.rel, message, line=self._line(parsed, path), where=dotted(path), hint=hint
        )

    def _claim(self, item_id, parsed, path):
        """Ids are permanent and course-wide: study progress is stored under them."""
        here = f"{parsed.rel}:{self._line(parsed, path)}"
        if item_id in self.ids:
            self._error(
                parsed,
                path,
                f"id `{item_id}` is already used at {self.ids[item_id]}",
                "every id must be unique in the course; give the newer item a new id",
            )
        else:
            self.ids[item_id] = here

    def _topic(self, parsed, path, value):
        if value and value not in self.topic_ids:
            error = _missing("topic", value, self.topic_ids)
            hint = error.hint or "topics are declared in course.yaml"
            self._error(parsed, path, str(error), hint)

    def _topics(self, parsed, path, values):
        for position, value in enumerate(values):
            self._topic(parsed, path + (position,), value)

    def md(self, parsed, path, value, *, cloze=False):
        if not value:
            return Rendered()
        return self.renderer.render(
            value,
            file=parsed.rel,
            line=parsed.loaded.text_line(path),
            where=dotted(path),
            base_dir=parsed.path.parent,
            cloze=cloze,
        )

    def links(self, parsed, path, refs):
        out = []
        for position, ref in enumerate(refs):
            try:
                url, label = self.resolver.resolve(ref)
            except RefError as exc:
                self._error(parsed, path + (position,), str(exc), exc.hint)
                continue
            out.append({"url": url, "label": label, "kind": re.split(r"[:#?]", ref, maxsplit=1)[0]})
        return out

    def steps(self, parsed, path, steps):
        return [
            {
                "do": self.md(parsed, path + (i, "do"), step.do).html,
                "why": self.md(parsed, path + (i, "why"), step.why).html,
            }
            for i, step in enumerate(steps or [])
        ]

    # ------------------------------------------------------------------ pass 1: index

    def index(self):
        bundle = self.bundle
        index = self.index_ = Index(course=self.files.slug)

        seen = set()
        for i, topic in enumerate(self.course.topics):
            if topic.id in seen:
                self._error(bundle.course, ("topics", i, "id"), f"topic `{topic.id}` appears twice")
            seen.add(topic.id)
            index.topics[topic.id] = topic.title

        for kind, docs in bundle.documents.items():
            for slug, doc in docs.items():
                index.documents[(kind, slug)] = doc.spec.title
                self._topics(doc, ("topics",), doc.spec.topics)
                number = _number(doc)
                if kind == "lecture" and number is not None:
                    if number in index.lecture_numbers:
                        self.report.warn(
                            doc.rel,
                            f"lecture number {number} is also used by "
                            f"{index.lecture_numbers[number]}.md",
                            hint="exam scopes refer to lectures by number, so keep them unique",
                        )
                    else:
                        index.lecture_numbers[number] = slug
        if bundle.cheatsheet:
            index.has_cheatsheet = True

        for slug, deck in bundle.decks.items():
            index.decks[slug] = deck.spec.title
            if slug == "review":
                self.report.error(
                    deck.rel,
                    "a deck cannot be called `review`: that address is the all-cards page",
                    hint="rename the file",
                )
            self._topic(deck, ("topic",), deck.spec.topic)
            lecture = deck.spec.lecture
            if lecture and lecture not in bundle.documents.get("lecture", {}):
                error = _missing("lecture", lecture, bundle.documents.get("lecture", {}))
                self._error(deck, ("lecture",), str(error), error.hint)
            for i, card in enumerate(deck.spec.cards):
                self._claim(card.id, deck, ("cards", i, "id"))
                self._topic(deck, ("cards", i, "topic"), card.topic)
                label = _shorten(strip_markdown(card.q or card.cloze))
                self.card_labels[card.id] = label
                index.cards[card.id] = (slug, label)
                if card.hook:
                    self._claim(f"{card.id}-hook", deck, ("cards", i, "hook"))
                    index.hooks[f"{card.id}-hook"] = label

        for slug, problem_set in bundle.problem_sets.items():
            index.sets[slug] = problem_set.spec.title
            self._topic(problem_set, ("topic",), problem_set.spec.topic)
            self._index_questions(problem_set, slug)

        for slug, assessment in bundle.assessments.items():
            index.assessments[slug] = assessment.spec.title
        for (exam, paper), parsed in bundle.papers.items():
            if exam not in bundle.assessments:
                continue  # discover already reported the missing assessment.yaml
            set_slug = f"{exam}-{paper}"
            if set_slug in index.sets:
                self.report.error(
                    parsed.rel,
                    f"this paper's address `{set_slug}` clashes with problems/{set_slug}.yaml",
                    hint="rename one of the two files",
                )
            index.sets[set_slug] = parsed.spec.title
            index.papers[(exam, paper)] = parsed.spec.title
            self._index_questions(parsed, set_slug)
        for slug in bundle.assessments:
            self._scope(slug, ())
        for assessment in bundle.assessments.values():
            for i, weight in enumerate(assessment.spec.weights):
                self._topic(assessment, ("weights", i, "topic"), weight.topic)

        if bundle.formulas:
            for s, section in enumerate(bundle.formulas.spec.sections):
                self._topic(bundle.formulas, ("sections", s, "topic"), section.topic)
                for r, row in enumerate(section.rows):
                    self._claim(row.id, bundle.formulas, ("sections", s, "rows", r, "id"))
                    index.formulas[row.id] = row.name
        if bundle.glossary:
            terms = bundle.glossary.spec.terms
            for i, term in enumerate(terms):
                self._claim(term.id, bundle.glossary, ("terms", i, "id"))
                self._topic(bundle.glossary, ("terms", i, "topic"), term.topic)
                index.terms[term.id] = term.term
            for i, term in enumerate(terms):
                for j, other in enumerate(term.see):
                    if other not in index.terms:
                        error = _missing("glossary term", other, index.terms)
                        self._error(bundle.glossary, ("terms", i, "see", j), str(error), error.hint)
        if bundle.hooks:
            for i, hook in enumerate(bundle.hooks.spec.hooks):
                self._claim(hook.id, bundle.hooks, ("hooks", i, "id"))
                self._topic(bundle.hooks, ("hooks", i, "topic"), hook.topic)
                index.hooks[hook.id] = hook.target
        for slug, palace in bundle.palaces.items():
            index.palaces[slug] = palace.spec.title
            self._topic(palace, ("topic",), palace.spec.topic)
            local = set()
            for i, locus in enumerate(palace.spec.loci):
                if locus.id in local:
                    self._error(
                        palace, ("loci", i, "id"), f"stop `{locus.id}` appears twice in this palace"
                    )
                local.add(locus.id)
        return index

    def _index_questions(self, parsed, set_slug):
        for i, question in enumerate(parsed.spec.questions):
            self._claim(question.id, parsed, ("questions", i, "id"))
            self._topic(parsed, ("questions", i, "topic"), question.topic)
            label = question.title or f"Question {i + 1}"
            self.index_.questions[question.id] = (set_slug, label)

    def _scope(self, slug, stack):
        """Which topics and lectures an exam covers, following `includes`."""
        if slug in self.scopes:
            return self.scopes[slug]
        bundle = self.bundle
        parsed = bundle.assessments[slug]
        spec = parsed.spec
        lectures_by_slug = bundle.documents.get("lecture", {})
        topics, lectures = set(spec.scope.topics), set()
        self._topics(parsed, ("scope", "topics"), spec.scope.topics)

        for i, number in enumerate(spec.scope.lectures):
            lecture = self.index_.lecture_numbers.get(number)
            if lecture is None:
                known = sorted(self.index_.lecture_numbers)
                self._error(
                    parsed,
                    ("scope", "lectures", i),
                    f"there is no lecture number {number}",
                    f"lectures that exist: {known}" if known else "this course has no lectures yet",
                )
                continue
            lectures.add(lecture)
            topics.update(lectures_by_slug[lecture].spec.topics)

        for i, other in enumerate(spec.includes):
            if other == slug or other in stack:
                self._error(parsed, ("includes", i), f"`{other}` includes itself, directly or not")
            elif other not in bundle.assessments:
                error = _missing("exam", other, bundle.assessments)
                self._error(parsed, ("includes", i), str(error), error.hint)
            else:
                more_topics, more_lectures = self._scope(other, stack + (slug,))
                topics.update(more_topics)
                lectures.update(more_lectures)

        if not (spec.scope.topics or spec.scope.lectures or spec.includes):
            topics, lectures = set(self.topic_ids), set(lectures_by_slug)  # the whole course

        ordered_topics = [t for t in self.topic_ids if t in topics]
        self.scopes[slug] = (ordered_topics, sorted(lectures))
        return self.scopes[slug]

    # ------------------------------------------------------------------ pass 2: rows

    def plan(self, renderer, resolver):
        self.renderer, self.resolver = renderer, resolver
        spec = self.course
        plan = Plan(
            course={
                "slug": self.files.slug,
                "title": spec.title,
                "code": spec.code,
                "year": spec.year,
                # A course that names no series is the only year of its own.
                "series": spec.series or self.files.slug,
                "semester": spec.semester,
                "status": spec.status,
                "enabled": spec.enabled,
                "has_educator": spec.has_educator,
                "hue": spec.hue,
                "order": spec.order,
                "description": spec.description.strip(),
            }
        )
        plan.topics = [
            {"key": t.id, "title": t.title, "short": t.short or "", "order": i}
            for i, t in enumerate(spec.topics)
        ]
        self._documents(plan)
        self._decks(plan)
        self._question_sets(plan)
        self._assessments(plan)
        self._reference(plan)
        self._memory(plan)
        # Whether there is anything for students: the owner's private material is not.
        shared = [
            row
            for rows in (plan.documents, plan.decks, plan.sets, plan.assessments, plan.palaces)
            for row in rows
            if not row.get("from_private")
        ]
        plan.course["has_content"] = bool(shared or plan.formulas or plan.terms or plan.hooks)
        return plan

    def _private(self, parsed):
        """Whether the file is the owner's private material (ingest/private.py)."""
        return parsed.path in getattr(self.files, "private", ())

    def _sources(self, doc):
        out = []
        source_dir = self.files.root / "source"
        for i, raw in enumerate(doc.spec.sources):
            path = PurePosixPath(raw.replace("\\", "/").strip())
            if not path.parts or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
                self._error(
                    doc,
                    ("sources", i),
                    f"`{raw}` is not a path inside source/",
                    "write it relative to the course's source/ folder, e.g. lectures/L05.pdf",
                )
                continue
            if source_dir.is_dir() and not (source_dir / path).is_file():
                self.report.warn(
                    doc.rel,
                    f"listed source `{path}` was not found under source/",
                    line=self._line(doc, ("sources", i)),
                )
            out.append(path.as_posix())
        return out

    def _document_row(self, doc, kind, order):
        spec = doc.spec
        body = self.renderer.render(
            doc.body, file=doc.rel, line=doc.body_line, base_dir=doc.path.parent, document=True
        )
        if not body.html.strip():
            self.report.warn(doc.rel, "this document has front matter but no text yet")
        takeaways = [
            self.renderer.inline(
                text,
                file=doc.rel,
                line=doc.loaded.text_line(("takeaways", i)),
                where=f"takeaways[{i}]",
            )
            for i, text in enumerate(spec.takeaways)
        ]
        return {
            "kind": kind,
            "slug": doc.slug,
            "from_private": self._private(doc),
            "number": _number(doc) if kind != "sheet" else None,
            "title": spec.title,
            "summary": spec.summary.strip(),
            "date": spec.date,
            "due": spec.due,
            "status": spec.status,
            "body_html": body.html,
            "takeaways": takeaways,
            "toc": body.toc,
            "sources": self._sources(doc),
            "reading_minutes": max(1, round(body.words / 220)) if body.words else 0,
            "search_text": " ".join([spec.title, spec.summary, body.text]),
            "order": order,
            "ref_topics": list(spec.topics),
        }

    def _documents(self, plan):
        for kind, docs in self.bundle.documents.items():
            ordered = sorted(
                docs.values(),
                key=lambda d: (_number(d) if _number(d) is not None else 10**6, d.slug),
            )
            for order, doc in enumerate(ordered):
                plan.documents.append(self._document_row(doc, kind, order))
        if self.bundle.cheatsheet:
            plan.documents.append(self._document_row(self.bundle.cheatsheet, "sheet", 0))

    def _decks(self, plan):
        card_order = 0
        for order, (slug, deck) in enumerate(sorted(self.bundle.decks.items())):
            spec = deck.spec
            plan.decks.append(
                {
                    "slug": slug,
                    "from_private": self._private(deck),
                    "title": spec.title,
                    "description": spec.description.strip(),
                    "order": order,
                    "ref_topic": spec.topic,
                    "ref_lecture": spec.lecture,
                }
            )
            for i, card in enumerate(spec.cards):
                base = ("cards", i)
                if card.kind == "cloze":
                    front = self.md(deck, base + ("cloze",), card.cloze, cloze=True)
                    if 'class="cloze"' not in front.html:
                        self._error(
                            deck,
                            base + ("cloze",),
                            "no {{blank}} survived: check the braces are balanced",
                        )
                else:
                    front = self.md(deck, base + ("q",), card.q)
                back = self.md(deck, base + ("a",), card.a)
                note = self.md(deck, base + ("note",), card.note)
                hook = self.md(deck, base + ("hook",), card.hook)
                steps = self.steps(deck, base + ("steps",), card.steps)
                topic = card.topic or spec.topic
                plan.cards.append(
                    {
                        "key": card.id,
                        "ref_deck": slug,
                        "kind": card.kind,
                        "ref_topic": topic,
                        "high_yield": card.hy,
                        "front_html": front.html,
                        "back_html": back.html,
                        "note_html": note.html,
                        "hook_html": hook.html,
                        "steps": steps,
                        "search_text": " ".join([front.text, back.text, note.text]),
                        "order": card_order,
                    }
                )
                card_order += 1
                if card.hook:
                    plan.hooks.append(
                        {
                            "key": f"{card.id}-hook",
                            "kind": "phrase",
                            "target": self.card_labels.get(card.id, card.id)[:300],
                            "hook_html": hook.html,
                            "explain_html": "",
                            "ref_topic": topic,
                            "ref_card": card.id,
                            "refs": [],
                            "search_text": hook.text,
                            "order": 10_000 + card_order,
                        }
                    )

    def _questions(self, parsed, set_slug, plan):
        total = 0.0
        for i, question in enumerate(parsed.spec.questions):
            base = ("questions", i)
            prompt = self.md(parsed, base + ("q",), question.q)
            solution = self.md(parsed, base + ("solution",), question.solution)
            parts = [
                {
                    "label": part.label or f"({chr(97 + j)})",
                    "q": self.md(parsed, base + ("parts", j, "q"), part.q).html,
                    "solution": self.md(
                        parsed, base + ("parts", j, "solution"), part.solution
                    ).html,
                    "points": part.points,
                }
                for j, part in enumerate(question.parts or [])
            ]
            choices = [
                {
                    "text": self.md(parsed, base + ("choices", j, "text"), choice.text).html,
                    "correct": choice.correct,
                    "why": self.md(parsed, base + ("choices", j, "why"), choice.why).html,
                }
                for j, choice in enumerate(question.choices or [])
            ]
            points = question.points
            if points is None and question.parts:
                points = sum(part.points or 0 for part in question.parts) or None
            total += points or 0
            plan.questions.append(
                {
                    "key": question.id,
                    "ref_set": set_slug,
                    "kind": question.type,
                    "number": str(i + 1),
                    "title": question.title or "",
                    "points": points,
                    "ref_topic": question.topic or getattr(parsed.spec, "topic", None),
                    "prompt_html": prompt.html,
                    "parts": parts,
                    "choices": choices,
                    "correct": question.correct,
                    "solution_html": solution.html,
                    "steps": self.steps(parsed, base + ("steps",), question.steps),
                    "answer_html": self.md(parsed, base + ("answer",), question.answer).html,
                    "source_html": self.md(parsed, base + ("source",), question.source).html,
                    "search_text": " ".join([question.title or "", prompt.text, solution.text]),
                    "order": i,
                }
            )
        return total

    def _question_sets(self, plan):
        for order, (slug, parsed) in enumerate(sorted(self.bundle.problem_sets.items())):
            spec = parsed.spec
            total = self._questions(parsed, slug, plan)
            plan.sets.append(
                {
                    "slug": slug,
                    "from_private": self._private(parsed),
                    "kind": "practice",
                    "title": spec.title,
                    "ref_assessment": None,
                    "paper_slug": "",
                    "ref_topic": spec.topic,
                    "date": None,
                    "duration_min": None,
                    "negative_marking": 0,
                    "total_points": total,
                    "instructions_html": self.md(parsed, ("instructions",), spec.instructions).html,
                    "order": order,
                }
            )
        papers = sorted(self.bundle.papers.items())
        for order, ((exam, paper), parsed) in enumerate(papers):
            if exam not in self.bundle.assessments:
                continue
            spec = parsed.spec
            slug = f"{exam}-{paper}"
            total = self._questions(parsed, slug, plan)
            plan.sets.append(
                {
                    "slug": slug,
                    "from_private": self._private(self.bundle.assessments[exam]),
                    "kind": spec.kind,
                    "title": spec.title,
                    "ref_assessment": exam,
                    "paper_slug": paper,
                    "ref_topic": None,
                    "date": spec.date,
                    "duration_min": spec.duration,
                    "negative_marking": spec.marking.negative,
                    "total_points": total,
                    "instructions_html": self.md(parsed, ("instructions",), spec.instructions).html,
                    "order": 1000 + order,
                }
            )

    def _assessments(self, plan):
        ordered = sorted(
            self.bundle.assessments.items(),
            key=lambda item: (item[1].spec.date is None, item[1].spec.date, item[0]),
        )
        for order, (slug, parsed) in enumerate(ordered):
            spec = parsed.spec
            topics, lectures = self.scopes.get(slug, ([], []))
            weights = [
                {
                    "topic": weight.topic,
                    "title": self.index_.topics.get(weight.topic, weight.topic),
                    "weight": weight.weight,
                    "note": self.md(parsed, ("weights", i, "note"), weight.note).html,
                }
                for i, weight in enumerate(spec.weights)
            ]
            study_plan = {}
            if spec.plan:
                phases = []
                for p, phase in enumerate(spec.plan.phases):
                    base = ("plan", "phases", p)
                    tasks = [
                        {
                            "tag": task.tag or "",
                            "text": self.md(parsed, base + ("tasks", t, "text"), task.text).html,
                            "links": self.links(parsed, base + ("tasks", t, "links"), task.links),
                        }
                        for t, task in enumerate(phase.tasks)
                    ]
                    phases.append(
                        {
                            "label": phase.label,
                            "minutes": phase.minutes,
                            "title": phase.title,
                            "why": self.md(parsed, base + ("why",), phase.why).html,
                            "tasks": tasks,
                        }
                    )
                study_plan = {
                    "intro": self.md(parsed, ("plan", "intro"), spec.plan.intro).html,
                    "phases": phases,
                    "skip": self.md(parsed, ("plan", "skip"), spec.plan.skip).html,
                }
            predictions = [
                {
                    "q": self.md(parsed, ("predictions", i, "q"), prediction.q).html,
                    "why": self.md(parsed, ("predictions", i, "why"), prediction.why).html,
                    "likelihood": prediction.likelihood,
                    "refs": self.links(parsed, ("predictions", i, "refs"), prediction.refs),
                }
                for i, prediction in enumerate(spec.predictions)
            ]
            plan.assessments.append(
                {
                    "slug": slug,
                    "from_private": self._private(parsed),
                    "kind": spec.kind,
                    "title": spec.title,
                    "date": spec.date,
                    "format_html": self.md(parsed, ("format",), spec.format).html,
                    "weights": weights,
                    "plan": study_plan,
                    "predictions": predictions,
                    "order": order,
                    "ref_topics": topics,
                    "ref_lectures": lectures,
                    "ref_includes": [s for s in spec.includes if s in self.bundle.assessments],
                }
            )

    def _reference(self, plan):
        formulas, glossary = self.bundle.formulas, self.bundle.glossary
        if formulas:
            used, order = set(), 0
            for s, section in enumerate(formulas.spec.sections):
                slug = slugify(section.title) or f"section-{s + 1}"
                while slug in used:
                    slug += "-2"
                used.add(slug)
                plan.formula_sections.append(
                    {"slug": slug, "title": section.title, "ref_topic": section.topic, "order": s}
                )
                for r, row in enumerate(section.rows):
                    base = ("sections", s, "rows", r)
                    body = self.md(formulas, base + ("formula",), row.formula)
                    note = self.md(formulas, base + ("note",), row.note)
                    plan.formulas.append(
                        {
                            "key": row.id,
                            "ref_section": slug,
                            "name": row.name,
                            "body_html": body.html,
                            "note_html": note.html,
                            "search_text": " ".join([row.name, body.text, note.text]),
                            "order": order,
                        }
                    )
                    order += 1
        if glossary:
            for i, term in enumerate(glossary.spec.terms):
                definition = self.md(glossary, ("terms", i, "definition"), term.definition)
                plan.terms.append(
                    {
                        "key": term.id,
                        "term": term.term,
                        "definition_html": definition.html,
                        "ref_topic": term.topic,
                        "see": [
                            {"key": other, "term": self.index_.terms[other]}
                            for other in term.see
                            if other in self.index_.terms
                        ],
                        "search_text": f"{term.term} {definition.text}",
                        "order": i,
                    }
                )

    def _memory(self, plan):
        hooks = self.bundle.hooks
        if hooks:
            for i, hook in enumerate(hooks.spec.hooks):
                body = self.md(hooks, ("hooks", i, "hook"), hook.hook)
                explain = self.md(hooks, ("hooks", i, "explain"), hook.explain)
                plan.hooks.append(
                    {
                        "key": hook.id,
                        "kind": hook.kind,
                        "target": hook.target,
                        "hook_html": body.html,
                        "explain_html": explain.html,
                        "ref_topic": hook.topic,
                        "ref_card": None,
                        "refs": self.links(hooks, ("hooks", i, "refs"), hook.refs),
                        "search_text": " ".join([hook.target, body.text, explain.text]),
                        "order": i,
                    }
                )
        for order, (slug, palace) in enumerate(sorted(self.bundle.palaces.items())):
            spec = palace.spec
            plan.palaces.append(
                {
                    "slug": slug,
                    "from_private": self._private(palace),
                    "title": spec.title,
                    "encodes_html": self.md(palace, ("encodes",), spec.encodes).html,
                    "intro_html": self.md(palace, ("intro",), spec.intro).html,
                    "ref_topic": spec.topic,
                    "order": order,
                }
            )
            for i, locus in enumerate(spec.loci):
                plan.loci.append(
                    {
                        "ref_palace": slug,
                        "key": locus.id,
                        "order": i,
                        "place": locus.place,
                        "image_html": self.md(palace, ("loci", i, "image"), locus.image).html,
                        "meaning_html": self.md(palace, ("loci", i, "means"), locus.means).html,
                        "refs": self.links(palace, ("loci", i, "refs"), locus.refs),
                    }
                )
