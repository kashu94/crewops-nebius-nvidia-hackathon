import { describe, expect, it } from "vitest";

import { errorOf, ToolCallError, valueOf } from "@/lib/mcp-result";

const text = (t: string) => ({ type: "text", text: t });

describe("MCP tool results", () => {
  it("parses JSON text, keeping list tools as lists", () => {
    expect(valueOf({ content: [text('{"answer":"yes"}')] }, false)).toEqual({ answer: "yes" });
    expect(valueOf({ content: [text('{"a":1}')] }, true)).toEqual([{ a: 1 }]);
    expect(valueOf({ content: [] }, true)).toEqual([]);
    expect(valueOf({ content: [text("plain")] }, false)).toBe("plain");
  });

  it("prefers structured content", () => {
    expect(valueOf({ structuredContent: { result: [1, 2] }, content: [] }, true)).toEqual([1, 2]);
  });

  it("turns tool errors into a code and a message", () => {
    const err = errorOf("Error executing tool approve_plan: FORBIDDEN: you submitted this plan");
    expect(err).toBeInstanceOf(ToolCallError);
    expect(err.code).toBe("FORBIDDEN");
    expect(err.message).toBe("you submitted this plan");
    expect(() =>
      valueOf({ isError: true, content: [text("UNAUTHORIZED: sign in first")] }, false),
    ).toThrowError("sign in first");
    expect(errorOf("something odd").code).toBe("ERROR");
  });
});
