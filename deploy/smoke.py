"""Ask the running site for a few pages and one static file. Exit 1 if anything is wrong.

Run inside the web container, where gunicorn listens on port 8000:

    python deploy/smoke.py [/extra/path/ ...]

The deploy script runs it after every deploy, and CI runs it against a freshly built image.
Standard library only.
"""

import os
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
# Django rejects a request whose Host it does not know, and redirects one that did not
# arrive over HTTPS. The proxy in front normally supplies both; here we stand in for it.
HEADERS = {
    "Host": os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0],
    "X-Forwarded-Proto": "https",
}


def get(path):
    request = urllib.request.Request(BASE + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError as error:
        return 0, str(error).encode()


def main(paths):
    failures = []
    home = b""
    for path in ["/", *paths]:
        status, body = get(path)
        print(f"  {status}  {path}")
        if status != 200:
            failures.append(f"{path} answered {status} {body[:200].decode(errors='replace')}")
        elif path == "/":
            home = body

    # The page must link the hashed script that collectstatic produced, and it must load.
    script = re.search(rb'src="(/static/js/app\.[0-9a-f]{8,}\.js)"', home)
    if home and not script:
        failures.append("the home page does not link a hashed app.js: did collectstatic run?")
    elif script:
        path = script.group(1).decode()
        status, _ = get(path)
        print(f"  {status}  {path}")
        if status != 200:
            failures.append(f"{path} answered {status}")

    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
