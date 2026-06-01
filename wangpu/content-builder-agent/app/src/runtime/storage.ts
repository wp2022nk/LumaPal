import type { ConnectionSettings } from "./types";

const BASE_URL_KEY = "content-builder.base-url";
const PAIRING_TOKEN_KEY = "content-builder.pairing-token";
const THREAD_ID_KEY = "content-builder.thread-id";

export function loadConnectionSettings(): ConnectionSettings {
  return {
    baseUrl: localStorage.getItem(BASE_URL_KEY) || "http://127.0.0.1:2024",
    // The web preview intentionally keeps the pairing token per browser session.
    // A native embedded runtime must replace this with Android Keystore storage.
    pairingToken: sessionStorage.getItem(PAIRING_TOKEN_KEY) || "",
  };
}

export function saveConnectionSettings(settings: ConnectionSettings): void {
  localStorage.setItem(BASE_URL_KEY, settings.baseUrl);
  if (settings.pairingToken) {
    sessionStorage.setItem(PAIRING_TOKEN_KEY, settings.pairingToken);
  } else {
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
