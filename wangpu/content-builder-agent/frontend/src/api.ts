import type { AgentManifest, ArtifactItem, FilePayload, WorkspaceEntry } from "./types";

function defaultAgentUrl(): string {
  if (typeof window === "undefined") return "http://localhost:2024";
  return window.location.hostname === "127.0.0.1"
    ? "http://127.0.0.1:2024"
    : "http://localhost:2024";
}

export const AGENT_URL = import.meta.env.VITE_AGENT_URL ?? defaultAgentUrl();

function fallbackAgentUrl(): string | null {
  if (AGENT_URL.includes("localhost")) {
    return AGENT_URL.replace("localhost", "127.0.0.1");
  }
  if (AGENT_URL.includes("127.0.0.1")) {
    return AGENT_URL.replace("127.0.0.1", "localhost");
  }
  return null;
}

export async function fetchAgent(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(`${AGENT_URL}${path}`, init);
  } catch (error) {
    const fallback = fallbackAgentUrl();
    if (!fallback) throw error;
    return fetch(`${fallback}${path}`, init);
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetchAgent(path);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export function assetUrl(path: string): string {
  const query = new URLSearchParams({ path });
  return `${AGENT_URL}/api/workspace/asset?${query.toString()}`;
}

export function getManifest(): Promise<AgentManifest> {
  return fetchJson<AgentManifest>("/api/agent/manifest");
}

export async function getArtifacts(): Promise<ArtifactItem[]> {
  const data = await fetchJson<{ items: ArtifactItem[] }>("/api/artifacts");
  return data.items;
}

export async function getTree(path = ".", depth = 2): Promise<WorkspaceEntry[]> {
  const query = new URLSearchParams({ path, depth: String(depth) });
  const data = await fetchJson<{ entries: WorkspaceEntry[] }>(`/api/workspace/tree?${query.toString()}`);
  return data.entries;
}

export function getFile(path: string): Promise<FilePayload> {
  const query = new URLSearchParams({ path });
  return fetchJson<FilePayload>(`/api/workspace/file?${query.toString()}`);
}
