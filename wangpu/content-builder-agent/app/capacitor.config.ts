import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.wangpu.contentbuilder",
  appName: "童芯智造",
  webDir: "dist",
  android: {
    // Debug APK connects to the trusted-LAN FastAPI server over HTTP.
    // Capacitor serves bundled assets from https://localhost, so Android must
    // allow mixed content for local development builds.
    allowMixedContent: true,
  },
  server: {
    androidScheme: "https",
    cleartext: true,
  },
};

export default config;
