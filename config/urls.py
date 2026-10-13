"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import include, path

if settings.GOOGLE_SIGN_IN:
    # The admin has its own sign-in page with a user name and a password. Nobody here
    # has a password, so on a server that page is only something to guess at. Wrapped
    # like this it never shows: a visitor is sent to the landing page to sign in with
    # Google, and comes back to the admin if their account is staff.
    admin.site.login = staff_member_required(admin.site.login, login_url=settings.LOGIN_URL)

urlpatterns = [
    path("admin/", admin.site.urls),
    # django-allauth: /accounts/google/login/, its callback, and /accounts/logout/.
    path("accounts/", include("allauth.urls")),
    path("account/", include("accounts.urls")),
    path("api/progress/", include("progress.urls")),
    # Asking and reporting (/ask/), and everything about educators (/educators/).
    path("", include("desk.urls")),
    # Editing a course on the site, for the people who write it.
    path("edit/", include("editor.urls")),
    path("", include("catalog.urls")),
]

if settings.USE_DEBUG_TOOLBAR:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()

if settings.USE_BROWSER_RELOAD:
    urlpatterns.append(path("__reload__/", include("django_browser_reload.urls")))

if settings.DEBUG:
    # runserver serves static files itself, but media needs an explicit route in development.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
