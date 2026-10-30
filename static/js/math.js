// Finishes what the server started in rendered Markdown.
//
// The server leaves LaTeX as text inside <span class="math">; KaTeX typesets it here.
// It also adds the icon to each callout title. Both run on page load and again on any
// fragment a component inserts later (a revealed answer, the next card).

const CALLOUT_ICONS = {
  note: "info",
  tip: "tip",
  warning: "warning",
  pitfall: "pitfall",
  definition: "definition",
  theorem: "theorem",
  example: "example",
  paper: "paper",
  proof: "proof",
  recall: "help",
  solution: "check",
};

export function typeset(root = document) {
  const katex = window.katex;
  if (!katex) return;
  for (const node of root.querySelectorAll(".math:not([data-done])")) {
    const tex = node.textContent;
    try {
      katex.render(tex, node, {
        displayMode: node.classList.contains("block"),
        throwOnError: true,
        strict: "ignore",
      });
    } catch (error) {
      // sync_content verifies every formula, so this is rare; show the source, not nothing.
      node.dataset.error = "1";
      node.title = String(error.message || error);
    }
    node.dataset.done = "1";
  }
}

export function decorate(root = document) {
  const sprite = document.documentElement.dataset.icons;
  for (const title of root.querySelectorAll(".callout__title:not([data-icon])")) {
    const kind = [...title.parentElement.classList]
      .find((name) => name.startsWith("callout--"))
      ?.slice("callout--".length);
    const icon = CALLOUT_ICONS[kind];
    if (icon) {
      title.insertAdjacentHTML(
        "afterbegin",
        `<svg class="icon" aria-hidden="true"><use href="${sprite}#${icon}"></use></svg>`,
      );
    }
    title.dataset.icon = "1";
  }
}

// KaTeX breaks an inline formula only after a relation or an operator at its top level.
// One with nowhere to break, a product of matrices say, can be wider than a phone's line
// and would push the page sideways. Such a formula gets the class math--wide, which makes
// it a box of its own that scrolls sideways (prose.css). It is checked whenever the block
// it stands in changes size: when the window does, when fonts arrive, and when a hidden
// answer is shown. (A ResizeObserver sees an inline element as 0 by 0, so it watches the
// block, not the formula.)
const BLOCK = "p, li, td, th, dd, summary, h1, h2, h3, h4, div";
const fits = new ResizeObserver((entries) => {
  for (const entry of entries) {
    for (const node of entry.target.querySelectorAll(".math.inline[data-done]")) fit(node);
  }
});

// KaTeX's fonts can arrive after the first look and widen a formula without changing the
// size of its block, so look once more when they have.
document.fonts?.ready.then(() => {
  for (const node of document.querySelectorAll(".math.inline[data-done]")) fit(node);
});

function fit(node) {
  if (node.classList.contains("math--wide")) {
    if (node.scrollWidth <= node.clientWidth + 1) node.classList.remove("math--wide");
    return;
  }
  const block = node.closest(BLOCK);
  if (!block) return;
  const edge = block.getBoundingClientRect().right - parseFloat(getComputedStyle(block).paddingRight);
  if (node.getBoundingClientRect().right > edge + 1) node.classList.add("math--wide");
}

export function enhance(root = document) {
  typeset(root);
  decorate(root);
  for (const node of root.querySelectorAll(".math.inline[data-done]")) {
    const block = node.closest(BLOCK);
    if (block && !block.dataset.fit) {
      block.dataset.fit = "1";
      fits.observe(block);
    }
  }
}

/** x-math="expression": like x-html, then typesets what it inserted. */
export function mathDirective(Alpine) {
  Alpine.directive("math", (el, { expression }, { effect, evaluateLater }) => {
    const read = evaluateLater(expression);
    effect(() => {
      read((html) => {
        el.innerHTML = html || "";
        enhance(el);
      });
    });
  });
}
