"""The files of every version of a course, each stored once.

A step in a course's history keeps the whole course as it stood (desk/handins.py). Stored
as a zip per step, one corrected line in a lecture would store every file of the course
again, images included. So the bytes are kept apart from the steps, the way git does it:

    Blob     sha256 of some bytes -> those bytes, once
    a step   {file name: sha256}

A file that did not change between two versions is one row here, however many steps
name it, and a version of a file that some save replaced is still here to be fetched.
Nothing is ever deleted from this table: it is the history.
"""

import hashlib

from .models import Blob

BATCH = 400  # shas per query: well below what SQLite accepts as parameters


def sha_of(data):
    return hashlib.sha256(data).hexdigest()


def _batches(items):
    items = list(items)
    for start in range(0, len(items), BATCH):
        yield items[start : start + BATCH]


def put(files):
    """Store a version ({name: bytes}); returns it as {name: sha}. Bytes that are here
    already are not written again."""
    shas = {name: sha_of(data) for name, data in files.items()}
    known = set()
    for batch in _batches(set(shas.values())):
        known.update(Blob.objects.filter(sha__in=batch).values_list("sha", flat=True))
    new = {}
    for name, sha in shas.items():
        if sha not in known:
            new[sha] = files[name]
    # ignore_conflicts: two people saving the same new bytes at once is not an error.
    Blob.objects.bulk_create(
        [Blob(sha=sha, data=data, size=len(data)) for sha, data in new.items()],
        batch_size=50,
        ignore_conflicts=True,
    )
    return shas


def get(mapping):
    """A version back from its {name: sha}: {name: bytes}. Raises LookupError if a file
    of it is not in the store, which would mean the store was tampered with."""
    found = {}
    for batch in _batches(set(mapping.values())):
        for sha, data in Blob.objects.filter(sha__in=batch).values_list("sha", "data"):
            found[sha] = bytes(data)
    missing = sorted(name for name, sha in mapping.items() if sha not in found)
    if missing:
        raise LookupError(f"not in the store: {', '.join(missing[:5])}")
    return {name: found[sha] for name, sha in mapping.items()}


def one(sha):
    """The bytes stored under `sha`, or None."""
    row = Blob.objects.filter(sha=sha).values_list("data", flat=True).first()
    return bytes(row) if row is not None else None
