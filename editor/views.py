"""Editing a course on the site.

Every view here starts the same way: find the course, and answer "no such page" unless
this person may write it (`_open`). What a view then does is small: fill a form from a
file, or take a form and hand the new text of that file to `desk.handins.change`, which
is where a course is allowed to change. A save is live at once; saves within half an
hour are one step in the course's history, and a step can be taken back.

The pattern of every form view is Django's usual one, "post, redirect, get": a POST that
worked answers with a redirect, so the page that follows is a plain GET. Reloading it, or
going back to it, never sends the form a second time.
"""

from datetime import datetime

from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.models import Document
from catalog.views.common import load_course
from desk import blobs, handins, mail
from desk.models import HandIn
from desk.notify import notify
from ingest import handin
from ingest.markdown import CALLOUTS

from . import history as versions_of
from . import source
from .forms import FileForm, PageForm

# What a kind of page is called in a sentence: "a new lecture".
NOUNS = {"lecture": "lecture", "lab": "lab", "homework": "homework", "guide": "guide"}
SHEET = Document.Kind.SHEET
RESERVED = {"new"}  # addresses the editor's own URLs use
GONE = "gone"  # in place of a fingerprint: the file is not there any more
MAX_PREVIEW = 400_000  # characters of text one preview renders; a long lecture has 40,000


def _open(request, slug, active=""):
    """The course and its page context, for someone who may write it. To anyone else the
    editor does not exist."""
    course, context = load_course(request, slug, active)
    if not context["can_edit"]:
        raise Http404
    # Whoever opens the editor is editing: the course's own pages then show their
    # "Edit this page" buttons, which is how one gets back here.
    if not request.session.get("editing"):
        request.session["editing"] = True
        context["editing"] = True
    return course, context


def _back(request, fallback):
    """The address in `next`, if it is on this site."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    allowed = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if allowed else fallback


def _page_urls(course, kind, name=None):
    """The addresses around one page: its form, its removal, itself on the site."""
    if kind == SHEET:
        return {
            "edit": reverse("editor:sheet", args=[course.slug]),
            "delete": reverse("editor:sheet_delete", args=[course.slug]),
            "public": reverse("catalog:cheatsheet", args=[course.slug]),
        }
    if name is None:
        return {"edit": reverse(f"editor:{kind}_new", args=[course.slug])}
    return {
        "edit": reverse(f"editor:{kind}", args=[course.slug, name]),
        "delete": reverse(f"editor:{kind}_delete", args=[course.slug, name]),
        "public": reverse(f"catalog:{kind}", args=[course.slug, name]),
    }


def overview(request, course):
    """Everything in the course that can be edited here, and what was changed lately."""
    course, context = _open(request, course)
    pages = list(course.documents.only("kind", "slug", "number", "title", "status", "course"))
    sections = [
        {
            "kind": kind,
            "title": title,
            "noun": NOUNS[kind],
            "new": reverse(f"editor:{kind}_new", args=[course.slug]),
            "pages": [
                {"doc": doc, "edit": reverse(f"editor:{kind}", args=[course.slug, doc.slug])}
                for doc in pages
                if doc.kind == kind
            ],
        }
        for kind, (_segment, title) in Document.SECTIONS.items()
    ]
    context.update(
        sections=sections,
        sheet=next((doc for doc in pages if doc.kind == SHEET), None),
        # light(): a list of steps needs their summaries, not the course each one kept.
        steps=HandIn.objects.filter(slug=course.slug).light().select_related("user")[:5],
        session_minutes=int(handins.SESSION.total_seconds() // 60),
        deleted=request.GET.get("deleted"),
    )
    return render(request, "editor/overview.html", context)


@require_POST
def switch(request, course):
    """Turn editing on or off. It is remembered in the session: a small store Django
    keeps per signed-in browser (the cookie holds only its key), which is the place for
    how someone wants to see the site right now."""
    course, context = load_course(request, course)
    if not context["can_edit"]:
        raise Http404
    request.session["editing"] = request.POST.get("to") == "on"
    return redirect(_back(request, course.get_absolute_url()))


def _save(request, course, name, text, seen):
    """Save one file (editor.source.save) and, when that began a new step in the course's
    history, tell the owner: the bot, and the site's own address. Like every message to
    the owner it says that something happened and where to look, never who did it; and it
    is queued with on_commit, so it goes out only once the step is really stored.

    Raises handins.Refused, and then nothing changed and nobody is told."""
    step, fresh = source.save(request.user, course, name, text, seen)
    _tell(request, course, step, fresh)
    return step


def _tell(request, course, step, fresh):
    """The owner's bot and the site's own address, once per step."""
    if not fresh or request.user.is_staff:  # the owner need not be told what they did
        return
    url = request.build_absolute_uri(reverse("admin:desk_handin_change", args=[step.pk]))
    title = f"Edited on the site: {course.title}"
    message = "It is live. Open the admin to see it or to take it back."
    level = "warning" if step.removes else "info"
    key = f"dsai-handin-{step.pk}"

    def tell():
        notify(title, message, level=level, url=url, dedupe_key=key)
        mail.send_owner(title, message, url)

    transaction.on_commit(tell)


def _refusal(error, noun="page"):
    """A refusal as the template shows it: a heading, and what to do."""
    heads = {
        "conflict": f"Not saved: this {noun} changed on the site while you were editing.",
        "invalid": "Not saved: the site's check found errors.",
        "owner": "Not saved: this changes what only the owner of the site may change.",
        "busy": "Not saved: someone is saving this course at this very moment.",
        "bad": "Not saved.",
    }
    return {"kind": error.kind, "head": heads.get(error.kind, "Not saved."), "lines": error.lines}


def _seen(request, form):
    """Which version of the file this save was made from. Normally the one the form was
    filled from. After a conflict the person may press "Save mine over it": they have
    been shown that the file changed and choose their text, so the save is made from the
    version that is there now. What it replaces stays in the course's history."""
    over = request.POST.get("over")
    if over is None:
        return form.cleaned_data["seen"]
    return "" if over == GONE else over


def page(request, course, kind, name=None):
    """The form for one Markdown page: lecture notes, a lab, a homework, a guide or the
    cheat sheet. Without a `name` it is the form for a new one."""
    segment = "cheatsheet" if kind == SHEET else Document.SECTIONS[kind][0]
    course, context = _open(request, course, segment)
    new = name is None and kind != SHEET
    try:
        current = (
            source.Page(name="") if new else source.load_page(course, source.page_name(kind, name))
        )
    except source.Unreadable:
        return redirect(
            f"{reverse('editor:file', args=[course.slug])}?name={source.page_name(kind, name)}"
        )
    if not new and kind != SHEET and not current.exists:
        raise Http404

    taken = set()
    if new:
        taken = set(course.documents.filter(kind=kind).values_list("slug", flat=True)) | RESERVED
    form = PageForm(
        request.POST or None,
        kind=kind,
        page=current,
        topics=list(course.topics.values_list("key", "title")),
        taken=taken,
    )
    refused = None
    if request.method == "POST" and form.is_valid():
        slug = form.cleaned_data["name"] if new else name
        file = source.page_name(kind, slug)
        text = source.compose(current, form.meta, form.cleaned_data["body"])
        seen = _seen(request, form)
        urls = _page_urls(course, kind, slug)
        try:
            _save(request, course, file, text, seen)
        except handins.Refused as error:
            if error.kind == "nothing":
                return redirect(f"{urls['edit']}?saved=same")
            refused = _refusal(error)
            if error.kind == "conflict":
                refused["now"] = source.fingerprint(source.read(course, file)) or GONE
        else:
            return redirect(urls["public"] if "view" in request.POST else f"{urls['edit']}?saved=1")

    urls = _page_urls(course, kind, None if new else name)
    context.update(
        form=form,
        page=current,
        kind=kind,
        new=new,
        noun="cheat sheet" if kind == SHEET else NOUNS[kind],
        refused=refused,
        saved=request.GET.get("saved"),
        urls=urls,
        # Where the preview looks for images: beside the file.
        file=current.name or source.page_name(kind, "new"),
        raw=f"{reverse('editor:file', args=[course.slug])}?name={current.name}",
        earlier=f"{reverse('editor:versions', args=[course.slug])}?name={current.name}",
        callouts=[(key, label) for key, (label, _folded) in CALLOUTS.items()],
    )
    return render(request, "editor/page.html", context)


def delete(request, course, kind, name=None):
    """Remove a page: asked first (GET), done on POST."""
    segment = "cheatsheet" if kind == SHEET else Document.SECTIONS[kind][0]
    course, context = _open(request, course, segment)
    file = source.page_name(kind, name)
    try:
        current = source.load_page(course, file)
    except source.Unreadable:
        current = source.Page(file, "", {}, "", source.fingerprint(source.read(course, file)))
    if not current.exists:
        raise Http404
    refused = None
    if request.method == "POST":
        try:
            _save(request, course, file, None, request.POST.get("seen", ""))
        except handins.Refused as error:
            refused = _refusal(error)
        else:
            return redirect(f"{reverse('editor:overview', args=[course.slug])}?deleted=1")
    context.update(
        page=current,
        kind=kind,
        noun="cheat sheet" if kind == SHEET else NOUNS[kind],
        title=current.meta.get("title") or file,
        refused=refused,
        urls=_page_urls(course, kind, name),
    )
    return render(request, "editor/delete.html", context)


def files(request, course):
    """Every file of the course, by folder. Text files open as they are."""
    course, context = _open(request, course)
    folders = {}
    for name, size, text in source.listing(course):
        where, _slash, last = name.rpartition("/")
        folders.setdefault(where or "the course folder", []).append(
            {"name": name, "last": last, "size": size, "text": text}
        )
    context.update(folders=folders, file_url=reverse("editor:file", args=[course.slug]))
    return render(request, "editor/files.html", context)


def file(request, course):
    """One text file as it is: the way to change what has no form of its own."""
    course, context = _open(request, course)
    name = request.GET.get("name", "")
    data = source.read(course, name)
    if data is None or not name.lower().endswith(tuple(handin.TEXT)):
        raise Http404
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Http404 from None
    here = f"{reverse('editor:file', args=[course.slug])}?name={name}"
    form = FileForm(request.POST or None, initial={"text": text, "seen": source.fingerprint(data)})
    refused = None
    if request.method == "POST" and form.is_valid():
        # A browser sends a text box with Windows line endings; a course file has Unix ones.
        typed = form.cleaned_data["text"].replace("\r\n", "\n").replace("\r", "\n")
        seen = _seen(request, form)
        try:
            _save(request, course, name, typed, seen)
        except handins.Refused as error:
            if error.kind == "nothing":
                return redirect(f"{here}&saved=same")
            refused = _refusal(error, "file")
            if error.kind == "conflict":
                refused["now"] = source.fingerprint(source.read(course, name)) or GONE
        else:
            return redirect(f"{here}&saved=1")
    context.update(
        form=form,
        name=name,
        refused=refused,
        saved=request.GET.get("saved"),
        is_course_file=name == "course.yaml",
        earlier=f"{reverse('editor:versions', args=[course.slug])}?name={name}",
    )
    return render(request, "editor/file.html", context)


@require_POST
def preview(request, course):
    """The text of a page as the site would show it. Asked by the form while someone
    types (static/js/components/editor.js); answers JSON, stores nothing."""
    course, _context = _open(request, course)
    name = request.POST.get("file", "")
    if not source.plain(name) or not name.endswith(".md"):
        raise Http404
    body = request.POST.get("body", "")
    if len(body) > MAX_PREVIEW:
        return JsonResponse({"error": "too long to preview"}, status=413)
    shown = source.preview(course, name, body, request.POST.getlist("takeaways")[:30])
    return JsonResponse(shown)


# ---- the history ---------------------------------------------------------------------------


def history(request, course):
    """Every step of the course, newest first."""
    course, context = _open(request, course)
    context.update(
        steps=HandIn.objects.filter(slug=course.slug).light().select_related("user"),
        restored=request.GET.get("restored"),
        now=handin.content_version(source.folder(course)),
    )
    return render(request, "editor/history.html", context)


def _step_page(request, course, context, row, refused=None):
    began, after = handins.before(row), handins.kept(row)
    changes = versions_of.compare(began, after) if began is not None else None
    now = handin.content_version(source.folder(course))
    # The saves of a sitting, when there was more than one: which files each changed.
    saves = [
        {"at": datetime.fromisoformat(entry["at"]), "files": sorted(entry["files"].items())}
        for entry in row.trail
    ]
    context.update(
        step=row,
        changes=changes,
        saves=saves if len(saves) > 1 else [],
        is_now=row.version == now,
        refused=refused,
        versions_url=reverse("editor:versions", args=[course.slug]),
        version_url=reverse("editor:version", args=[course.slug]),
    )
    return render(request, "editor/step.html", context)


def step(request, course, pk):
    """One step: what it changed, line by line, and the way back to it."""
    course, context = _open(request, course)
    row = get_object_or_404(HandIn.objects.select_related("user"), pk=pk, slug=course.slug)
    return _step_page(request, course, context, row)


@require_POST
def restore(request, course, pk):
    """Make the course what it was after a step. That is a new step, built on what is
    there now: nothing leaves the history, and it can be taken back like any other."""
    course, context = _open(request, course)
    row = get_object_or_404(HandIn.objects.select_related("user"), pk=pk, slug=course.slug)
    if request.POST.get("sure") != "yes":
        return redirect(f"{reverse('editor:step', args=[course.slug, row.pk])}?unsure=1#restore")
    wanted = handins.kept(row)
    try:
        made, fresh = handins.change(request.user, course, lambda _files: dict(wanted))
    except handins.Refused as error:
        if error.kind == "nothing":
            return redirect(f"{reverse('editor:history', args=[course.slug])}?restored=same")
        return _step_page(request, course, context, row, _refusal(error, "course"))
    _tell(request, course, made, fresh)
    return redirect(f"{reverse('editor:history', args=[course.slug])}?restored={row.pk}")


def versions(request, course):
    """Every version one file has had."""
    course, context = _open(request, course)
    name = request.GET.get("name", "")
    found = versions_of.versions(course, name) if source.plain(name) else []
    exists = source.read(course, name) is not None
    if not found and not exists:
        raise Http404
    context.update(
        name=name,
        versions=found,
        exists=exists,
        version_url=reverse("editor:version", args=[course.slug]),
    )
    return render(request, "editor/versions.html", context)


def version(request, course):
    """One version of one file: what it said, how it differs from the file as it is now,
    and (POST) putting it back."""
    course, context = _open(request, course)
    name = request.GET.get("name", "")
    sha = request.GET.get("sha", "")
    found = versions_of.versions(course, name) if source.plain(name) else []
    # Only a version this file of this course really had: a hash alone opens nothing.
    this = next((item for item in found if item.sha and item.sha == sha), None)
    data = blobs.one(sha) if this is not None else None
    if data is None:
        raise Http404
    current = source.read(course, name)
    refused, stale = None, False
    if request.method == "POST":
        # The page showed what putting this back would change. If the file is no longer
        # what that was compared with, show the comparison again instead of saving.
        stale = request.POST.get("seen", "") != source.fingerprint(current)
        if not stale:
            try:
                _save(request, course, name, data, source.fingerprint(current))
            except handins.Refused as error:
                if error.kind == "conflict":
                    stale = True
                elif error.kind != "nothing":
                    refused = _refusal(error, "file")
        if not stale and refused is None:
            return redirect(f"{reverse('editor:versions', args=[course.slug])}?name={name}&put=1")
        current = source.read(course, name)
    change = versions_of.compare({name: current} if current is not None else {}, {name: data})
    context.update(
        name=name,
        version=this,
        text=data.decode("utf-8", errors="replace")
        if name.lower().endswith(tuple(handin.TEXT))
        else None,
        change=change[0] if change else None,
        seen=source.fingerprint(current),
        refused=refused,
        stale=stale,
        versions_url=f"{reverse('editor:versions', args=[course.slug])}?name={name}",
    )
    return render(request, "editor/version.html", context)
