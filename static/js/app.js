// Entry point. No build step: the browser loads these files as ES modules.
//
// Django renders every page as complete HTML. Alpine then adds behaviour to the elements
// that carry x-data. Two "stores" hold state shared across components on a page:
//   $store.progress   grades, read marks, attempts, activity (saved in the browser)
//   $store.timer      the focus timer

import Alpine from "../vendor/alpine/alpine.esm.min.js";

import bell from "./components/bell.js";
import cover from "./components/cover.js";
import editor, { rows } from "./components/editor.js";
import flashcards from "./components/flashcards.js";
import news from "./components/news.js";
import nextUp from "./components/nextup.js";
import notice from "./components/notice.js";
import palaceWalk from "./components/palace.js";
import paper from "./components/paper.js";
import settingsPanel from "./components/settings.js";
import shell from "./components/shell.js";
import tour from "./components/tour.js";
import * as widgets from "./components/widgets.js";
import { enhance, mathDirective } from "./math.js";
import { createProgress } from "./store/progress.js";
import { createTimer } from "./store/timer.js";

window.Alpine = Alpine;

Alpine.store("progress", createProgress());
Alpine.store("timer", createTimer(Alpine.store("progress")));
Alpine.store("ui", {
  toast: "",
  say(message, ms = 2600) {
    this.toast = message;
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => (this.toast = ""), ms);
  },
});

mathDirective(Alpine);

Alpine.data("shell", shell);
Alpine.data("nextUp", nextUp);
Alpine.data("flashcards", flashcards);
Alpine.data("paper", paper);
Alpine.data("palaceWalk", palaceWalk);
Alpine.data("cover", cover);
Alpine.data("settingsPanel", settingsPanel);
Alpine.data("tour", tour);
Alpine.data("news", news);
Alpine.data("notice", notice);
Alpine.data("bell", bell);
Alpine.data("editor", editor);
Alpine.data("rows", rows);
for (const [name, component] of Object.entries(widgets)) Alpine.data(name, component);

// Typeset the maths that arrived in the page's HTML, then start Alpine.
enhance(document);
Alpine.start();
