<script setup lang="ts">
import { FilePenLine, Loader2 } from "lucide-vue-next";
import type { ArtifactDraftView } from "../types";

defineProps<{
  draft: ArtifactDraftView;
  timeline?: boolean;
}>();
</script>

<template>
  <details :class="timeline ? 'timeline-item artifact-draft-card' : 'artifact-draft-card'" open>
    <summary>
      <span>
        <Loader2 v-if="draft.status === 'streaming'" class="spin" :size="16" />
        <FilePenLine v-else :size="16" />
        {{ draft.path || draft.toolName }}
      </span>
      <small>{{ draft.status === "streaming" ? "生成中" : "完成" }}</small>
    </summary>
    <pre v-if="draft.content">{{ draft.content }}</pre>
    <pre v-else>{{ draft.argsText }}</pre>
  </details>
</template>
