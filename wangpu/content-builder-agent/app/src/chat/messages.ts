export interface ContentBlock {
  type?: string;
  text?: string;
  image_url?: string | { url?: string };
}

export interface AppMessage {
  id?: string;
  type?: string;
  role?: string;
  content?: string | ContentBlock[];
  tool_calls?: Array<{ id?: string; name?: string; args?: unknown }>;
  name?: string;
  additional_kwargs?: Record<string, unknown>;
  _getType?: () => string;
}

export function messageRole(message: AppMessage): string {
  return message.type || message.role || message._getType?.() || "assistant";
}

export function messageText(message: AppMessage): string {
  if (typeof message.content === "string") {
    return message.content;
  }
  if (!Array.isArray(message.content)) {
    return "";
  }
  return message.content
    .filter((block) => block.type === "text" || block.type === "output_text" || !block.type)
    .map((block) => block.text || "")
    .join("");
}

export function messageImages(message: AppMessage): string[] {
  if (!Array.isArray(message.content)) {
    return [];
  }
  return message.content.flatMap((block) => {
    if (typeof block.image_url === "string") {
      return [block.image_url];
    }
    if (block.image_url?.url) {
      return [block.image_url.url];
    }
    return [];
  });
}

export function messageReasoning(message: AppMessage): string {
  const reasoning = message.additional_kwargs?.reasoning_content;
  if (typeof reasoning === "string") {
    return reasoning;
  }
  if (!Array.isArray(message.content)) {
    return "";
  }
  return message.content
    .filter((block) => block.type === "thinking" || block.type === "reasoning")
    .map((block) => block.text || "")
    .join("");
}

export function isAssistant(message: AppMessage): boolean {
  return ["ai", "assistant"].includes(messageRole(message));
}
