"""Images referenced from course content.

They live next to the Markdown under courses/, which the web server never exposes. Sync
copies each one into MEDIA_ROOT under a name derived from its bytes, so a changed image gets
a new URL (no stale browser cache) and an unchanged one is copied once.
"""

import hashlib
import shutil
from pathlib import Path

from django.conf import settings


class Assets:
    def __init__(self, course):
        self.course = course
        self.files: dict[Path, str] = {}  # file on disk -> path under MEDIA_ROOT

    def knows(self, path):
        """Whether an image that is not in the course folder is known all the same. Never,
        for a sync. The editor's preview answers otherwise for an image that was
        uploaded and is waiting for its text to be saved (editor/source.py)."""
        return False

    def add(self, path):
        """Register an image; returns the URL the page should use."""
        relative = self.files.get(path)
        if relative is None:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            relative = f"courses/{self.course}/{digest}{path.suffix.lower()}"
            self.files[path] = relative
        return settings.MEDIA_URL + relative

    def commit(self):
        """Copy registered images into MEDIA_ROOT and drop this course's unused ones."""
        root = Path(settings.MEDIA_ROOT)
        folder = root / "courses" / self.course
        keep = set()
        for source, relative in self.files.items():
            target = root / relative
            keep.add(target.name)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        if folder.is_dir():
            for stale in folder.iterdir():
                if stale.is_file() and stale.name not in keep:
                    stale.unlink()
