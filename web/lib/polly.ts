import "server-only";

import { PollyClient, SynthesizeSpeechCommand, type Engine, type VoiceId } from "@aws-sdk/client-polly";

import { settings } from "./config";

const MAX_SPOKEN_CHARS = 1500;
let polly: PollyClient | null = null;

/** Text as MP3 in the deployment's Polly voice (Indian English). Used for answers and for
 * the desk speaker's alerts, so both sound the same. */
export async function synthesize(text: string): Promise<Uint8Array<ArrayBuffer>> {
  polly ??= new PollyClient({
    region: settings.region(),
    requestHandler: { connectionTimeout: 5_000, requestTimeout: 15_000 },
  });
  const out = await polly.send(
    new SynthesizeSpeechCommand({
      Text: text.slice(0, MAX_SPOKEN_CHARS),
      OutputFormat: "mp3",
      VoiceId: settings.pollyVoice() as VoiceId,
      Engine: settings.pollyEngine() as Engine,
      LanguageCode: "en-IN",
    }),
  );
  const audio = await out.AudioStream?.transformToByteArray();
  if (!audio) throw new Error("no audio");
  return new Uint8Array(audio);
}
