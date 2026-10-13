"""Versions of a course are kept file by file (Blob), not as a zip per step.

Three things in one migration, in this order: the new table and columns, then the steps
that exist are converted, then nothing: the old column stays, now allowed to be empty, so
that a step this could not convert is still read from its zip (desk.handins.kept), and so
that the zips are there as a backup until a later migration drops the column.

A data migration reads rows through `apps.get_model`, which gives the models as they are
at this point in the history, not as models.py has them today. For the same reason the
two small functions it needs are written out here and not imported from the application:
a migration must keep working when that code has moved on.
"""

import hashlib
import io
import zipfile

from django.db import migrations, models

NOTE = ".handin.json"


def course_in(data):
    """The files of the course in a stored zip: {name inside the course: bytes}."""
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            _slug, _slash, name = info.filename.partition("/")
            if not info.is_dir() and name and name != NOTE:
                files[name] = archive.read(info)
    return files


def version_of(files):
    """The fingerprint a step recorded for its course (ingest.handin.version_of)."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name])
        digest.update(b"\0")
    return digest.hexdigest()


def to_blobs(apps, schema_editor):
    HandIn = apps.get_model("desk", "HandIn")
    Blob = apps.get_model("desk", "Blob")
    # iterator(): one zip in memory at a time, not all of them.
    for row in HandIn.objects.exclude(archive=None).iterator(chunk_size=20):
        if row.files:
            continue
        try:
            files = course_in(bytes(row.archive))
        except (zipfile.BadZipFile, OSError, ValueError):
            continue  # left as it is: its zip is still what is read
        # Converted only if it is, to the byte, the version the step says it is.
        if not files or version_of(files) != row.version:
            continue
        names = {}
        for name, data in files.items():
            sha = hashlib.sha256(data).hexdigest()
            Blob.objects.get_or_create(sha=sha, defaults={"data": data, "size": len(data)})
            names[name] = sha
        row.files = names
        row.save(update_fields=["files"])


class Migration(migrations.Migration):
    dependencies = [
        ("desk", "0006_handin_via"),
    ]

    operations = [
        migrations.CreateModel(
            name="Blob",
            fields=[
                (
                    "sha",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("data", models.BinaryField()),
                ("size", models.PositiveIntegerField()),
            ],
        ),
        migrations.AddField(
            model_name="handin",
            name="before",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="handin",
            name="files",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="handin",
            name="trail",
            field=models.JSONField(default=list),
        ),
        migrations.AlterField(
            model_name="handin",
            name="archive",
            field=models.BinaryField(blank=True, null=True),
        ),
        # Going back, there is nothing to undo: the zips were never removed.
        migrations.RunPython(to_blobs, migrations.RunPython.noop),
    ]
