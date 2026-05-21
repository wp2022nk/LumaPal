export type RunStreamEvent = {
  event: string;
  data: unknown;
};

export type NormalizedStreamPayload = {
  data: unknown;
  namespace: string[];
  metadata?: Record<string, unknown>;
};

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isNamespace(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function namespaceFromMetadata(value: unknown): string[] {
  if (!isPlainObject(value)) return [];
  const namespace =
    value.langgraph_checkpoint_ns ??
    value.namespace ??
    value.ns;
  if (isNamespace(namespace)) return namespace;
  if (typeof namespace === "string") return namespace.split("|").filter(Boolean);
  return [];
}

function parseSseEvent(frame: string): RunStreamEvent | null {
  const lines = frame.split(/\r?\n/);
  const event = lines
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const dataText = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n");

  if (!event && !dataText) return null;

  let data: unknown = dataText;
  if (dataText) {
    try {
      data = JSON.parse(dataText);
    } catch {
      data = dataText;
    }
  }

  return { event: event ?? "message", data };
}

export function splitStreamEventName(eventName: string): {
  baseEvent: string;
  namespace: string[];
} {
  const [baseEvent, ...namespace] = eventName.split("|");
  return { baseEvent, namespace: namespace.filter(Boolean) };
}

export function normalizeStreamPayloads(
  eventName: string,
  data: unknown,
  eventNamespace: string[] = [],
): NormalizedStreamPayload[] {
  if (Array.isArray(data)) {
    if (data.length === 3 && isNamespace(data[0]) && typeof data[1] === "string") {
      return [{ namespace: data[0], data: data[2] }];
    }

    if (data.length === 2 && isNamespace(data[0])) {
      return [{ namespace: data[0], data: data[1] }];
    }

    const metadata = isPlainObject(data[1]) ? data[1] : undefined;
    return [
      {
        namespace: eventNamespace.length ? eventNamespace : namespaceFromMetadata(metadata),
        metadata,
        data,
      },
    ];
  }

  if (isPlainObject(data)) {
    const rawNamespace = data.namespace ?? data.ns;
    const namespace = isNamespace(rawNamespace)
      ? rawNamespace
      : typeof rawNamespace === "string"
        ? rawNamespace.split("|").filter(Boolean)
        : eventNamespace;

    if ("data" in data) {
      return [{ namespace, data: data.data, metadata: data as Record<string, unknown> }];
    }

    if (eventName === "messages" && ("message" in data || "chunk" in data)) {
      const chunk = data.message ?? data.chunk;
      const metadata = isPlainObject(data.metadata) ? data.metadata : data;
      return [{ namespace, data: [chunk, metadata], metadata }];
    }
  }

  return [{ namespace: eventNamespace, data }];
}

export function namespaceSource(namespace: string[]): string | null {
  for (const segment of namespace) {
    if (!segment.includes(":")) continue;
    const source = segment.split(":")[0]?.trim();
    if (source && !source.startsWith("__")) return source;
  }
  return null;
}

export function isSubagentNamespace(namespace: string[]): boolean {
  const source = namespaceSource(namespace);
  return Boolean(source && source !== "main");
}

export async function* iterateSseEvents(
  response: Response,
): AsyncGenerator<RunStreamEvent> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      while (true) {
        const crlfIndex = buffer.indexOf("\r\n\r\n");
        const lfIndex = buffer.indexOf("\n\n");
        let frameEnd = -1;
        let separatorLength = 2;

        if (crlfIndex >= 0 && (lfIndex < 0 || crlfIndex < lfIndex)) {
          frameEnd = crlfIndex;
          separatorLength = 4;
        } else if (lfIndex >= 0) {
          frameEnd = lfIndex;
        } else {
          break;
        }

        const frame = buffer.slice(0, frameEnd);
        buffer = buffer.slice(frameEnd + separatorLength);

        const parsed = parseSseEvent(frame);
        if (parsed) yield parsed;
      }
    }

    const tail = buffer.trim();
    if (tail) {
      const parsed = parseSseEvent(tail);
      if (parsed) yield parsed;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {}
  }
}
