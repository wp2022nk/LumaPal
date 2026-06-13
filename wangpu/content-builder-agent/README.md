# Content Builder Agent Android App

This repository contains a trusted-LAN development version of the Content Builder Android companion app. The new frontend lives in `app/`; the old `wangpu/src` frontend is intentionally unused.

## Architecture

- Python Deep Agents server: `content_builder/`
- React + TypeScript + Vite frontend: `app/src/`
- Capacitor Android shell: `app/android/`
- Per-conversation output: `output/threads/<thread_id>/{artifacts,games,uploads,workspace}/`
- Daily history snapshots: `history/<YYYY-MM-DD>/threads/<thread_id>/{artifacts,games,uploads}/`

Every conversation has its own artifacts, uploads, games, and writable workspace. Opening a historical conversation switches both the LangGraph thread and the file browser to that conversation.

The v1 APK does **not** bundle Python, FunASR, PyTorch, or the local shell backend. It connects to the computer over the local network. `app/src/runtime/embeddedDeepAgentsRuntime.ts` reserves the phase-two replacement boundary for an embedded TypeScript `createDeepAgent()` runtime.

## Start The LAN Server

Install Python dependencies:

```powershell
uv sync
```

Voice input uses the computer-side local FunASR models and CPU PyTorch runtime.
Text messages and recognized voice messages both receive streamed Qwen TTS audio
and emotion events in the App.

Start the server from this directory:

```powershell
.\scripts\dev-backend.ps1
```

The script prints the pairing token and listens on `http://0.0.0.0:2024`. In the App settings screen, enter the computer's LAN address such as `http://192.168.1.8:2024` and paste the pairing token.

To print the token again:

```powershell
.\scripts\show-pairing-token.ps1
```

The settings screen writes `qwen`, `dashscope`, and `tavily` keys only to the computer-side ignored file `secrets.local.yaml`. The API returns configuration booleans only; it never returns plaintext keys.

## Preview In VSCode

Use Node 22 LTS `>=22.12`. From `app/`:

```powershell
npm install
npm run dev -- --host
```

Open the printed Vite URL in VSCode's browser preview. When `app/` is opened as the VSCode workspace, the same commands are available from `.vscode/tasks.json`.

## Build Android

Requirements:

- Node 22 LTS `>=22.12`
- JDK 21 with `JAVA_HOME` pointing to that JDK
- Android SDK command-line tools
- Android `platform-tools`, platform `35`, and build-tools for API 35

Build and sync:

```powershell
cd app
npm run build
npx cap sync android
npm run android:debug
```

The debug APK is written under `app/android/app/build/outputs/apk/debug/`. Debug builds allow cleartext LAN HTTP and Android WebView mixed content so the bundled `https://localhost` UI can reach the development server. Release builds disable cleartext traffic; use HTTPS before distributing a release.

If Android SDK packages are missing or `sdkmanager.bat` was only partially extracted, run `npm run android:prepare-sdk` once and review the Android SDK licenses interactively.

## Trusted LAN Warning

The server uses a command allow-list, but `LocalShellBackend` is not an isolated sandbox. Commands run with the current computer user's permissions. Use the server only on a trusted LAN and a development computer. Do not expose port `2024` to the public internet.

The launcher starts an outer FastAPI gateway on port `2024` and an official loopback-only LangGraph Agent Server on port `2025`. The inner `langgraph dev` process uses `--allow-blocking` because Deep Agents initialization reads local YAML, skills, and prompt files synchronously. This flag is limited to the trusted-LAN development launcher and is not a production deployment setting.

## Official References

- [Deep Agents frontend overview](https://docs.langchain.com/oss/python/deepagents/frontend/overview)
- [Sandbox frontend pattern](https://docs.langchain.com/oss/python/deepagents/frontend/sandbox)
- [Subagent streaming](https://docs.langchain.com/oss/python/deepagents/frontend/subagent-streaming)
- [Todo list](https://docs.langchain.com/oss/python/deepagents/frontend/todo-list)
- [LangGraph authentication](https://docs.langchain.com/langsmith/auth)
- [Capacitor Android](https://capacitorjs.com/docs/android)
- [Capacitor Camera](https://capacitorjs.com/docs/apis/camera)
