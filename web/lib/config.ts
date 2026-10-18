// Server-side settings, written to .env.local by `python -m crewops.web_env` from the
// deployed stacks' outputs. None of them is a secret; none reaches the browser except the
// live-events domains, which /api/live hands to signed-in users.

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set; run \`uv run python -m crewops.web_env\` first`);
  }
  return value;
}

export const settings = {
  region: () => process.env.CREWOPS_REGION ?? process.env.AWS_REGION ?? "ap-southeast-2",
  clientId: () => required("CREWOPS_USER_POOL_CLIENT_ID"),
  runtimeArn: () => required("CREWOPS_MCP_RUNTIME_ARN"),
  eventsHttpDomain: () => required("CREWOPS_EVENTS_HTTP_DOMAIN"),
  eventsRealtimeDomain: () => required("CREWOPS_EVENTS_REALTIME_DOMAIN"),
  pollyVoice: () => process.env.CREWOPS_POLLY_VOICE ?? "Kajal",
  pollyEngine: () => process.env.CREWOPS_POLLY_ENGINE ?? "neural",
  /** Web push (desktop alerts): the app's public VAPID key; unset, desktop alerts are off. */
  pushPublicKey: () => process.env.CREWOPS_PUSH_PUBLIC_KEY ?? "",
};

export function invocationUrl(region: string, runtimeArn: string): string {
  return (
    `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/` +
    `${encodeURIComponent(runtimeArn)}/invocations?qualifier=DEFAULT`
  );
}
