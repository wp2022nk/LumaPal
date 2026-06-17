# 童芯智造 Android Frontend

The app is a React + TypeScript + Vite frontend wrapped by Capacitor Android.

Useful commands:

```powershell
npm run dev -- --host
npm run test
npm run build
npx cap sync android
npm run android:debug
```

The frontend depends on the `AgentRuntime` contract in `src/runtime/types.ts`. V1 uses `LanAgentServerRuntime`; see `docs/embedded-runtime-migration.md` for the phase-two embedded-runtime boundary.

The published `@langchain/react@1.0.10` package is already moving to the v2 protocol. The Python Agent Server path currently uses the official same-repository `@langchain/langgraph-sdk/react` compatibility hook so it can retain `filterSubagentMessages`, `reconnectOnMount`, `streamSubgraphs`, and `getSubagentsByMessage()` exactly as required by the Deep Agents frontend guide.
