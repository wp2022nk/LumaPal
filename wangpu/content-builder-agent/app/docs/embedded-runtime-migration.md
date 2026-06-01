# Embedded Deep Agents Runtime Migration

V1 deliberately keeps the Android APK thin. Python, FunASR models, PyTorch, Qwen TTS orchestration, and the trusted-LAN shell remain on the development computer.

Phase two should replace only `LanAgentServerRuntime` with `EmbeddedDeepAgentsRuntime` while keeping the UI components unchanged.

## Target Runtime

- Use the official TypeScript `createDeepAgent()` API.
- Store thread artifacts in the Android app's private files directory.
- Store provider keys and pairing replacement secrets with Android Keystore.
- Replace desktop shell execution with a small mobile-safe command surface.
- Keep thread IDs as the namespace boundary for messages, artifacts, uploads, and sandbox views.
- Preserve the `AgentRuntime` methods in `src/runtime/types.ts`.

## Current Hook Compatibility Note

The official Deep Agents frontend docs show `useStream` from `@langchain/react` with `filterSubagentMessages`. At the time of this implementation, published `@langchain/react@1.0.10` exposes the newer v2 projection API while the Python Agent Server workflow still needs the compatibility behavior. V1 therefore imports the official `useStream` compatibility hook from `@langchain/langgraph-sdk/react`. Re-evaluate this import when the embedded TypeScript runtime is introduced.
