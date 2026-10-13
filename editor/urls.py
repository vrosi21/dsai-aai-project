"""The editor's addresses, all under /edit/<course>/ (config/urls.py mounts them there).

One prefix for everything that changes a course means one line each where the site
limits how much may be sent and how often (config/settings.py).
"""

from django.urls import path

from catalog.models import Document

from . import shelves, views, whole
from .items import SHELVES

app_name = "editor"

urlpatterns = [
    path("<slug:course>/", views.overview, name="overview"),
    path("<slug:course>/switch/", views.switch, name="switch"),
    path("<slug:course>/preview/", views.preview, name="preview"),
    path("<slug:course>/preview/fields/", views.preview_fields, name="preview_fields"),
    path("<slug:course>/files/", views.files, name="files"),
    path("<slug:course>/file/", views.file, name="file"),
    # The course's history: its steps, one step, going back to it; and the versions of
    # one file (named in ?name=, as a file's name has slashes in it).
    path("<slug:course>/history/", views.history, name="history"),
    path("<slug:course>/history/<int:pk>/", views.step, name="step"),
    path("<slug:course>/history/<int:pk>/restore/", views.restore, name="restore"),
    path("<slug:course>/history/file/", views.versions, name="versions"),
    path("<slug:course>/history/file/version/", views.version, name="version"),
    path("<slug:course>/cheatsheet/", views.page, {"kind": "sheet"}, name="sheet"),
    path("<slug:course>/cheatsheet/delete/", views.delete, {"kind": "sheet"}, name="sheet_delete"),
]

# Lectures, labs, homework and guides are the same form; the address says which kind, in
# the same words as the course's own pages (catalog/urls.py). "new/" is listed before the
# pattern that takes any name, so it is the one that matches.
for kind, (segment, _title) in Document.SECTIONS.items():
    urlpatterns += [
        path(f"<slug:course>/{segment}/new/", views.page, {"kind": kind}, name=f"{kind}_new"),
        path(f"<slug:course>/{segment}/<slug:name>/", views.page, {"kind": kind}, name=f"{kind}"),
        path(
            f"<slug:course>/{segment}/<slug:name>/delete/",
            views.delete,
            {"kind": kind},
            name=f"{kind}_delete",
        ),
    ]


def shelf_urls(key, lead=None, name="<slug:name>"):
    """The addresses of one kind of YAML content (editor/shelves.py): its containers, one
    container, and one item in it. The extra dict tells the views which kind is meant,
    and the names are made from its key: editor:cards, editor:cards_item, …"""
    lead = lead or f"<slug:course>/{key}/"
    kind = {"shelf": key}
    if SHELVES[key].single:  # the glossary: one container, so one level less
        return [
            path(lead, shelves.index, kind, name=key),
            path(f"{lead}new/", shelves.item, kind, name=f"{key}_item_new"),
            path(f"{lead}<slug:item>/", shelves.item, kind, name=f"{key}_item"),
            path(
                f"{lead}<slug:item>/remove/", shelves.remove_item, kind, name=f"{key}_item_remove"
            ),
        ]
    one = f"{lead}{name}/"
    return [
        path(lead, shelves.index, kind, name=key),
        path(f"{lead}new/", shelves.create, kind, name=f"{key}_new"),
        path(one, shelves.container, kind, name=f"{key}_one"),
        path(f"{one}settings/", shelves.settings, kind, name=f"{key}_settings"),
        path(f"{one}remove/", shelves.remove_container, kind, name=f"{key}_remove"),
        path(f"{one}new/", shelves.item, kind, name=f"{key}_item_new"),
        path(f"{one}<slug:item>/", shelves.item, kind, name=f"{key}_item"),
        path(f"{one}<slug:item>/remove/", shelves.remove_item, kind, name=f"{key}_item_remove"),
    ]


urlpatterns += [
    # The course's own file: what its educators decide, and what the owner does.
    path("<slug:course>/about/", whole.about, name="about"),
    path("<slug:course>/settings/", whole.course_settings, name="course_settings"),
    # An exam is a folder: its own page (one form), and its papers, each a file of
    # questions like a problem set.
    path("<slug:course>/exams/", whole.index, {"sheet": "exams"}, name="exams"),
    path("<slug:course>/exams/new/", whole.form, {"sheet": "exams"}, name="exams_new"),
    path("<slug:course>/exams/<slug:exam>/", whole.exam, name="exam"),
    path(
        "<slug:course>/exams/<slug:name>/about/", whole.form, {"sheet": "exams"}, name="exams_one"
    ),
    path(
        "<slug:course>/exams/<slug:name>/remove/",
        whole.remove,
        {"sheet": "exams"},
        name="exams_remove",
    ),
    *shelf_urls("papers", lead="<slug:course>/exams/<slug:exam>/papers/"),
    path("<slug:course>/palaces/", whole.index, {"sheet": "palaces"}, name="palaces"),
    path("<slug:course>/palaces/new/", whole.form, {"sheet": "palaces"}, name="palaces_new"),
    path(
        "<slug:course>/palaces/<slug:name>/", whole.form, {"sheet": "palaces"}, name="palaces_one"
    ),
    path(
        "<slug:course>/palaces/<slug:name>/remove/",
        whole.remove,
        {"sheet": "palaces"},
        name="palaces_remove",
    ),
    *shelf_urls("cards"),
    *shelf_urls("problems"),
    # A section of the formula sheet is known by its position, not by a name.
    *shelf_urls("formulas", name="<int:name>"),
    *shelf_urls("glossary"),
    *shelf_urls("hooks"),
]
