import { describe, expect, it } from "vitest";

import { homeFor, viewsFor } from "@/lib/roles";
import { claimsOf, expiresSoon } from "@/lib/tokens";
import { isTool, sameOrigin } from "@/lib/tools";
import { invocationUrl } from "@/lib/config";

function jwt(claims: object) {
  const part = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${part({ alg: "none" })}.${part(claims)}.sig`;
}

describe("tokens", () => {
  it("reads claims and expiry without trusting them for anything else", () => {
    const token = jwt({ username: "rohit", exp: 1000, "cognito:groups": ["controller"] });
    expect(claimsOf(token)?.username).toBe("rohit");
    expect(expiresSoon(token, 900)).toBe(false);
    expect(expiresSoon(token, 950)).toBe(true); // within the 60 s margin
    expect(expiresSoon(undefined, 0)).toBe(true);
    expect(claimsOf("garbage")).toBeNull();
  });
});

describe("roles and views", () => {
  it("sends each role to its home", () => {
    expect(homeFor(["crew"])).toBe("/crew");
    expect(homeFor(["approver"])).toBe("/approvals");
    expect(homeFor(["controller"])).toBe("/console");
    expect(homeFor(["admin"])).toBe("/admin");
    expect(homeFor(["reporter"])).toBe("/operations");
  });

  it("shows only the views a role uses", () => {
    expect(viewsFor(["crew"]).map((v) => v.href)).toEqual(["/crew", "/assistant"]);
    expect(viewsFor(["controller"]).map((v) => v.href)).toEqual(["/console", "/disruptions", "/assistant"]);
    expect(viewsFor(["approver"]).map((v) => v.href)).toEqual(["/disruptions", "/approvals", "/assistant"]);
    expect(viewsFor(["admin"]).map((v) => v.href)).toEqual(["/console", "/disruptions", "/admin", "/assistant"]);
    expect(viewsFor(["reporter"]).map((v) => v.href)).toEqual(["/operations", "/report", "/assistant"]);
  });
});

describe("the tool proxy", () => {
  it("forwards only known tools", () => {
    expect(isTool("approve_plan")).toBe(true);
    expect(isTool("crew_tool")).toBe(true); // role-checked dispatch on the server
    expect(isTool("list_crew_tools")).toBe(false);
    expect(isTool("__proto__")).toBe(false);
    expect(isTool("constructor")).toBe(false);
  });

  it("accepts state-changing requests only from this site", () => {
    const req = (origin: string | null, host = "localhost:3000") =>
      new Request("http://localhost:3000/api/tools/ask", {
        method: "POST",
        headers: { ...(origin ? { origin } : {}), host },
      });
    expect(sameOrigin(req("http://localhost:3000"))).toBe(true);
    expect(sameOrigin(req("https://evil.example"))).toBe(false);
    expect(sameOrigin(req(null))).toBe(false);
  });

  it("encodes the runtime ARN in the invocation URL", () => {
    expect(invocationUrl("ap-southeast-2", "arn:aws:bedrock-agentcore:ap-southeast-2:111111111111:runtime/x")).toBe(
      "https://bedrock-agentcore.ap-southeast-2.amazonaws.com/runtimes/" +
        "arn%3Aaws%3Abedrock-agentcore%3Aap-southeast-2%3A111111111111%3Aruntime%2Fx/invocations?qualifier=DEFAULT",
    );
  });
});
