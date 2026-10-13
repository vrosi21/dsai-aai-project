"""Print what was changed on the site and is not in the owner's course files yet.

    manage.py export_steps            every course
    manage.py export_steps rl mlpr    these courses

It runs on the server, and what it prints is read by `manage.py take_in` on the owner's
computer, over the same SSH connection `publish` uses (`deploy manage export_steps`). It
reads and prints; it changes nothing.

The output is one JSON document between two marker lines, so that anything else a
container prints on the way does not get in the way of reading it:

    {"format": 1,
     "steps": [{"id", "slug", "series", "via", "at", "by", "summary", "base", "version",
                "files": {name: sha}}, …],        oldest first
     "blobs": {sha: the file's bytes, base64}}    each file once, as the site keeps them
"""

import base64
import json

from django.core.management.base import BaseCommand

from desk import handins

BEGIN = "-----BEGIN DSAI STEPS-----"
END = "-----END DSAI STEPS-----"


class Command(BaseCommand):
    help = "Print the steps of courses that the owner's course files do not have yet."

    def add_arguments(self, parser):
        parser.add_argument("courses", nargs="*", help="Course slugs (default: all).")

    def handle(self, *args, **options):
        steps, pool = handins.pending(options["courses"] or None)
        document = {
            "format": 1,
            "steps": steps,
            "blobs": {sha: base64.b64encode(data).decode("ascii") for sha, data in pool.items()},
        }
        self.stdout.write(BEGIN)
        self.stdout.write(json.dumps(document))
        self.stdout.write(END)
