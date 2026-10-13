"""Which folder holds the files the site shows for a course.

Almost always that is the owner's folder under the courses directory. The exception is a
course that an educator has added to since the owner last published: the site then shows
the course as handed in, from a folder of its own, until the owner's files contain those
additions. That decision belongs to whoever keeps the hand-ins (desk/handins.py), which
registers itself here; this module only knows that such a thing may exist.

    folders()            slug -> folder, for every course there is
    folder("rl")         the folder of one course, or None
    folders(settle=True) the same, and let the providers tidy up what has been overtaken

`folders()` is what a sync asks, and it is exact. `folder(slug)` is what a page asks while
someone waits for it, so a provider may answer it from what it already has on disk.
"""

from django.conf import settings

from .discover import find_courses

_providers = []


def provide(provider):
    """Register `provider(base, settle=..., notes=..., only=...) -> folders`. It is given
    the owner's folders by slug and returns the folders to use instead. `only` is a slug
    when just that course is asked about."""
    if provider not in _providers:
        _providers.append(provider)


def folders(*, settle=False, notes=None, only=None):
    """The folder to read each course from. `notes` (a dict) collects one line per course
    worth telling whoever is syncing."""
    found = find_courses(settings.CONTENT_ROOTS)
    notes = notes if notes is not None else {}
    for provider in _providers:
        found = provider(found, settle=settle, notes=notes, only=only)
    return found


def folder(slug):
    return folders(only=slug).get(slug)
