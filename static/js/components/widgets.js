// Small read-outs of progress used across pages. Each export becomes an Alpine component
// of the same name (see app.js), used in a template as x-data="name" or x-data="name(arg)".

import { COURSE, DAY, manifest, manifests, percent, plural } from "../lib/util.js";

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

function cardIds(courseManifest, keep = () => true) {
  return courseManifest.cards.filter(keep).map((card) => card.id);
}

function share(stats) {
  return (kind) => (stats.total ? (100 * stats[kind]) / stats.total : 0);
}

/** Progress ring in the top bar: this course's cards by grade. */
export const ring = () => ({
  circumference: 2 * Math.PI * 15,
  ids: [],
  init() {
    this.ids = cardIds(manifest());
  },
  get stats() {
    return this.$store.progress.stats(COURSE, this.ids);
  },
  get percent() {
    return percent(this.stats.solid, this.stats.total);
  },
  get title() {
    const s = this.stats;
    return `${s.solid} solid, ${s.shaky} shaky, ${s.missed} missed, ${s.unseen} unseen`;
  },
  arc(kind) {
    const length = this.stats.total ? (this.circumference * this.stats[kind]) / this.stats.total : 0;
    return `${length} ${this.circumference - length}`;
  },
  offset(kind) {
    const s = this.stats;
    const before = { solid: 0, shaky: s.solid, missed: s.solid + s.shaky }[kind];
    return s.total ? (-this.circumference * before) / s.total : 0;
  },
});

/** A course on the dashboard. */
export const courseCard = (slug) => ({
  ids: [],
  init() {
    const found = manifests().find((m) => m.slug === slug);
    this.ids = found ? cardIds(found) : [];
  },
  get stats() {
    return this.$store.progress.stats(slug, this.ids);
  },
  get due() {
    return this.stats.due;
  },
  pct(kind) {
    return share(this.stats)(kind);
  },
  get label() {
    const s = this.stats;
    if (!s.total) return "";
    if (s.unseen === s.total) return "Not started";
    return `${percent(s.solid, s.total)}% solid, ${s.unseen} unseen`;
  },
});

/** One deck's bar in a list. */
export const deckBar = (deck) => ({
  ids: [],
  init() {
    this.ids = cardIds(manifest(), (card) => card.deck === deck);
  },
  get stats() {
    return this.$store.progress.stats(COURSE, this.ids);
  },
  pct(kind) {
    return share(this.stats)(kind);
  },
  get label() {
    const s = this.stats;
    return `${s.solid} solid, ${s.shaky} shaky, ${s.missed} missed, ${s.unseen} unseen`;
  },
});

/** Cards matching a smart filter (due, weak, unseen, key), optionally within topics. */
export const smartDeck = (filter, topics = null) => ({
  get count() {
    const progress = this.$store.progress;
    const now = Date.now();
    return manifest().cards.filter((card) => {
      if (topics && !topics.includes(card.topic)) return false;
      const record = progress.record(COURSE, card.id);
      const s = record?.s || 0;
      if (filter === "due") return progress.isDue(record, now);
      if (filter === "weak") return s === 1 || s === 2;
      if (filter === "unseen") return s === 0;
      if (filter === "key") return card.hy;
      return true;
    }).length;
  },
});

/** "3 / 5 worked" for a problem set or paper. */
export const setDone = (slug) => ({
  get done() {
    const set = manifest().sets.find((s) => s.slug === slug);
    return set ? this.$store.progress.countMarked(COURSE, set.questions) : 0;
  },
});

/** Read state of a document, for list rows. */
export const readMark = (key) => ({
  get read() {
    return this.$store.progress.status(COURSE, key) === 3;
  },
});

/** The reading page: mark as read. */
export const reader = (key) => ({
  get read() {
    return this.$store.progress.status(COURSE, key) === 3;
  },
  toggle() {
    this.$store.progress.set(COURSE, key, !this.read);
  },
});

/** Table of contents: highlights the section in view and shows how far you have read. */
export const toc = () => ({
  current: "",
  progress: 0,
  init() {
    const headings = [...document.querySelectorAll(".prose :is(h2, h3)[id]")];
    if (!headings.length) return;
    const update = () => {
      const line = window.scrollY + 120;
      let active = headings[0];
      for (const heading of headings) {
        if (heading.offsetTop <= line) active = heading;
      }
      this.current = active.id;
      const article = document.querySelector(".prose");
      const span = article.offsetHeight - window.innerHeight * 0.6;
      const done = window.scrollY - article.offsetTop + 120;
      this.progress = Math.max(0, Math.min(100, span > 0 ? (100 * done) / span : 100));
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
  },
});

/** "in 12 days" for an exam date (YYYY-MM-DD). */
export const countdown = (isoDate) => ({
  get days() {
    const [year, month, day] = isoDate.split("-").map(Number);
    const target = new Date(year, month - 1, day);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((target - today) / DAY);
  },
  get label() {
    const days = this.days;
    if (days === 0) return "Today";
    if (days === 1) return "Tomorrow";
    if (days > 1) return `In ${days} days`;
    return days === -1 ? "Yesterday" : `${-days} days ago`;
  },
  get soon() {
    return this.days >= 0 && this.days <= 14;
  },
});

/** Today's numbers on the dashboard. */
export const today = () => ({
  get day() {
    return this.$store.progress.days(1)[0];
  },
  get minutes() {
    return this.day.minutes;
  },
  get cards() {
    return this.day.cards;
  },
  get streak() {
    return this.$store.progress.streak();
  },
});

/** Fourteen days of activity as a row of bars. */
export const activity = () => ({
  get days() {
    const days = this.$store.progress.days(14);
    const amount = (day) => day.cards + day.minutes;
    const peak = Math.max(1, ...days.map(amount));
    return days.map((day, index) => ({
      date: day.key,
      label: WEEKDAYS[day.date.getDay()],
      level: amount(day) ? Math.max(12, (100 * amount(day)) / peak) : 0,
      today: index === days.length - 1,
      title: `${day.date.toLocaleDateString(undefined, { day: "numeric", month: "short" })}: ${plural(day.cards, "card")}, ${day.minutes} min`,
    }));
  },
});

/** Stat tiles on a course's home page. */
export const courseStats = () => ({
  get cards() {
    const s = this.$store.progress.stats(COURSE, cardIds(manifest()));
    const pct = share(s);
    return { ...s, pSolid: pct("solid"), pShaky: pct("shaky"), pMissed: pct("missed") };
  },
  get read() {
    const lectures = manifest().docs.filter((doc) => doc.kind === "lecture");
    return this.$store.progress.countMarked(
      COURSE,
      lectures.map((doc) => doc.key),
    );
  },
  get questions() {
    const ids = manifest().sets.flatMap((set) => set.questions);
    return this.$store.progress.countMarked(COURSE, ids);
  },
});

/** An exam's study plan: tick phases off. */
export const phases = (exam, count) => ({
  key(index) {
    return `phase:${exam}:${index}`;
  },
  done(index) {
    return this.$store.progress.status(COURSE, this.key(index)) === 3;
  },
  toggle(index) {
    this.$store.progress.set(COURSE, this.key(index), !this.done(index));
  },
  get finished() {
    let total = 0;
    for (let index = 0; index < count; index++) if (this.done(index)) total++;
    return total;
  },
});

/** How the cards inside an exam's scope are doing. */
export const scopeStats = (topics) => ({
  ids: [],
  init() {
    this.ids = cardIds(manifest(), (card) => topics.includes(card.topic));
  },
  get stats() {
    return this.$store.progress.stats(COURSE, this.ids);
  },
  pct(kind) {
    return share(this.stats)(kind);
  },
});

/** Best and latest score of a paper, from saved attempts. */
export const attemptSummary = (paperSlug) => ({
  get attempts() {
    return this.$store.progress.attempts(COURSE, paperSlug);
  },
  get label() {
    const list = this.attempts;
    if (!list.length) return "Not attempted";
    const last = list[list.length - 1];
    const best = list.reduce((a, b) => (b.score / b.max > a.score / a.max ? b : a));
    const show = (a) => `${+a.score.toFixed(1)} / ${+a.max.toFixed(1)}`;
    return list.length === 1 ? `Scored ${show(last)}` : `Last ${show(last)}, best ${show(best)}`;
  },
});
