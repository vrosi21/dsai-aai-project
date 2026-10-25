"""What config/ adds around the apps: the security policy, the rate limits, the limit on
the size of a request, and the production side of config/settings.py.

Settings are read once, when Django starts, from environment variables. To see what a
server would get, the production tests start a second Python process with the server's
variables.
"""

import os
import re
import subprocess
import sys
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings

from desk import handins

PRODUCTION = {
    "DJANGO_DEBUG": "0",
    "DJANGO_SECRET_KEY": "test-only-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJ",
    "DJANGO_ALLOWED_HOSTS": "vault.test",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://vault.test",
}


def manage(*args, unset=(), **env):
    environment = {**os.environ, **PRODUCTION, **env}
    for name in unset:
        environment.pop(name, None)
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=settings.BASE_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class ContentSecurityPolicyTests(SimpleTestCase):
    """The policy fails silently: what it forbids simply does nothing in the browser. These
    tests read the templates for the two things it forbids that are easy to write by habit."""

    @staticmethod
    def templates():
        folders = [settings.BASE_DIR / "templates", *settings.BASE_DIR.glob("*/templates")]
        for folder in folders:
            for path in folder.rglob("*.html"):
                # Template comments may mention a tag; they never reach the browser.
                yield path, re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"))

    def test_every_inline_script_in_a_template_carries_the_nonce(self):
        checked = 0
        for path, text in self.templates():
            for tag in re.findall(r"<script\b[^>]*>", text):
                checked += 1
                fine = " src=" in tag or "csp_nonce" in tag or "application/json" in tag
                self.assertTrue(fine, msg=f"{path.name}: {tag}")
        self.assertGreater(checked, 3)

    def test_no_template_uses_an_inline_event_handler(self):
        """onclick="..." is inline script too, and no nonce can allow it. Alpine's
        @click="..." does the same job and is permitted."""
        for path, text in self.templates():
            found = re.findall(r"""\son[a-z]+=["']""", text)
            self.assertEqual(found, [], msg=f"{path.name} has an inline handler: use @event")


class RateLimitTests(TestCase):
    """config/ratelimit.py, with one small limit so a handful of requests reaches it."""

    SYNC = "/api/progress/sync/"

    def setUp(self):
        caches["limits"].clear()
        self.addCleanup(caches["limits"].clear)
        # Counts are kept per minute of the clock. Stopped mid-minute, a test's requests
        # all fall in the same one, however long the test takes.
        clock = mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)
        clock.start()
        self.addCleanup(clock.stop)
        self.ada = get_user_model().objects.create_user("ada")
        self.bob = get_user_model().objects.create_user("bob")

    def sync_as(self, user):
        client = Client()
        client.force_login(user)
        return client.post(self.SYNC, "{}", content_type="application/json")

    @override_settings(RATE_LIMITS=[("sync", "/api/progress/", 3)])
    def test_the_call_after_the_limit_is_refused_and_told_when_to_come_back(self):
        answers = [self.sync_as(self.ada).status_code for _ in range(4)]
        self.assertEqual(answers, [200, 200, 200, 429])
        refused = self.sync_as(self.ada)
        self.assertEqual(refused.json(), {"error": "too many requests"})
        self.assertTrue(1 <= int(refused["Retry-After"]) <= 60)

    @override_settings(RATE_LIMITS=[("sync", "/api/progress/", 2)])
    def test_one_account_at_its_limit_does_not_slow_another(self):
        for _ in range(3):
            self.sync_as(self.ada)
        self.assertEqual(self.sync_as(self.ada).status_code, 429)
        self.assertEqual(self.sync_as(self.bob).status_code, 200)

    @override_settings(RATE_LIMITS=[("sync", "/api/progress/", 1)])
    def test_other_pages_are_not_counted(self):
        self.client.force_login(self.ada)
        for _ in range(3):
            self.assertEqual(self.client.get("/settings/").status_code, 200)

    @override_settings(RATE_LIMITS=[("sign-in", "/accounts/", 2)])
    def test_signed_out_the_visitors_address_is_what_counts(self):
        def visit(address):
            return self.client.get("/accounts/login/", HTTP_CF_CONNECTING_IP=address).status_code

        self.assertEqual([visit("203.0.113.5") for _ in range(3)], [200, 200, 429])
        self.assertEqual(visit("203.0.113.9"), 200)  # someone else, behind the same proxy

    @override_settings(RATE_LIMITS=[("sign-in", "/accounts/", 2)])
    def test_behind_a_tunnel_the_address_the_worker_passes_on_is_what_counts(self):
        """Through a Cloudflare Tunnel, CF-Connecting-IP is one placeholder for everyone."""
        placeholder = "2a06:98c0:3600::103"

        def visit(address):
            return self.client.get(
                "/accounts/login/", HTTP_CF_CONNECTING_IP=placeholder, HTTP_X_VISITOR_IP=address
            ).status_code

        self.assertEqual([visit("203.0.113.5") for _ in range(3)], [200, 200, 429])
        self.assertEqual(visit("203.0.113.9"), 200)  # not refused for the first one's sake

    @override_settings(RATE_LIMITS=[("search", "/search/", 1)])
    def test_a_page_is_refused_in_plain_words(self):
        self.client.force_login(self.ada)
        self.client.get("/search/", {"q": "fisher"})
        refused = self.client.get("/search/", {"q": "fisher"})
        self.assertEqual(refused.status_code, 429)
        self.assertIn(b"Wait a minute", refused.content)

    def test_the_limits_shipped_leave_room_for_real_use(self):
        """Fast flashcard drilling saves about every three seconds."""
        limits = {name: limit for name, _, limit in settings.RATE_LIMITS}
        self.assertGreaterEqual(limits["sync"], 60)
        self.assertGreaterEqual(limits["sign-in"], 100)  # a class signing in at once


class BodyLimitTests(TestCase):
    """config/bodylimit.py: a request too large for its page is refused before it is read."""

    HAND_IN = "/educators/courses/example/hand-in/"
    MB = 1024 * 1024

    def setUp(self):
        caches["limits"].clear()
        self.addCleanup(caches["limits"].clear)
        self.ada = get_user_model().objects.create_user("ada")

    def upload(self, megabytes=3):
        return {"zip": SimpleUploadedFile("course.zip", b"0" * megabytes * self.MB)}

    def test_a_visitor_cannot_make_the_server_store_an_upload(self):
        """The CSRF check reads the form, files and all, before the site asks who is
        there. So the refusal has to come first: the body is never parsed.

        The check only gets that far for a request carrying a CSRF cookie, which is no
        obstacle: a visitor's own program sends whatever cookie it likes."""
        visitor = Client(enforce_csrf_checks=True)
        visitor.cookies["csrftoken"] = "a" * 32
        with mock.patch("django.http.multipartparser.MultiPartParser.parse") as parse:
            answer = visitor.post("/", self.upload())
        self.assertEqual(answer.status_code, 413)
        parse.assert_not_called()

    def test_nor_on_the_page_that_takes_files(self):
        self.assertEqual(self.client.post(self.HAND_IN, self.upload()).status_code, 413)

    def test_signed_in_a_large_request_is_refused_on_every_other_page(self):
        self.client.force_login(self.ada)
        answer = self.client.post("/ask/", self.upload())
        self.assertEqual(answer.status_code, 413)
        self.assertIn(b"more than this page accepts", answer.content)

    def test_signed_in_the_hand_in_page_takes_a_file(self):
        """Ada writes no course, so the page turns her away; but it is the page that
        answers, having received the file."""
        self.client.force_login(self.ada)
        answer = self.client.post(self.HAND_IN, self.upload())
        self.assertNotEqual(answer.status_code, 413)

    def test_and_refuses_one_larger_than_a_hand_in_may_be(self):
        """The size is the one the request declares; nothing that large is built here."""
        self.client.force_login(self.ada)
        answer = self.client.post(self.HAND_IN, {}, CONTENT_LENGTH=str(40 * self.MB))
        self.assertEqual(answer.status_code, 413)
        self.assertIn(b"26 MB at most", answer.content)

    def test_the_api_is_refused_in_json(self):
        self.client.force_login(self.ada)
        answer = self.client.post(
            "/api/progress/sync/",
            "{}",
            content_type="application/json",
            CONTENT_LENGTH=str(3 * self.MB),
        )
        self.assertEqual(answer.status_code, 413)
        self.assertEqual(answer.json(), {"error": "request too large"})

    def test_a_length_that_is_not_a_number_counts_as_no_body(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get("/settings/", CONTENT_LENGTH="lots").status_code, 200)

    def test_the_limit_shipped_leaves_room_for_the_largest_hand_in(self):
        """Two numbers in two files: the page's own limit must be the one a person meets,
        because it answers with a sentence about their zip."""
        limits = dict(settings.BODY_LIMITS)
        self.assertGreater(limits["/educators/courses/"], handins.MAX_UPLOAD + 64 * 1024)
        self.assertTrue(self.HAND_IN.startswith("/educators/courses/"))


class LogPromiseTests(SimpleTestCase):
    """The privacy page says what the server notes about a request. The line gunicorn
    writes is set in the Dockerfile; this holds the one to the other."""

    def test_the_log_line_holds_what_the_privacy_page_says_and_no_more(self):
        dockerfile = (settings.BASE_DIR / "Dockerfile").read_text(encoding="utf-8")
        line = re.search(r'"--access-logformat", "(.+)"\]', dockerfile).group(1)
        fields = set(re.findall(r"%\((.+?)\)", line))
        # time, method, path without the query, status, size, duration, browser
        self.assertEqual(fields, {"t", "m", "U", "s", "b", "M", "a"})
        # Named apart, because each would break a sentence of the page: the address
        # (h), the whole request line and the query (r, q), the page come from (f), the
        # signed-in name (u), and any header ({...}i), which is where a cookie is.
        for told in ("h", "r", "q", "f", "u"):
            self.assertNotIn(told, fields)
        self.assertFalse([name for name in fields if name.startswith("{")])
        page = (settings.BASE_DIR / "catalog/templates/catalog/privacy.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("It does not note who asked or what was searched for", page)

    def test_the_proxy_keeps_the_request_out_of_what_it_writes_about_a_failure(self):
        """Caddy puts the request into its line about a failed one: headers (the shared
        secret, the visitor's address), the address line with its query. Found in the
        server's own log on 2026-10-12. The proxy file has to leave each of them out."""
        proxy = (settings.BASE_DIR / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        for field in ("headers", "uri", "remote_ip", "client_ip"):
            self.assertRegex(proxy, rf"request>{field}\s+delete")


class ProductionSettingsTests(SimpleTestCase):
    DEBUG = "from django.conf import settings; print('DEBUG IS', settings.DEBUG)"

    def test_debug_is_off_on_a_server_even_when_nobody_said_so(self):
        """A server names its hosts. If DJANGO_DEBUG was forgotten there, the answer must
        still be off: debug mode shows settings in error pages and offers a sign-in
        without Google."""
        result = manage("shell", "-c", self.DEBUG, unset=["DJANGO_DEBUG"])
        self.assertIn("DEBUG IS False", result.stdout, msg=result.stderr)

    def test_debug_is_on_where_nothing_is_configured(self):
        """The laptop: no variables at all, and `runserver` just works."""
        unset = ["DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "DJANGO_SECRET_KEY"]
        result = manage("shell", "-c", self.DEBUG, unset=unset)
        self.assertIn("DEBUG IS True", result.stdout, msg=result.stderr)

    def test_debug_can_be_asked_for_by_name(self):
        result = manage("shell", "-c", self.DEBUG, DJANGO_DEBUG="1")
        self.assertIn("DEBUG IS True", result.stdout, msg=result.stderr)

    def test_refuses_to_start_without_a_secret_key(self):
        """The key in the repository is public; a server must never fall back to it."""
        result = manage("check", DJANGO_SECRET_KEY="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be set", result.stderr)

    def test_passes_the_deployment_checklist(self):
        result = manage("check", "--deploy", "--fail-level", "WARNING")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_the_admin_offers_no_password_form_once_google_is_configured(self):
        """Nobody has a password, so the form would only invite guessing. With Google
        sign-in set up, the admin's own login page sends a visitor to the landing page."""
        look = (
            "from django.test import Client;"
            "r = Client().get('/admin/login/', secure=True, HTTP_HOST='vault.test');"
            "print('ANSWER', r.status_code, r.headers.get('Location'))"
        )
        with_google = manage(
            "shell", "-c", look, GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="secret"
        )
        self.assertIn("ANSWER 302 /?next=/admin/login/", with_google.stdout, msg=with_google.stderr)
