"""A ceiling on how often one person, or one address, may call the costly parts of the site.

Not for load: a class of fifty is nothing to this site. It is for the day one account or
one script calls the same address in a loop, so that it slows down only itself.

Three small pieces, each replaceable without touching the others:

    who(request)          the name a request is counted under
    over(name, who, n)    counts one more call this minute; true once that is more than n
    RateLimitMiddleware   applies settings.RATE_LIMITS and answers 429 Too Many Requests

A new limit is one line in settings.RATE_LIMITS. Counts live in the cache named "limits"
(settings.CACHES), which is memory inside each gunicorn worker. Requests are spread over
the workers, so with two of them the real ceiling lies between the number in the settings
and twice that number. Good enough for a ceiling; a shared cache would make it exact.
"""

import logging
import time

from django.conf import settings
from django.core.cache import caches
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

WINDOW = 60  # seconds


def who(request):
    """The name a request is counted under: the account, or else the visitor's address.

    The address comes from X-Visitor-IP, which edge/_worker.js copies from Cloudflare's
    CF-Connecting-IP on every request. It has to be copied: once the server is itself
    behind Cloudflare (a Cloudflare Tunnel), the worker's request crosses from one
    Cloudflare zone to another, and Cloudflare then puts the same placeholder in
    CF-Connecting-IP for every visitor. Neither header can be forged: the worker replaces
    whatever a visitor sent as X-Visitor-IP, and the server answers nobody but the worker
    (docs/DEPLOYMENT.md). CF-Connecting-IP alone is what a worker from before this sends.
    The connection's own address is only right in development: on the server it is the
    proxy, the same for everyone.
    """
    # No spaces in these names: they become cache keys, and Django warns about keys that
    # a memcached server could not store.
    if request.user.is_authenticated:
        return f"account-{request.user.pk}"
    address = (
        request.META.get("HTTP_X_VISITOR_IP")
        or request.META.get("HTTP_CF_CONNECTING_IP")
        or request.META.get("REMOTE_ADDR", "")
    )
    return f"address-{address}"


def over(name, counted_as, limit):
    """Count one more call in the current minute. True when that makes more than `limit`."""
    cache = caches["limits"]
    key = f"{name}:{counted_as}:{int(time.time() // WINDOW)}"
    # add() stores the starting value only if the key is new; incr() is then one atomic
    # step, so two requests arriving together cannot both read the same count.
    cache.add(key, 0, timeout=2 * WINDOW)
    try:
        return cache.incr(key) > limit
    except ValueError:  # the entry was evicted between the two calls: start again
        cache.set(key, 1, timeout=2 * WINDOW)
        return False


class RateLimitMiddleware:
    """Runs after AuthenticationMiddleware (it needs request.user) and before the views."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        for name, prefix, limit in settings.RATE_LIMITS:
            if request.path.startswith(prefix):
                counted_as = who(request)
                if over(name, counted_as, limit):
                    return self.refuse(request, name, counted_as)
                break  # the first matching rule is the only one applied
        return self.get_response(request)

    @staticmethod
    def refuse(request, name, counted_as):
        # Accounts are named in the log so a runaway one can be found; addresses are not,
        # because the site promises to keep nothing about visitors.
        subject = counted_as if counted_as.startswith("account") else "an address"
        logger.warning("Rate limit '%s' reached by %s", name, subject)
        wait = WINDOW - int(time.time()) % WINDOW
        if request.path.startswith("/api/"):
            response = JsonResponse({"error": "too many requests"}, status=429)
        else:
            response = HttpResponse(
                "Too many requests. Wait a minute, then try again.",
                status=429,
                content_type="text/plain; charset=utf-8",
            )
        response["Retry-After"] = str(wait)
        return response
