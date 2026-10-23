// Questions the desk speaker can answer: who runs which desk and how loaded the room is.
// Answered by rules from the ops board, never by a model. The speaker has no signed-in person
// behind it, so it only says what the wall display already shows to anyone in the room.

type Level = "high" | "medium" | "low";

export type RoomBoard = {
  counts: Record<Level, number> & { escalated: number; open: number };
  desks: { desk_id: string; controller: string; open: number; high: number; escalated: number; submitted: number }[];
  disruptions: {
    what: string;
    status: string;
    severity: Level | null;
    escalated: boolean;
    desk: { desk_id: string; controller: string } | null;
  }[];
  day: { flights: number; passengers: number; crew_out: number; reserves_on_call: number };
};

export type RoomAnswer = { text: string; inScope: boolean };

export const OUT_OF_SCOPE =
  "That's outside what this speaker covers. I can tell you about the desks, the controllers, " +
  "open disruptions and today's numbers. For anything else, ask Alexa or the CrewOps assistant.";

const HELP =
  "You can ask: how many desks are there, who are the controllers, how many disruptions are open, " +
  "what's on Rohit's desk, which desk is busiest, anything escalated, or how many flights today.";

const INTRO =
  "I'm the CrewOps ops-room speaker. I read out each new disruption and its cover as it happens, " +
  "and answer questions about the desks, the controllers, open disruptions and today's numbers.";

const NUMBER_WORDS: Record<string, string> = { one: "1", won: "1", two: "2", to: "2", too: "2", three: "3" };

function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`;
}

function deskName(deskId: string): string {
  return deskId.replace("desk-", "desk ");
}

function listed(items: string[], max = 3): string {
  const shown = items.slice(0, max);
  const more = items.length - shown.length;
  const head = shown.length > 1 ? `${shown.slice(0, -1).join(", ")} and ${shown.at(-1)}` : (shown[0] ?? "");
  return more > 0 ? `${shown.join(", ")}, and ${more} more` : head;
}

/** The desk a question is about: a controller's first or full name, "desk 2", or "my desk"
 * when this speaker serves one desk. */
function deskAsked(q: string, board: RoomBoard, scope: string): RoomBoard["desks"][number] | undefined {
  const byName = board.desks.find((d) => {
    const [first] = d.controller.toLowerCase().split(" ");
    return q.includes(d.controller.toLowerCase()) || new RegExp(`\\b${first}('?s)?\\b`).test(q);
  });
  if (byName) return byName;
  const number = q.match(/\bdesk (?:number )?(\d|one|won|two|to|too|three)\b/)?.[1];
  if (number) return board.desks.find((d) => d.desk_id === `desk-${NUMBER_WORDS[number] ?? number}`);
  if (/\bmy desk\b|\bthis desk\b/.test(q)) return board.desks.find((d) => d.desk_id === scope);
  return undefined;
}

function deskDetail(d: RoomBoard["desks"][number], board: RoomBoard): string {
  const head = `${d.controller}, ${deskName(d.desk_id)}`;
  if (d.open === 0) return `${head}, has nothing open.`;
  const extras = [
    d.high ? `${d.high} high severity` : "",
    d.escalated ? `${d.escalated} escalated` : "",
    d.submitted ? `${d.submitted} waiting for approval` : "",
  ].filter(Boolean);
  const cards = board.disruptions.filter((c) => c.desk?.desk_id === d.desk_id);
  const worst = cards[0];
  return (
    `${head}, has ${plural(d.open, "open disruption")}${extras.length ? `: ${extras.join(", ")}` : ""}.` +
    (worst ? ` The most urgent: ${worst.what}${worst.severity ? `, ${worst.severity} severity` : ""}.` : "")
  );
}

export function answerRoomQuestion(question: string, board: RoomBoard, scope = "room"): RoomAnswer {
  const q = ` ${question
    .toLowerCase()
    .replace(/[^a-z0-9' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()} `;
  const ok = (text: string): RoomAnswer => ({ text, inScope: true });
  const howMany = /\bhow many\b|\bnumber of\b|\bcount\b/.test(q);

  if (/\bhelp\b|what can (i|you)|what do you (know|cover)/.test(q)) return ok(HELP);
  if (/^ (who|what) are you |^ what('s| is) your name |^ introduce yourself /.test(q)) return ok(INTRO);
  if (/^ (hi|hello|hey|good (morning|afternoon|evening)) $/.test(q)) return ok(`Hello. ${HELP}`);
  if (/^ (ok |okay )?(thanks|thank you|cheers)( very much)? $/.test(q)) return ok("You're welcome.");

  const desk = deskAsked(q, board, scope);
  if (desk) return ok(deskDetail(desk, board));

  const perDesk = /\beach\b|\bper\b|\bevery\b|\ball\b|\bwhich\b|\bload\b|\bwhat\b|\bhas\b|\bhave\b/.test(q);
  const loads = () =>
    ok(
      `${listed(
        board.desks.map((d) => `${d.controller} has ${d.open} open${d.high ? `, ${d.high} high` : ""}`),
        board.desks.length,
      )}.`,
    );

  if (/\bcontrollers?\b/.test(q)) {
    if (perDesk && !/\bwho\b|\bnames?\b/.test(q)) return loads();
    const names = board.desks.map((d) => `${d.controller} on ${deskName(d.desk_id)}`);
    if (howMany && !/\bwho\b|\bnames?\b/.test(q)) {
      return ok(`There are ${plural(board.desks.length, "controller")}, one per desk.`);
    }
    return ok(`There are ${plural(board.desks.length, "controller")}: ${listed(names, names.length)}.`);
  }

  if (/\bbusiest\b|\bmost\b|\bleast\b|\bquietest\b/.test(q) && /\bdesk|controller|disruption|open\b/.test(q)) {
    if (board.desks.every((d) => d.open === 0)) return ok("Every desk is quiet: nothing is open.");
    const sorted = [...board.desks].sort((a, b) => b.open - a.open || b.high - a.high);
    const pick = /\bleast\b|\bquietest\b/.test(q) ? sorted.at(-1) : sorted[0];
    return ok(pick ? deskDetail(pick, board) : "There are no desks on the board.");
  }

  if (/\bdesks?\b/.test(q)) {
    if (howMany && !perDesk) {
      return ok(
        `There are ${plural(board.desks.length, "desk")}: ` +
          `${listed(
            board.desks.map((d) => `${deskName(d.desk_id)} with ${d.controller}`),
            board.desks.length,
          )}.`,
      );
    }
    return loads();
  }

  if (/\bescalat/.test(q)) {
    const hit = board.disruptions.filter((c) => c.escalated);
    if (!hit.length) return ok("Nothing is escalated right now.");
    return ok(
      `${plural(hit.length, "disruption is", "disruptions are")} escalated: ${listed(hit.map((c) => c.what))}.`,
    );
  }

  if (/\bhigh\b|\bsevere\b|\bserious\b|\burgent\b|\bcritical\b/.test(q)) {
    const hit = board.disruptions.filter((c) => c.severity === "high");
    if (!hit.length) return ok("Nothing open is high severity right now.");
    return ok(`${plural(hit.length, "high severity disruption")}: ${listed(hit.map((c) => c.what))}.`);
  }

  if (/\bapprov|\bwaiting\b|\bsubmitted\b|\bpending\b/.test(q)) {
    const waiting = board.desks.reduce((n, d) => n + d.submitted, 0);
    return ok(
      waiting ? `${plural(waiting, "plan is", "plans are")} waiting for approval.` : "No plan is waiting for approval.",
    );
  }

  if (/\bdisruptions?\b|\bopen\b|\bissues?\b|\bproblems?\b|\bgoing on\b|\bsituation\b|\bstatus\b/.test(q)) {
    const { open, high, medium, low, escalated } = board.counts;
    if (!open) return ok("No disruptions are open. All quiet.");
    // a report not yet analysed is open but has no severity
    const unrated = open - high - medium - low;
    const levels = [
      high && `${high} high`,
      medium && `${medium} medium`,
      low && `${low} low`,
      unrated > 0 && `${unrated} not yet assessed`,
    ].filter(Boolean);
    return ok(
      `${plural(open, "disruption is", "disruptions are")} open: ${levels.join(", ")}` +
        `${escalated ? `, and ${escalated} escalated` : ""}.`,
    );
  }

  if (/\bcrew (are |is )?out\b|\bunavailable\b|\bsick\b|\babsent\b|\bout today\b|\bon leave\b/.test(q)) {
    return ok(`${plural(board.day.crew_out, "crew member is", "crew members are")} out today.`);
  }

  if (/\breserves?\b|\bstandby\b|\bon call\b/.test(q)) {
    return ok(`${plural(board.day.reserves_on_call, "reserve is", "reserves are")} on call today.`);
  }

  if (/\bflights?\b|\bpassengers?\b|\btoday\b/.test(q)) {
    return ok(
      `${plural(board.day.flights, "flight")} today, ` +
        `with ${board.day.passengers.toLocaleString("en-IN")} passengers booked.`,
    );
  }

  return { text: OUT_OF_SCOPE, inScope: false };
}
