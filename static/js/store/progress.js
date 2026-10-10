// Study progress. This is the only module that reads or writes browser storage for it.
//
// Everything is one flat map of records keyed "<course>/<id>":
//     { s: 0-3, t: timestamp, k: solid streak, n: times graded }
// s = 1 missed, 2 shaky, 3 solid (also used for "read" and "done"); s = 0 is a tombstone
// left by clearing a grade. Because every record has a timestamp and nothing is ever
// deleted, two copies of the data can be merged by keeping the newer record per key. That
// is all Import does today, and all a server sync would need to do later.

import { DAY, dayKey } from "../lib/util.js";

const KEY = "dsai:v1";
// Written into an exported file so Import can tell it is ours. The second name is what the
// site was called before it became DSAI Vault; files exported back then still import.
const EXPORT_IDS = ["dsai-vault", "dsai.pages"];
// Days before a solid card comes back, by how many times in a row it was solid.
const INTERVALS = [1, 3, 7, 14, 30];

const blank = () => ({ v: 1, records: {}, attempts: {}, activity: {}, prefs: {} });

let state = blank();

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    state = raw ? { ...blank(), ...JSON.parse(raw) } : blank();
  } catch {
    state = blank();
  }
}

function storageWorks() {
  try {
    localStorage.setItem("dsai:probe", "1");
    localStorage.removeItem("dsai:probe");
    return true;
  } catch {
    return false;
  }
}

/** Fold `incoming` into `target`: newest record wins, attempts are united. */
export function merge(target, incoming) {
  let changed = 0;
  for (const [key, theirs] of Object.entries(incoming.records || {})) {
    const mine = target.records[key];
    if (!mine || (theirs && theirs.t > mine.t)) {
      target.records[key] = theirs;
      changed++;
    }
  }
  for (const [key, list] of Object.entries(incoming.attempts || {})) {
    const known = new Set((target.attempts[key] || []).map((a) => a.t));
    const fresh = list.filter((a) => !known.has(a.t));
    if (fresh.length) {
      target.attempts[key] = [...(target.attempts[key] || []), ...fresh].sort((a, b) => a.t - b.t);
      changed += fresh.length;
    }
  }
  for (const [day, theirs] of Object.entries(incoming.activity || {})) {
    const mine = (target.activity[day] ||= { cards: 0, minutes: 0 });
    // The same day seen on two devices: keep the larger count rather than adding twice.
    mine.cards = Math.max(mine.cards || 0, theirs.cards || 0);
    mine.minutes = Math.max(mine.minutes || 0, theirs.minutes || 0);
  }
  return changed;
}

export function createProgress() {
  load();
  let timer = null;

  const store = {
    // Reading `rev` inside a getter makes Alpine re-run it whenever progress changes.
    rev: 0,
    persistent: storageWorks(),

    touch() {
      this.rev++;
      clearTimeout(timer);
      timer = setTimeout(() => this.flush(), 120);
    },
    flush() {
      try {
        localStorage.setItem(KEY, JSON.stringify(state));
        this.persistent = true;
      } catch {
        this.persistent = false;
      }
    },

    // ---- records ---------------------------------------------------------------------
    record(course, id) {
      this.rev;
      return state.records[`${course}/${id}`];
    },
    status(course, id) {
      return this.record(course, id)?.s || 0;
    },
    grade(course, id, s, { count = true } = {}) {
      const key = `${course}/${id}`;
      const previous = state.records[key];
      state.records[key] = {
        s,
        t: Date.now(),
        k: s === 3 ? (previous?.k || 0) + 1 : 0,
        n: (previous?.n || 0) + (s ? 1 : 0),
      };
      if (s && count) this.log("cards", 1);
      this.touch();
    },
    /** A yes/no mark (lecture read, phase done) stored the same way as a grade. */
    set(course, id, on) {
      this.grade(course, id, on ? 3 : 0, { count: false });
    },
    isDue(record, now = Date.now()) {
      if (!record || !record.s) return false;
      if (record.s === 1) return true;
      if (record.s === 2) return now - record.t >= DAY;
      const days = INTERVALS[Math.min(record.k || 1, INTERVALS.length) - 1];
      return now - record.t >= days * DAY;
    },
    stats(course, ids) {
      this.rev;
      const out = { total: ids.length, solid: 0, shaky: 0, missed: 0, unseen: 0, due: 0 };
      const now = Date.now();
      for (const id of ids) {
        const record = state.records[`${course}/${id}`];
        const s = record?.s || 0;
        out[["unseen", "missed", "shaky", "solid"][s]]++;
        if (this.isDue(record, now)) out.due++;
      }
      return out;
    },
    countMarked(course, ids) {
      this.rev;
      return ids.filter((id) => state.records[`${course}/${id}`]?.s).length;
    },

    // ---- mock exam attempts ----------------------------------------------------------
    attempts(course, paper) {
      this.rev;
      return state.attempts[`${course}/${paper}`] || [];
    },
    addAttempt(course, paper, attempt) {
      (state.attempts[`${course}/${paper}`] ||= []).push({ ...attempt, t: Date.now() });
      this.touch();
    },

    // ---- activity --------------------------------------------------------------------
    log(kind, amount) {
      const day = (state.activity[dayKey()] ||= { cards: 0, minutes: 0 });
      day[kind] = (day[kind] || 0) + amount;
      this.touch();
    },
    /** The last `count` days, oldest first. */
    days(count) {
      this.rev;
      const out = [];
      for (let back = count - 1; back >= 0; back--) {
        const date = new Date(Date.now() - back * DAY);
        const key = dayKey(date);
        out.push({ key, date, cards: 0, minutes: 0, ...state.activity[key] });
      }
      return out;
    },
    streak() {
      this.rev;
      const active = (key) => {
        const day = state.activity[key];
        return day && (day.cards > 0 || day.minutes > 0);
      };
      let length = 0;
      // A streak is still alive if today has nothing yet but yesterday did.
      let back = active(dayKey()) ? 0 : 1;
      while (active(dayKey(new Date(Date.now() - back * DAY)))) {
        length++;
        back++;
      }
      return length;
    },

    // ---- moving data -----------------------------------------------------------------
    exportText() {
      return JSON.stringify({ app: EXPORT_IDS[0], exported: new Date().toISOString(), ...state }, null, 2);
    },
    importText(text) {
      const incoming = JSON.parse(text);
      if (!EXPORT_IDS.includes(incoming?.app) || typeof incoming.records !== "object") {
        throw new Error("This file is not a DSAI Vault progress export.");
      }
      const changed = merge(state, incoming);
      this.touch();
      return changed;
    },
    reset(course = null) {
      if (!course) {
        state = { ...blank(), prefs: state.prefs };
      } else {
        const prefix = `${course}/`;
        for (const bucket of [state.records, state.attempts]) {
          for (const key of Object.keys(bucket)) if (key.startsWith(prefix)) delete bucket[key];
        }
      }
      this.touch();
      this.flush();
    },
    totals() {
      this.rev;
      const records = Object.values(state.records).filter((r) => r.s);
      return { records: records.length, days: Object.keys(state.activity).length };
    },
  };

  // Another tab changed progress: pick it up, so two open tabs never overwrite each other.
  window.addEventListener("storage", (event) => {
    if (event.key !== KEY) return;
    load();
    const live = window.Alpine?.store("progress");
    if (live) live.rev++;
  });
  // A page change within 120 ms of a grade must not lose it.
  window.addEventListener("pagehide", () => store.flush());
  return store;
}
