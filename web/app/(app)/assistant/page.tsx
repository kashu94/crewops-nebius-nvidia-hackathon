"use client";

import { Activity } from "lucide-react";

import { AlexaModule } from "@/components/alexa";
import { useMe } from "@/components/me";
import { Panel } from "@/components/ui";

const BY_ROLE: Record<string, string[]> = {
  crew: ["How many duty hours do I have?", "Is my medical valid?", "Am I legal for P-2291?", "What is RULE-REST-04?"],
  reporter: ["Is gate BLR-G1 free on 14 Oct at 06:00?", "Which controller desks are there?", "What is RULE-FDP-01?"],
};
const STAFF = [
  "C-1042 is sick. Who covers?",
  "Who is on reserve at BLR on 13 Oct?",
  "How many duty hours does C-2087 have?",
  "Which certifications expire within 30 days?",
  "Which single flight leg has the most seats at risk if cancelled?",
];

export default function Assistant() {
  const me = useMe();
  const suggestions = me.roles.includes("crew")
    ? BY_ROLE.crew
    : me.roles.includes("reporter") && me.roles.length === 1
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
