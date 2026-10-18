// Turning an MCP tool result into data, and a tool error into a code and a message.

export class ToolCallError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

type Content = { type: string; text?: string };
export type ToolResult = { isError?: boolean; content?: Content[]; structuredContent?: unknown };

const PREFIX = /^Error executing tool \w+:\s*/;

/** "FORBIDDEN: your role may not …" -> code FORBIDDEN, message "your role may not …". */
export function errorOf(text: string): ToolCallError {
  const plain = text.replace(PREFIX, "").trim();
  const match = /^([A-Z][A-Z_]+):\s*([\s\S]*)$/.exec(plain);
  return match ? new ToolCallError(match[1], match[2]) : new ToolCallError("ERROR", plain);
}

/** The tool's value. List tools arrive as one text item per element, so `asList` keeps a
 * one-element list a list. */
export function valueOf(result: ToolResult, asList: boolean): unknown {
  const texts = (result.content ?? []).filter((c) => c.type === "text").map((c) => c.text ?? "");
  if (result.isError) {
    throw errorOf(texts.join(" "));
  }
  if (result.structuredContent !== undefined && result.structuredContent !== null) {
    const structured = result.structuredContent as { result?: unknown };
    return asList && Array.isArray(structured.result) ? structured.result : structured;
  }
  const values = texts.map((t) => {
    try {
      return JSON.parse(t);
    } catch {
      return t;
    }
  });
  return asList || values.length !== 1 ? values : values[0];
}
