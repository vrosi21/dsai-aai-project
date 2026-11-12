from django.urls import path

from . import views

app_name = "desk"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    # The dashboard of someone who writes courses; for everyone else, what that means.
    path("educators/", views.educators, name="educators"),
    path("educators/open/", views.open_courses, name="open_courses"),
    path("educators/apply/", views.apply, name="apply"),
    path("educators/guide/", views.guide, name="guide"),
    # The files of one course as a zip, for the person who writes it; and the way back:
    # the zip they hand in.
    path("educators/courses/<slug:series>/", views.course, name="course"),
    path("educators/courses/<slug:series>/files.zip", views.files, name="files"),
    path("educators/courses/<slug:series>/hand-in/", views.hand_in, name="hand_in"),
    path("educators/hand-ins/<int:pk>.zip", views.handin_zip, name="handin_zip"),
    path("educators/hand-ins/<int:pk>/take-back/", views.take_back, name="take_back"),
    # The bell at the top of every page: the list, and the address that marks it read.
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read/", views.notifications_read, name="notifications_read"),
    path("educators/requests/<int:pk>/done/", views.resolve, name="resolve"),
    # The owner's switch on an item's page: public, unlisted or private.
    path("courses/<slug:course>/visibility/", views.visibility, name="visibility"),
]
