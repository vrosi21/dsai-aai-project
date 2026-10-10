"""Views, grouped by area. urls.py refers to them as `views.<name>`."""

from .course import course, topic
from .documents import document, document_list
from .exams import assessment, exams, paper
from .home import home, search, settings_page
from .memory import memory, palace
from .practice import cards, deck, problem_set, problems, review
from .reference import cheatsheet, formulas, glossary
from .source import source_file

__all__ = [
    "assessment",
    "cards",
    "cheatsheet",
    "course",
    "deck",
    "document",
    "document_list",
    "exams",
    "formulas",
    "glossary",
    "home",
    "memory",
    "palace",
    "paper",
    "problem_set",
    "problems",
    "review",
    "search",
    "settings_page",
    "source_file",
    "topic",
]
