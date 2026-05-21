<script setup lang="ts">
import AppSidebar from "./components/AppSidebar.vue";
import ConversationPane from "./components/ConversationPane.vue";
import InspectorPanel from "./components/InspectorPanel.vue";
import { useContentBuilderConsole } from "./composables/useContentBuilderConsole";

const {
  activeThreadId,
  sessions,
  stream,
  streamError,
  draft,
  pendingImages,
  inspectorWidth,
  manifest,
  manifestError,
  artifacts,
  artifactError,
  selectedArtifact,
  artifactPayload,
  workspaceEntries,
  workspaceError,
  selectedFile,
  activeInspector,
  collapsed,
  chatMessages,
  todos,
  todoStats,
  subagents,
  artifactDrafts,
  subagentByName,
  toolEvents,
  fileDiff,
  selectedArtifactKind,
  callsForMessage,
  subagentsForMessage,
  artifactDraftsForMessage,
  syncCollapsed,
  refreshSideData,
  loadArtifacts,
  loadWorkspace,
  openWorkspaceFile,
  selectArtifact,
  handleFileInput,
  removePendingImage,
  submitMessage,
  startNewConversation,
  selectSession,
  stopStream,
  startInspectorResize,
} = useContentBuilderConsole();
</script>

<template>
  <div class="app-shell" :style="{ '--inspector-width': `${inspectorWidth}px` }">
    <AppSidebar
      :sessions="sessions"
      :active-thread-id="activeThreadId"
      :manifest="manifest"
      :manifest-error="manifestError"
      :collapsed="collapsed"
      @new-conversation="startNewConversation"
      @select-session="selectSession"
      @refresh-side-data="refreshSideData"
      @sync-collapsed="syncCollapsed"
    />

    <ConversationPane
      v-model:draft="draft"
      :agent-name="manifest?.agent.name || 'content_writer'"
      :is-loading="stream.isLoading.value"
      :stream-thread-id="activeThreadId"
      :active-thread-id="activeThreadId"
      :chat-messages="chatMessages"
      :stream-error="streamError"
      :pending-images="pendingImages"
      :subagent-descriptions="subagentByName"
      :calls-for-message="callsForMessage"
      :subagents-for-message="subagentsForMessage"
      :artifact-drafts-for-message="artifactDraftsForMessage"
      @submit-message="submitMessage"
      @stop-stream="stopStream"
      @handle-file-input="handleFileInput"
      @remove-pending-image="removePendingImage"
    />

    <InspectorPanel
      v-model:active-inspector="activeInspector"
      :collapsed="collapsed"
      :todos="todos"
      :todo-stats="todoStats"
      :tool-events="toolEvents"
      :subagents="subagents"
      :artifact-drafts="artifactDrafts"
      :subagent-descriptions="subagentByName"
      :artifacts="artifacts"
      :artifact-error="artifactError"
      :selected-artifact="selectedArtifact"
      :selected-artifact-kind="selectedArtifactKind"
      :artifact-payload="artifactPayload"
      :workspace-entries="workspaceEntries"
      :workspace-error="workspaceError"
      :selected-file="selectedFile"
      :file-diff="fileDiff"
      @toggle-collapsed="(key) => (collapsed[key] = !collapsed[key])"
      @start-inspector-resize="startInspectorResize"
      @load-artifacts="loadArtifacts"
      @select-artifact="selectArtifact"
      @load-workspace="loadWorkspace"
      @open-workspace-file="openWorkspaceFile"
    />
  </div>
</template>
