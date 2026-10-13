"""Images added in the editor (editor/images.py): what is kept of an upload, where it
waits, and how it joins the course when the text that shows it is saved."""

import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.utils import timezone
from PIL import Image

from catalog.models import Card, Document
from desk.models import HandIn
from editor import images
from editor.models import Upload
from ingest import live

from .test_editor import DECK, EDIT, C, EditorCase

UPLOAD = f"/edit/image/{C}/"


def picture(size=(640, 480), kind="JPEG", mode="RGB", **options):
    """A picture with something in it (a gradient: it does not compress to nothing)."""
    image = Image.new(mode, size)
    width, height = size
    step = max(1, width // 64)
    for x in range(0, width, step):
        shade = int(255 * x / width)
        colour = (shade, 255 - shade, 128) if mode == "RGB" else (shade, 255 - shade, 128, 200)
        image.paste(colour, (x, 0, min(x + step, width), height))
    buffer = io.BytesIO()
    image.save(buffer, kind, **options)
    return buffer.getvalue()


def photo_from_a_phone():
    """A photo as a phone writes it: lying on its side, with a note to turn it, where it
    was taken, and on which phone."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turn a quarter clockwise to see it upright
    exif[0x010F] = "PhoneMaker"
    exif[0x0110] = "Phone 12"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (43.0, 51.0, 23.0)
    gps[3], gps[4] = "E", (18.0, 24.0, 47.0)
    return picture((800, 600), exif=exif.tobytes())


def opened(data):
    return Image.open(io.BytesIO(data))


class PrepareTests(SimpleTestCase):
    def test_a_photo_comes_out_upright_as_webp_without_what_the_camera_wrote(self):
        sent = photo_from_a_phone()
        before = opened(sent)
        self.assertIn(0x8825, before.getexif())  # it does carry a position
        made, ending, width, height = images.prepare(sent)
        after = opened(made)
        self.assertEqual((ending, after.format), (".webp", "WEBP"))
        self.assertEqual((width, height), (600, 800))  # turned: it was 800 by 600 lying down
        self.assertEqual(dict(after.getexif()), {})
        for trace in (b"PhoneMaker", b"Phone 12", b"Exif", b"GPS"):
            self.assertNotIn(trace, made)
        self.assertLess(len(made), len(sent))

    def test_a_large_image_is_scaled_to_fit(self):
        made, _ending, width, height = images.prepare(picture((4000, 3000)))
        self.assertEqual((width, height), (images.SIDE, 1200))
        self.assertEqual(opened(made).size, (images.SIDE, 1200))
        _made, _ending, width, height = images.prepare(picture((300, 200)))
        self.assertEqual((width, height), (300, 200))  # a small one is not blown up

    def test_a_drawing_is_kept_exactly_and_a_photo_is_not(self):
        flat = Image.new("RGB", (800, 600), "white")
        flat.paste("black", (100, 100, 700, 110))
        buffer = io.BytesIO()
        flat.save(buffer, "PNG")
        drawing, *_ = images.prepare(buffer.getvalue())
        self.assertIn(b"VP8L", drawing[:16])  # the lossless kind of WebP
        self.assertEqual(opened(drawing).convert("RGB").getpixel((400, 105)), (0, 0, 0))
        noisy = Image.effect_noise((800, 600), 80).convert("RGB")
        buffer = io.BytesIO()
        noisy.save(buffer, "JPEG", quality=95)
        photo, *_ = images.prepare(buffer.getvalue())
        self.assertIn(b"VP8 ", photo[:16])  # the kind for photos

    def test_what_is_see_through_stays_so(self):
        made, *_ = images.prepare(picture((200, 100), "PNG", "RGBA"))
        self.assertEqual(opened(made).mode, "RGBA")

    def test_what_is_not_a_picture_is_refused_and_says_why(self):
        animation = io.BytesIO()
        frames = [Image.new("RGB", (20, 20), colour) for colour in ("red", "green", "blue")]
        frames[0].save(animation, "GIF", save_all=True, append_images=frames[1:])
        huge = io.BytesIO()
        Image.new("1", (7000, 6000)).save(huge, "PNG")  # 42 million pixels in a few kilobytes
        cases = [
            (b"just some text", "not an image"),
            (b"%PDF-1.7 a page of slides", "not an image"),
            (picture()[:200], "not an image"),
            (animation.getvalue(), "animation"),
            (huge.getvalue(), "pixels"),
            (b"\0" * (images.MAX_UPLOAD + 1), "8 MB"),
        ]
        for data, word in cases:
            with self.subTest(word=word, size=len(data)), self.assertRaises(images.Unusable) as no:
                images.prepare(data)
            self.assertIn(word, str(no.exception))

    def test_an_svg_drawing_is_kept_as_it_is_unless_it_can_act(self):
        drawing = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            b'<path d="M0 0L10 10"/></svg>'
        )
        self.assertEqual(images.prepare(drawing), (drawing, ".svg", 0, 0))
        self.assertEqual(images.prepare(b'<?xml version="1.0"?>\n' + drawing)[1], ".svg")
        for bad in (
            drawing.replace(b"<path", b"<script>alert(1)</script><path"),
            drawing.replace(b"<path", b'<path onclick="x()"'),
            b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]>' + drawing,
            b"<svg " + b"x" * images.MAX_SVG + b"</svg>",
        ):
            with self.subTest(bad=bad[:40]), self.assertRaises(images.Unusable):
                images.prepare(bad)


class UploadTests(EditorCase):
    def send(self, data, client=None, name="photo.jpg"):
        sent = SimpleUploadedFile(name, data, "image/jpeg")
        return (client or self.client).post(UPLOAD, {"image": sent})

    def test_an_upload_is_kept_aside_and_answers_with_the_line_that_shows_it(self):
        answer = self.send(photo_from_a_phone())
        self.assertEqual(answer.status_code, 200)
        kept = Upload.objects.get()
        told = answer.json()
        self.assertEqual(told["path"], f"assets/{kept.name}")
        self.assertRegex(kept.name, r"^[0-9a-f]{16}\.webp$")
        self.assertEqual(
            (told["width"], told["height"], kept.user, kept.slug), (600, 800, self.ada, C)
        )
        # It is not in the course: nothing was saved.
        self.assertFalse(HandIn.objects.exists())
        # The form that asked can show it; nobody else can.
        shown = self.client.get(told["url"])
        self.assertEqual((shown.status_code, shown["Content-Type"]), (200, "image/webp"))
        self.assertEqual(shown.content, bytes(kept.data))
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(told["url"]).status_code, 404)

    def test_the_same_image_twice_is_one_image(self):
        first = self.send(picture()).json()
        again = self.send(picture()).json()
        self.assertEqual(first["name"], again["name"])
        self.assertEqual(Upload.objects.count(), 1)

    def test_what_cannot_be_taken_is_said_and_nothing_is_kept(self):
        answer = self.send(b"not a picture at all")
        self.assertEqual(answer.status_code, 400)
        self.assertIn("not an image", answer.json()["error"])
        self.assertEqual(self.client.post(UPLOAD).status_code, 400)
        self.assertEqual(self.client.get(UPLOAD).status_code, 405)
        self.assertFalse(Upload.objects.exists())

    def test_only_those_who_write_the_course_can_upload(self):
        self.client.force_login(self.eve)
        self.assertEqual(self.send(picture()).status_code, 404)
        self.assertFalse(Upload.objects.exists())
        self.client.logout()
        self.assertEqual(self.send(picture()).status_code, 302)

    def test_an_upload_nothing_came_to_use_is_forgotten(self):
        self.send(picture((100, 100)))
        Upload.objects.update(added_at=timezone.now() - images.KEPT - timedelta(hours=1))
        self.send(picture((120, 120)))
        self.assertEqual(Upload.objects.count(), 1)

    def test_more_than_the_page_accepts_is_refused_before_it_is_read(self):
        big = SimpleUploadedFile("big.jpg", b"\0" * (10 * 1024 * 1024), "image/jpeg")
        self.assertEqual(self.client.post(UPLOAD, {"image": big}).status_code, 413)


class InTheCourseTests(EditorCase):
    GUIDE = f"{EDIT}guides/choosing-a-method/"

    def setUp(self):
        super().setUp()
        sent = SimpleUploadedFile("p.jpg", photo_from_a_phone(), "image/jpeg")
        self.kept = self.client.post(UPLOAD, {"image": sent}).json()
        self.line = f"![The fit, on a whiteboard]({self.kept['path']})"

    def test_the_preview_shows_it_before_it_is_saved(self):
        shown = self.client.post(
            f"{EDIT}preview/",
            {"file": "content/guides/choosing-a-method.md", "body": f"Look:\n\n{self.line}\n"},
        ).json()
        self.assertEqual(shown["problems"], [])
        self.assertIn(f'src="{self.kept["url"]}"', shown["html"])
        self.assertIn('alt="The fit, on a whiteboard"', shown["html"])
        # An image nobody uploaded is still an error.
        shown = self.client.post(
            f"{EDIT}preview/",
            {"file": "content/guides/choosing-a-method.md", "body": "![x](assets/nope.webp)\n"},
        ).json()
        self.assertIn("image not found", shown["problems"][0]["message"])

    def test_saving_the_text_takes_the_image_into_the_course_beside_the_file(self):
        data = self.form_of(self.GUIDE)
        data["body"] += f"\r\n\r\n{self.line}\r\n"
        self.assertEqual(self.client.post(self.GUIDE, data).status_code, 302)
        where = f"content/guides/assets/{self.kept['name']}"
        stored = (live.folder(C) / where).read_bytes()
        self.assertEqual(stored, bytes(Upload.objects.get().data))
        step = HandIn.objects.get()
        self.assertEqual(step.new_files, [where])
        doc = Document.objects.get(course__slug=C, slug="choosing-a-method")
        self.assertRegex(doc.body_html, r'<img src="/media/courses/example/[0-9a-f]{16}\.webp"')
        self.assertFalse((self.course / where).exists())  # the owner's files: untouched
        # Saved again with another change, the image is there already and is not added twice.
        data = self.form_of(self.GUIDE) | {"summary": "With a picture now."}
        self.assertEqual(self.client.post(self.GUIDE, data).status_code, 302)
        self.assertEqual(HandIn.objects.get().new_files, [where])

    def test_a_card_can_show_it_too(self):
        url = f"{EDIT}cards/least-squares/lsq-loss/"
        page = self.client.get(url)
        fields = {f.node.key: f for f in page.context["sections"][0]["fields"]}
        data = {
            "shape": "basic",
            "q": fields["q"].value,
            "a": fields["a"].value + f"\n\n{self.line}",
            "hy": "yes",
            "seen": page.context["seen"],
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertTrue((live.folder(C) / f"content/decks/assets/{self.kept['name']}").is_file())
        self.assertIn("<img", Card.objects.get(key="lsq-loss").back_html)
        self.assertIn(self.kept["path"], (live.folder(C) / DECK).read_text("utf-8"))

    def test_an_image_kept_for_another_course_is_not_taken(self):
        Upload.objects.update(slug="another-course")
        data = self.form_of(self.GUIDE)
        data["body"] += f"\n\n{self.line}\n"
        answer = self.client.post(self.GUIDE, data)
        self.assertContains(answer, "image not found")
        self.assertFalse(HandIn.objects.exists())

    def test_the_fields_of_a_form_preview_it_as_well(self):
        answer = self.client.post(
            f"{EDIT}preview/fields/",
            {"file": DECK, "fields": [{"name": "a", "label": "Answer", "text": self.line}]},
            content_type="application/json",
        )
        (part,) = answer.json()["fields"]
        self.assertEqual(part["problems"], [])
        self.assertIn(self.kept["url"], part["html"])
