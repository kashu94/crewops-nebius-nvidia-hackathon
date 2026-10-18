"use client";

// The browser's own speech recognition (Chrome and Edge), typed minimally. When it is
// missing, the assistant is typed to instead.

type Result = { isFinal: boolean; 0: { transcript: string } };
type ResultEvent = { resultIndex: number; results: ArrayLike<Result> };

export type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: ResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
};

export function makeRecognition(): Recognition | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => Recognition;
    webkitSpeechRecognition?: new () => Recognition;
  };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const r = new Ctor();
  r.lang = "en-IN";
  r.interimResults = true;
  r.continuous = false;
  return r;
}
