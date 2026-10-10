"""Every address of the site, in one place.

Each `path()` maps a URL pattern to a view function and gives it a name. Templates and the
importer never hard-code an address: they ask for it by name (`{% url 'catalog:deck' … %}`,
`reverse("catalog:deck", …)`), so changing a pattern here changes it everywhere.
"""

from django.urls import path

from . import views

app_name = "catalog"

# Lectures, labs, homework and guides share two views; the extra dict tells them which kind.
documents = [
    ("lectures", "lecture", "lectures", "lecture"),
    ("labs", "lab", "labs", "lab"),
    ("homework", "homework", "homework_list", "homework"),
    ("guides", "guide", "guides", "guide"),
]

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("settings/", views.settings_page, name="settings"),
    path("courses/<slug:course>/", views.course, name="course"),
    path("courses/<slug:course>/topics/<slug:key>/", views.topic, name="topic"),
    path("courses/<slug:course>/cards/", views.cards, name="cards"),
    path("courses/<slug:course>/cards/review/", views.review, name="review"),
    path("courses/<slug:course>/cards/<slug:slug>/", views.deck, name="deck"),
    path("courses/<slug:course>/problems/", views.problems, name="problems"),
    path("courses/<slug:course>/problems/<slug:slug>/", views.problem_set, name="problem_set"),
    path("courses/<slug:course>/exams/", views.exams, name="exams"),
    path("courses/<slug:course>/exams/<slug:assessment>/", views.assessment, name="assessment"),
    path("courses/<slug:course>/exams/<slug:assessment>/<slug:paper>/", views.paper, name="paper"),
    path("courses/<slug:course>/formulas/", views.formulas, name="formulas"),
    path("courses/<slug:course>/glossary/", views.glossary, name="glossary"),
    path("courses/<slug:course>/cheatsheet/", views.cheatsheet, name="cheatsheet"),
    path("courses/<slug:course>/memory/", views.memory, name="memory"),
    path("courses/<slug:course>/memory/palaces/<slug:slug>/", views.palace, name="palace"),
    path("courses/<slug:course>/source/<path:path>", views.source_file, name="source"),
]

for segment, kind, list_name, detail_name in documents:
    urlpatterns += [
        path(
            f"courses/<slug:course>/{segment}/",
            views.document_list,
            {"kind": kind},
            name=list_name,
        ),
        path(
            f"courses/<slug:course>/{segment}/<slug:slug>/",
            views.document,
            {"kind": kind},
            name=detail_name,
        ),
    ]
