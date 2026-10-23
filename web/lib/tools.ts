// The MCP tools the web app may call on a user's behalf. The server decides what each user
// may actually do (`crew_tool` runs the deterministic tools through the same role-checked
// dispatch as the assistant); this list only keeps the proxy from being a door to anything
// else.

export const TOOLS = {
  whoami: { list: false, write: false },
  disruptions: { list: true, write: false },
  my_roster: { list: false, write: false },
  my_reports: { list: true, write: false },
  report_reference: { list: false, write: false },
  rule_thresholds: { list: true, write: false },
  set_rule_threshold: { list: false, write: true },
  activity_log: { list: true, write: false },
  crew_tool: { list: false, write: false },
  ask: { list: false, write: false },
  my_queue: { list: true, write: false },
  speaker_feed: { list: false, write: false },
  welcome_back: { list: false, write: true },
  still_here: { list: false, write: true },
  operations_overview: { list: false, write: false },
  ops_board: { list: false, write: false },
  push_subscribe: { list: false, write: true },
  push_unsubscribe: { list: false, write: true },
  show_disruption: { list: false, write: false },
  report_disruption: { list: false, write: true },
  submit_plan: { list: false, write: true },
  approve_plan: { list: false, write: true },
  reject_plan: { list: false, write: true },
  withdraw_plan: { list: false, write: true },
  close_disruption: { list: false, write: true },
  confirm_resolution: { list: false, write: true },
  return_resolution: { list: false, write: true },
  suggest_cover: { list: false, write: true },
  alert_addresses: { list: true, write: false },
  add_alert_address: { list: false, write: true },
  remove_alert_address: { list: false, write: true },
  revoke_report: { list: false, write: true },
  team_accounts: { list: true, write: false },
  reset_password: { list: false, write: true },
} as const;

export type ToolName = keyof typeof TOOLS;

export function isTool(name: string): name is ToolName {
  return Object.prototype.hasOwnProperty.call(TOOLS, name);
}

/** A state-changing request must come from this site: browsers send Origin on POST. Behind a
 * proxy the site's host is the first `x-forwarded-host` entry (a chain of proxies lists
 * several) or `host`, either of which may carry the default port. A cross-site page can set
 * neither, so accepting either match lets no other site in. */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  let site: URL;
  try {
    site = new URL(origin);
  } catch {
    return false;
  }
  const forwarded = request.headers.get("x-forwarded-host")?.split(",")[0]?.trim();
  return [forwarded, request.headers.get("host")].some((host) => {
    if (!host) return false;
    try {
      return new URL(`${site.protocol}//${host}`).host === site.host;
    } catch {
      return false;
    }
  });
}
