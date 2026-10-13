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
    /** The box the text is typed into. */
    get box() {
      return this.form.querySelector("textarea[name=body], textarea[name=text]");
    },
    get previews() {
      return Boolean(this.$root.dataset.preview);
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

    async refresh() {
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
      } else if (this.previews && event.target === this.box && (key === "b" || key === "i")) {
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
