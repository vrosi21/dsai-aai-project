"""The editor's addresses, all under /edit/<course>/ (config/urls.py mounts them there).

One prefix for everything that changes a course means one line each where the site
limits how much may be sent and how often (config/settings.py).
"""

from django.urls import path

from catalog.models import Document

from . import views

app_name = "editor"

urlpatterns = [
    path("<slug:course>/", views.overview, name="overview"),
    path("<slug:course>/switch/", views.switch, name="switch"),
    path("<slug:course>/preview/", views.preview, name="preview"),
    path("<slug:course>/files/", views.files, name="files"),
    path("<slug:course>/file/", views.file, name="file"),
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
