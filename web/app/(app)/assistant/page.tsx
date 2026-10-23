"use client";

import { Activity } from "lucide-react";

import { AlexaModule } from "@/components/alexa";
import { useMe } from "@/components/me";
import { Panel } from "@/components/ui";
import type { Role } from "@/lib/roles";

// No dates: the live service runs on today's date, so a fixed day soon reads as the past.
const BY_ROLE: Record<string, string[]> = {
  crew: [
    "When do I fly next?",
    "Am I legal for my next trip?",
    "How many duty hours do I have?",
    "Is my medical valid?",
  ],
  reporter: ["Is gate BLR-G6 free?", "Which controller desks are there?", "What's the FDP limit?"],
  admin: [
    "What can I do that a super admin can't?",
    "How many open disruptions does each controller have?",
    "What are the rules?",
  ],
};
const STAFF = [
  "Who is on reserve at BLR today?",
  "How many duty hours does C-2087 have?",
  "Which certifications expire within 30 days?",
  "What's the FDP limit?",
];

export default function Assistant() {
  const me = useMe();
  const has = (role: Role) => me.roles.includes(role);
  const suggestions = has("crew")
    ? BY_ROLE.crew
    : has("controller") || has("approver")
      ? ["What's waiting for me?", ...STAFF]
      : has("admin") || has("super_admin")
        ? BY_ROLE.admin
        : has("reporter") && me.roles.length === 1
          ? BY_ROLE.reporter
          : STAFF;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 lg:h-[calc(100vh-6.75rem)] lg:overflow-hidden">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Alexa+</h1>
        <p className="text-sm text-muted">
          Ask by voice or text. Every figure comes from the engine and is checked before it is spoken.
        </p>
      </div>
      <Panel
        title="Alexa+ voice"
        icon={<Activity size={14} className="text-accent" />}
        className="lg:min-h-0 lg:flex-1"
        bodyClassName="lg:overflow-hidden"
      >
        <AlexaModule suggestions={suggestions} wide className="lg:h-full" />
      </Panel>
    </div>
  );
}
