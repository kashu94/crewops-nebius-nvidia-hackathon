export type Level = 'high' | 'medium' | 'low';

export type Cover = {
  seat: string;
  who: string;
  how: string;
  cost_inr: number | null;
};

export type Card = {
  disruption_id: string;
  status: string;
  handling: string | null;
  what: string;
  report_type: string;
  severity: Level | null;
  reasons: string[];
  desk: { desk_id: string; controller: string } | null;
  escalated: boolean;
  waiting_minutes: number;
  airlines: string[];
  cover: Cover[];
  impact: { flights: number; passengers: number; flight_ids: string[] };
};

export type Event = {
  at: string;
  event: string;
  title: string;
  text: string;
  disruption_id: string;
  severity: Level | null;
};

export type Departure = {
  flight_no: string;
  from: string;
  to: string;
  dep_utc: string;
  aircraft: string;
  airline: string | null;
  state: 'on plan' | 'affected' | 'cancelled';
};

export type DeskLoad = {
  desk_id: string;
  controller: string;
  open: number;
  high: number;
  escalated: number;
  submitted: number;
};

export type Board = {
  as_of: string;
  today: string;
  counts: Record<Level, number> & { escalated: number; open: number };
  desks: DeskLoad[];
  disruptions: Card[];
  day: { flights: number; passengers: number; crew_out: number; reserves_on_call: number };
  latest: Event[];
  departures: Departure[];
  reserve_cover: { airline: string; base: string; rank: string; on_call: number }[];
  system: { models: { state: string; retry_in_seconds: number } };
};
