<script setup lang="ts">
import { Image as ImageIcon, ListChecks, PanelRight, Terminal } from "lucide-vue-next";
import type {
  ActiveInspector,
  ArtifactItem,
  FilePayload,
  TodoItem,
  ToolEventView,
  WorkspaceEntry,
} from "../types";
import ArtifactsPanel from "./ArtifactsPanel.vue";
import FlowPanel from "./FlowPanel.vue";
import PlanPanel from "./PlanPanel.vue";
import SandboxPanel from "./SandboxPanel.vue";

defineProps<{
  activeInspector: ActiveInspector;
  collapsed: Record<string, boolean>;
  todos: TodoItem[];
  todoStats: {
    total: number;
    completed: number;
    active: number;
    percent: number;
  };
  toolEvents: ToolEventView[];
  subagents: any[];
  subagentDescriptions: Map<string, string>;
  artifacts: ArtifactItem[];
  artifactError: string | null;
  selectedArtifact: ArtifactItem | null;
  selectedArtifactKind: string;
  artifactPayload: FilePayload | null;
  workspaceEntries: WorkspaceEntry[];
  workspaceError: string | null;
  selectedFile: FilePayload | null;
  fileDiff: Array<{ kind: "same" | "add" | "remove"; text: string }>;
}>();

const emit = defineEmits<{
  "update:activeInspector": [value: ActiveInspector];
  toggleCollapsed: [key: string];
  startInspectorResize: [event: PointerEvent];
  loadArtifacts: [];
  selectArtifact: [item: ArtifactItem];
  loadWorkspace: [];
  openWorkspaceFile: [entry: WorkspaceEntry];
}>();
</script>

<template>
  <div
    class="inspector-resizer"
    role="separator"
    aria-orientation="vertical"
    title="拖动调整右侧面板宽度"
    @pointerdown="emit('startInspectorResize', $event)"
  />

  <aside class="inspector">
    <nav class="inspector-tabs" aria-label="Inspector panels">
      <button
        :class="{ active: activeInspector === 'plan' }"
        type="button"
        title="任务规划"
        @click="emit('update:activeInspector', 'plan')"
      >
        <ListChecks :size="17" />
      </button>
      <button
        :class="{ active: activeInspector === 'flow' }"
        type="button"
        title="执行流"
        @click="emit('update:activeInspector', 'flow')"
      >
        <PanelRight :size="17" />
      </button>
      <button
        :class="{ active: activeInspector === 'artifacts' }"
        type="button"
        title="中间产物"
        @click="emit('update:activeInspector', 'artifacts')"
      >
        <ImageIcon :size="17" />
      </button>
      <button
        :class="{ active: activeInspector === 'sandbox' }"
        type="button"
        title="代码沙箱"
        @click="emit('update:activeInspector', 'sandbox')"
      >
        <Terminal :size="17" />
      </button>
    </nav>

    <PlanPanel
      v-show="activeInspector === 'plan'"
      :collapsed="collapsed.plan"
      :todos="todos"
      :todo-stats="todoStats"
      @toggle="emit('toggleCollapsed', 'plan')"
    />

    <FlowPanel
      v-show="activeInspector === 'flow'"
      :collapsed="collapsed.flow"
      :tool-events="toolEvents"
      :subagents="subagents"
      :subagent-descriptions="subagentDescriptions"
      @toggle="emit('toggleCollapsed', 'flow')"
    />

    <ArtifactsPanel
      v-show="activeInspector === 'artifacts'"
      :artifacts="artifacts"
      :artifact-error="artifactError"
      :selected-artifact="selectedArtifact"
      :selected-artifact-kind="selectedArtifactKind"
      :artifact-payload="artifactPayload"
      @load-artifacts="emit('loadArtifacts')"
      @select-artifact="emit('selectArtifact', $event)"
    />

    <SandboxPanel
      v-show="activeInspector === 'sandbox'"
      :workspace-entries="workspaceEntries"
      :workspace-error="workspaceError"
      :selected-file="selectedFile"
      :file-diff="fileDiff"
      @load-workspace="emit('loadWorkspace')"
      @open-workspace-file="emit('openWorkspaceFile', $event)"
    />
  </aside>
</template>
