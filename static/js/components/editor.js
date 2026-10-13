// The editor's form (editor/templates/editor/page.html and file.html).
//
// The form itself is plain HTML that works by being submitted. This adds three things:
// buttons that write Markdown and LaTeX into the text box, a preview that the server
// renders (with the code that publishes a page, so it cannot differ from the result),
// and a warning before leaving with something unsaved.

// What each button of the toolbar writes. `wrap` goes around the selection, `lines` in
// front of every selected line, `block` on lines of its own. `sample` is what stands in
// for a selection when nothing is selected, and is left selected to be typed over.
const TOOLS = {
  bold: { wrap: ["**", "**"], sample: "bold" },
  italic: { wrap: ["*", "*"], sample: "italic" },
  code: { wrap: ["`", "`"], sample: "code" },
  link: { wrap: ["[", "](address)"], sample: "text" },
  math: { wrap: ["$", "$"], sample: "x^2" },
  frac: { wrap: ["$\\frac{", "}{b}$"], sample: "a" },
  h2: { lines: () => "## " },
  h3: { lines: () => "### " },
  list: { lines: () => "- " },
  numbered: { lines: (index) => `${index + 1}. ` },
  display: { block: ["$$", "$$"], sample: "a = b" },
  aligned: { block: ["$$\\begin{aligned}", "\\end{aligned}$$"], sample: "a &= b \\\\\n  &= c" },
  fence: { block: ["```python", "```"], sample: "print(1)" },
  table: {
    block: ["", ""],
    sample: "| Column | Column |\n|---|---|\n| cell | cell |\n| cell | cell |",
  },
  blank: { wrap: ["{{", "}}"], sample: "word" },
};

const PAUSE = 450; // ms without typing before the preview is asked for

/** "Fisher's criterion" -> "fisher-s-criterion": an address made from a title. */
function slug(text) {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 56)
    .replace(/-+$/, "");
}

export default function editor() {
  return {
    pane: "write",
    title: "",
    summary: "",
    html: "",
    points: [],
    shown: [], // a form about a YAML file: each of its texts, rendered
    last: null, // there, the text box the cursor was in last
    uploading: false,
    issues: [], // what the server's renderer objected to
    broken: [], // formulas the browser's KaTeX could not typeset
    pending: false,
    failed: false,
    dirty: false,
    leaving: false,
    named: false, // the address of a new page was typed by hand: stop suggesting one
    timer: null,
    asked: 0,

    init() {
      this.read();
      if (this.previews) this.refresh();
      window.addEventListener("beforeunload", (event) => {
        if (!this.dirty || this.leaving) return;
        // Both lines mean "ask before leaving"; browsers differ in which they heed.
        event.preventDefault();
        event.returnValue = "";
      });
    },

    // Looked up in the page each time, not through x-ref: init() runs before Alpine has
    // reached the elements inside this one.
    get form() {
      return this.$root.querySelector("form");
    },
    /** The box the text is typed into: the one there is, or, in a form with several,
        the one the cursor was in last. */
    get box() {
      if (this.last?.isConnected) return this.last;
      return this.form.querySelector("textarea[name=body], textarea[name=text], textarea[data-md]");
    },
    get previews() {
      return Boolean(this.$root.dataset.preview || this.$root.dataset.fields);
    },
    get problems() {
      return [...this.issues, ...this.broken];
    },

    field(name) {
      return this.form.elements[name];
    },

    /** Copy what the preview shows as plain text straight from the form. */
    read() {
      this.title = this.field("title")?.value.trim() ?? "";
      this.summary = this.field("summary")?.value.trim() ?? "";
    },

    changed(event) {
      this.dirty = true;
      this.read();
      this.suggest(event);
      if (!this.previews) return;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.refresh(), PAUSE);
    },

    /** A new page's address follows its title and number until someone types one. */
    suggest(event) {
      const name = this.field("name");
      if (!name || !this.$root.dataset.new) return;
      if (event?.target === name) {
        this.named = name.value !== "";
        return;
      }
      if (this.named) return;
      const stem = slug(this.title);
      const number = this.field("number")?.value;
      name.value = stem && number ? `${String(number).padStart(2, "0")}-${stem}` : stem;
    },

    focused(event) {
      if (event.target.matches?.("textarea[data-md]")) this.last = event.target;
    },

    /** Go to a field of the form by its name, and show it. */
    show(name) {
      const field = this.form.elements[name];
      this.pane = "write";
      this.$nextTick(() => {
        field?.scrollIntoView({ block: "center" });
        field?.focus();
      });
    },

    /** A form about a YAML file: every text of it that is on show, to be rendered. */
    async refreshFields() {
      const fields = [...this.form.querySelectorAll("textarea[data-md]")]
        // A field the form is hiding (the choices of a question that has none) is not sent.
        .filter((box) => box.offsetParent !== null && box.value.trim() !== "")
        .map((box) => ({
          name: box.name,
          label: box.dataset.label,
          text: box.value,
          cloze: box.dataset.md === "cloze",
        }));
      const mine = ++this.asked;
      this.pending = true;
      try {
        const response = await fetch(this.$root.dataset.fields, {
          method: "POST",
          body: JSON.stringify({ file: this.$root.dataset.file, fields }),
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.documentElement.dataset.csrf,
          },
        });
        if (!response.ok) throw new Error(response.status);
        const answer = await response.json();
        if (mine !== this.asked) return;
        this.shown = answer.fields;
        this.issues = answer.fields.flatMap((part) => part.problems);
        this.failed = false;
        this.$nextTick(() => this.maths());
      } catch {
        if (mine === this.asked) this.failed = true;
      } finally {
        if (mine === this.asked) this.pending = false;
      }
    },

    async refresh() {
      if (this.$root.dataset.fields) return this.refreshFields();
      const data = new FormData();
      data.set("file", this.$root.dataset.file);
      data.set("body", this.box.value);
      for (const input of this.form.querySelectorAll("[name=takeaways]")) {
        data.append("takeaways", input.value);
      }
      const mine = ++this.asked;
      this.pending = true;
      try {
        const response = await fetch(this.$root.dataset.preview, {
          method: "POST",
          body: data,
          // Django refuses a POST without the token it gave this page (CSRF protection).
          headers: { "X-CSRFToken": document.documentElement.dataset.csrf },
        });
        if (!response.ok) throw new Error(response.status);
        const shown = await response.json();
        if (mine !== this.asked) return; // a newer answer is on its way
        this.html = shown.html;
        this.points = shown.takeaways;
        this.issues = shown.problems;
        this.failed = false;
        // KaTeX typesets once the HTML is in the page; what it cannot read, it marks.
        this.$nextTick(() => this.maths());
      } catch {
        if (mine === this.asked) this.failed = true;
      } finally {
        if (mine === this.asked) this.pending = false;
      }
    },

    /** List the formulas KaTeX could not typeset beside the server's own objections. */
    maths() {
      const marked = this.$root.querySelectorAll(".editor__paper .math[data-error]");
      this.broken = [...marked].map((node) => ({
        level: "error",
        line: null,
        where: "",
        message: "Maths that cannot be typeset",
        hint: node.title, // KaTeX's own words, which quote the place (static/js/math.js)
      }));
    },

    save() {
      this.leaving = true;
      this.form.requestSubmit();
    },

    /** Ctrl+S saves; in the text box Ctrl+B and Ctrl+I do what they do everywhere. */
    keys(event) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        this.save();
      } else if (this.previews && event.target.matches?.("textarea") && (key === "b" || key === "i")) {
        event.preventDefault();
        this.tool(key === "b" ? "bold" : "italic");
      }
    },

    /** Put the cursor on a line of the text, and show it. */
    goto(line) {
      const box = this.box;
      const lines = box.value.split("\n");
      const start = lines.slice(0, line - 1).join("\n").length + (line > 1 ? 1 : 0);
      this.pane = "write";
      this.$nextTick(() => {
        box.focus();
        box.setSelectionRange(start, start + (lines[line - 1] ?? "").length);
        const height = parseFloat(getComputedStyle(box).lineHeight) || 20;
        box.scrollTop = Math.max(0, (line - 4) * height);
      });
    },

    // ---- images ------------------------------------------------------------------------

    /** Send an image to the site, which makes it fit for a course and keeps it aside
        (editor/images.py), and write the line of Markdown that shows it. It becomes part
        of the course when the text with that line is saved. */
    async place(file) {
      if (!file || !this.$root.dataset.upload) return;
      const data = new FormData();
      data.set("image", file);
      this.uploading = true;
      try {
        const response = await fetch(this.$root.dataset.upload, {
          method: "POST",
          body: data,
          headers: { "X-CSRFToken": document.documentElement.dataset.csrf },
        });
        const answer = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(
            answer.error || (response.status === 413 ? "That image is too large." : "The image could not be taken."),
          );
        }
        const box = this.box;
        const { selectionStart: start, selectionEnd: end, value } = box;
        const before = value.slice(0, start);
        const gap = before === "" || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
        // What the picture shows is read out by screen readers and shown if it fails to
        // load: left selected, to be typed over.
        const what = "Describe the picture";
        this.put(start, end, `${gap}![${what}](${answer.path})\n`, gap.length + 2, gap.length + 2 + what.length);
      } catch (error) {
        this.$store.ui.say(error.message, 6000);
      } finally {
        this.uploading = false;
      }
    },
    picked(event) {
      this.place(event.target.files[0]);
      event.target.value = ""; // so that choosing the same file again is a change
    },
    /** An image among what was pasted or dropped on a text box, or nothing. */
    pictured(event, files) {
      if (!event.target.matches?.("textarea[data-md], textarea[name=body]")) return null;
      return [...(files ?? [])].find((file) => file.type.startsWith("image/")) ?? null;
    },
    pasted(event) {
      const file = this.pictured(event, event.clipboardData?.files);
      if (!file) return; // text: pasted as always
      event.preventDefault();
      this.last = event.target;
      this.place(file);
    },
    dragging(event) {
      // A browser only lets something be dropped where dragging over was accepted.
      if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
    },
    dropped(event) {
      const file = this.pictured(event, event.dataTransfer?.files);
      if (!file) return;
      event.preventDefault();
      this.last = event.target;
      event.target.focus();
      this.place(file);
    },

    // ---- the toolbar -------------------------------------------------------------------

    tool(name) {
      const { wrap, lines, block, sample } = TOOLS[name];
      if (wrap) this.wrap(wrap[0], wrap[1], sample);
      else if (lines) this.lines(lines);
      else this.block(block[0], block[1], sample);
    },

    callout(event) {
      const kind = event.target.value;
      event.target.value = "";
      if (kind) this.block(`:::${kind}`, ":::", "Text");
    },

    /** Replace [start, end) of the text and select part of what was put there. */
    put(start, end, text, from, to) {
      const box = this.box;
      box.focus();
      box.setRangeText(text, start, end, "end");
      box.setSelectionRange(start + from, start + to);
      // Tell the form, as typing would: the preview follows, and the page is unsaved.
      box.dispatchEvent(new Event("input", { bubbles: true }));
    },

    wrap(before, after, sample) {
      const { selectionStart: start, selectionEnd: end, value } = this.box;
      const chosen = value.slice(start, end) || sample;
      this.put(start, end, before + chosen + after, before.length, before.length + chosen.length);
    },

    lines(prefix) {
      const { selectionStart, selectionEnd, value } = this.box;
      const start = value.lastIndexOf("\n", selectionStart - 1) + 1;
      const stop = value.indexOf("\n", selectionEnd);
      const end = stop < 0 ? value.length : stop;
      const text = value
        .slice(start, end)
        .split("\n")
        .map((line, index) => prefix(index) + line)
        .join("\n");
      this.put(start, end, text, 0, text.length);
    },

    block(open, close, sample) {
      const { selectionStart: start, selectionEnd: end, value } = this.box;
      const chosen = value.slice(start, end) || sample;
      const before = value.slice(0, start);
      // A block stands apart from what is around it by an empty line.
      const gap = before === "" || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
      const head = gap + (open ? `${open}\n` : "");
      const tail = (close ? `\n${close}` : "") + (value.slice(end).startsWith("\n") ? "" : "\n");
      this.put(start, end, head + chosen + tail, head.length, head.length + chosen.length);
    },
  };
}

/** A list of one-line boxes (editor/templates/editor/widgets/rows.html). */
export function rows() {
  return {
    add() {
      this.$refs.list.append(this.$refs.blank.content.cloneNode(true));
      this.$refs.list.lastElementChild.querySelector("input").focus();
    },
    remove(event) {
      const row = event.target.closest(".rows__row");
      const form = row.closest("form");
      row.remove();
      form?.dispatchEvent(new Event("input", { bubbles: true }));
    },
  };
}

/** The entries of a list in a form made from a schema (editor/templates/editor/nodes.html).
    `next` is the number the next new entry gets, `mark` what stands for that number in
    the names of the blank entry. Entries are read by the server in the order they stand
    on the page, so moving one is moving its element. */
export function repeat(next, mark) {
  return {
    next,
    add() {
      const html = this.$refs.blank.innerHTML.replaceAll(mark, String(this.next++));
      this.$refs.rows.insertAdjacentHTML("beforeend", html);
      const row = this.$refs.rows.lastElementChild;
      row.querySelector("input:not([type=checkbox]), textarea, select")?.focus();
      this.told(row);
    },
    remove(event) {
      const row = event.target.closest(".repeat__row");
      const form = row.closest("form");
      row.remove();
      this.told(form);
    },
    move(event, by) {
      const row = event.target.closest(".repeat__row");
      const other = by < 0 ? row.previousElementSibling : row.nextElementSibling;
      if (!other) return;
      if (by < 0) other.before(row);
      else other.after(row);
      this.told(row);
    },
    /** Tell the form that it changed, as typing would. */
    told(from) {
      from?.dispatchEvent(new Event("input", { bubbles: true }));
    },
  };
}
