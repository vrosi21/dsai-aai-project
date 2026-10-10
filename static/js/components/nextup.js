// "One next action, always."
//
// Choosing what to study is where the hour goes, so the site chooses. For each course it
// works out the single most useful thing to do right now, from what the course contains
// (the manifest) and what you have done (progress). On the dashboard the strongest
// suggestion across all active courses is shown; inside a course, that course's own.

import { COURSE, DAY, manifest, manifests, plural } from "../lib/util.js";

function suggest(course, progress) {
  const cards = progress.stats(
    course.slug,
    course.cards.map((card) => card.id),
  );

  // 1. An exam within three weeks that has a plan: do the next unticked phase.
  for (const exam of course.exams) {
    if (!exam.date || !exam.phases) continue;
    const days = Math.ceil((new Date(`${exam.date}T23:59:59`) - Date.now()) / DAY) - 1;
    if (days < 0 || days > 21) continue;
    let phase = 0;
    while (phase < exam.phases && progress.status(course.slug, `phase:${exam.slug}:${phase}`) === 3) {
      phase++;
    }
    if (phase < exam.phases) {
      const when = days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
      return {
        weight: 100 - days,
        title: `${exam.title} is ${when}`,
        detail: `Phase ${phase + 1} of ${exam.phases} in the plan is next.`,
        action: "Open the plan",
        url: `${exam.url}#plan`,
      };
    }
  }

  // 2. Cards whose review time has come.
  if (cards.due) {
    return {
      weight: 50 + Math.min(cards.due, 40),
      title: `${plural(cards.due, "card")} due for review`,
      detail: "Missed and shaky cards first, then the ones whose time has come.",
      action: "Review now",
      url: `${course.urls.review}?filter=due`,
    };
  }

  // 3. The first lecture not yet marked as read.
  const lecture = course.docs.find(
    (doc) => doc.kind === "lecture" && progress.status(course.slug, doc.key) !== 3,
  );
  if (lecture) {
    return {
      weight: 30,
      title: lecture.number === null ? lecture.title : `Lecture ${lecture.number}: ${lecture.title}`,
      detail: "Not read yet. Read it, mark it, then run its deck while it is fresh.",
      action: "Start reading",
      url: lecture.url,
    };
  }

  // 4. Cards never seen.
  if (cards.unseen) {
    return {
      weight: 20,
      title: `${plural(cards.unseen, "card")} you have not seen`,
      detail: "Answer out loud before you reveal. Grade honestly.",
      action: "Start",
      url: `${course.urls.review}?filter=unseen`,
    };
  }

  // 5. A practice set with questions left.
  for (const set of course.sets) {
    const left = set.questions.filter((id) => !progress.status(course.slug, id)).length;
    if (set.kind === "practice" && left) {
      return {
        weight: 10,
        title: set.title,
        detail: `${plural(left, "question")} left. On paper first, solutions closed.`,
        action: "Open the set",
        url: set.url,
      };
    }
  }
  return null;
}

export default () => ({
  get item() {
    const progress = this.$store.progress;
    progress.rev;
    const pool = COURSE ? [manifest()] : manifests().filter((m) => m.status === "active");
    let best = null;
    for (const course of pool) {
      const found = suggest(course, progress);
      if (found && (!best || found.weight > best.weight)) {
        best = { ...found, course: course.title, hue: course.hue };
      }
    }
    return best;
  },
});
