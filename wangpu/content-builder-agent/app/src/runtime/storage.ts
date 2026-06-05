import type { ConnectionSettings } from "./types";

const BASE_URL_KEY = "content-builder.base-url";
const PAIRING_TOKEN_KEY = "content-builder.pairing-token";
const THREAD_ID_KEY = "content-builder.thread-id";

export function loadConnectionSettings(): ConnectionSettings {
  const legacySessionToken = sessionStorage.getItem(PAIRING_TOKEN_KEY) || "";
  if (legacySessionToken && !localStorage.getItem(PAIRING_TOKEN_KEY)) {
    localStorage.setItem(PAIRING_TOKEN_KEY, legacySessionToken);
  }
  return {
    baseUrl: localStorage.getItem(BASE_URL_KEY) || "http://127.0.0.1:2024",
    pairingToken: localStorage.getItem(PAIRING_TOKEN_KEY) || legacySessionToken,
  };
}

export function saveConnectionSettings(settings: ConnectionSettings): void {
  localStorage.setItem(BASE_URL_KEY, settings.baseUrl);
  if (settings.pairingToken) {
    localStorage.setItem(PAIRING_TOKEN_KEY, settings.pairingToken);
    sessionStorage.setItem(PAIRING_TOKEN_KEY, settings.pairingToken);
  } else {
    localStorage.removeItem(PAIRING_TOKEN_KEY);
    sessionStorage.removeItem(PAIRING_TOKEN_KEY);
  }
}

export function loadThreadId(): string | null {
  return localStorage.getItem(THREAD_ID_KEY);
}

export function saveThreadId(threadId: string | null): void {
  if (threadId) {
    localStorage.setItem(THREAD_ID_KEY, threadId);
  } else {
    localStorage.removeItem(THREAD_ID_KEY);
  }
}
