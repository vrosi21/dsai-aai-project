"""Build the educator kit: what the people who write courses work from.

    manage.py export_authoring educator-kit

The kit is a repository of its own that every educator clones and keeps up to date with
`git pull`. It holds no course content. It carries a copy of everything needed to write
content and to check it without this application:

    README.md  AGENTS.md  CLAUDE.md                 from ingest/educator_kit/
    .gitignore  .gitattributes                      (kept there without the dot)
    .claude/skills/                                 the same instructions as a command
    courses/README.md                               where an educator's own course goes;
                                                    the rest of that folder is ignored
    docs/                                           the authoring guide, the field reference,
                                                    the schemas and the example course
    tools/unpack.py  tools/check.py  tools/pack.py  the three tools an educator runs
    VERSION  CHANGELOG.md                           which version it is (desk/kit.py)
    tools/rules/                                    the part of the importer they run: the
                                                    modules of ingest/ that need no Django

All of it is generated: the originals are here, where they are tested with the code they
describe. Run this again after changing the authoring docs, a schema or the importer, then
commit and push the result in the kit's repository. An educator's `courses/` folder, should
the target have one, is never touched.
"""

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from desk import kit

KIT = Path(__file__).resolve().parents[2] / "educator_kit"
INGEST = KIT.parent
DOCS = Path(settings.BASE_DIR) / "docs"

# The importer's first half (finding files, reading YAML, checking fields) and the shape
# of a hand-in. None of these modules imports Django, which is what lets them run in a
# repository that has only Python.
RULES = ["discover.py", "handin.py", "load.py", "report.py", "schemas.py", "yamlio.py"]
# Kit file -> its place in the kit's repository.
FILES = {
    "README.md": "README.md",
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "gitignore": ".gitignore",
    "gitattributes": ".gitattributes",
    "courses/README.md": "courses/README.md",
    "skills/update-cms/SKILL.md": ".claude/skills/update-cms/SKILL.md",
    "tools/unpack.py": "tools/unpack.py",
    "tools/check.py": "tools/check.py",
    "tools/pack.py": "tools/pack.py",
}
GUIDE = ["AUTHORING.md", "content-types", "schemas", "example-course"]

NOTE = """# How content is written

These files are copies. The originals are kept with the site's own code, where they are
tested together with what they describe. Do not edit them here: `git pull` brings the
next version.

- [AUTHORING.md](AUTHORING.md): the guide. Read it first.
- [content-types/](content-types/): one page per kind of file, and
  [fields.md](content-types/fields.md), every field of every file.
- [schemas/](schemas/): the same format as JSON Schema, for editors and tools.
- [example-course/example/](example-course/example/): a complete, valid course.

The guide was written for the site's own repository. In this kit a course is in
`courses/<course>/`, and the commands are `tools/check.py` and `tools/pack.py` (see the
README at the top). A few links in the guide lead to pages about running the site
(DEPLOYMENT.md, ARCHITECTURE.md); those are not part of the kit, and nothing in them is
needed to write content.
"""

HEADER = "# Copied from the site's code by `manage.py export_authoring`. Do not edit here.\n"


def copy(source, target):
    """Replace `target` with a copy of `source` (a file or a folder)."""
    if target.is_dir():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        # Never raw material, a course's duties (ingest/duties.py) or the owner's private
        # material (ingest/private.py): all three are the owner's, and the kit is public.
        ignore = shutil.ignore_patterns("__pycache__", "source", "duties.yaml", "private")
        shutil.copytree(source, target, ignore=ignore)
    else:
        shutil.copyfile(source, target)


class Command(BaseCommand):
    help = "Write the educator kit (instructions, docs, checker) into its repository folder."

    def add_arguments(self, parser):
        parser.add_argument("target", help="The kit's folder, e.g. educator-kit.")

    def handle(self, *args, **options):
        target = Path(options["target"]).resolve()
        if not target.is_dir():
            raise CommandError(f"{target} is not a folder.")
        if target in (Path(settings.BASE_DIR).resolve(), Path(settings.COURSES_DIR).resolve()):
            raise CommandError(
                "That is the application or its courses folder. Name the kit's own folder."
            )

        for name, published in FILES.items():
            copy(KIT / name, target / published)
        for name in GUIDE:
            copy(DOCS / name, target / "docs" / name)
        (target / "docs" / "README.md").write_text(NOTE, encoding="utf-8", newline="\n")
        # Which version this is and what changed: the list is kept in desk/kit.py.
        (target / "VERSION").write_text(f"{kit.LATEST}\n", encoding="utf-8", newline="\n")
        (target / "CHANGELOG.md").write_text(kit.changelog(), encoding="utf-8", newline="\n")

        rules = target / "tools" / "rules"
        if rules.is_dir():
            shutil.rmtree(rules)
        rules.mkdir(parents=True)
        (rules / "__init__.py").write_text(HEADER, encoding="utf-8", newline="\n")
        for name in RULES:
            text = (INGEST / name).read_text(encoding="utf-8")
            (rules / name).write_text(HEADER + text, encoding="utf-8", newline="\n")

        self.stdout.write(self.style.SUCCESS(f"Wrote the educator kit to {target}"))
        self.stdout.write("Commit and push it there; educators get it with `git pull`.")
