<script setup lang="ts">
import { CheckCircle2, Loader2, XCircle } from "lucide-vue-next";
import type { ToolEventView } from "../types";
import { schemaFields, statusText, summarizeResult, toolStatus } from "../utils";

defineProps<{
  event: ToolEventView;
  timeline?: boolean;
}>();
</script>

<template>
  <details :class="timeline ? 'timeline-item' : 'tool-card'" :open="!timeline">
    <summary>
      <span class="tool-title">
        <Loader2 v-if="toolStatus(event) === 'running'" class="spin" :size="16" />
        <XCircle v-else-if="toolStatus(event) === 'error'" :size="16" />
        <CheckCircle2 v-else :size="16" />
        {{ event.call.name }}
      </span>
      <span v-if="!timeline" class="tool-state">{{ statusText(toolStatus(event)) }}</span>
      <small v-else>{{ statusText(toolStatus(event)) }}</small>
    </summary>
    <p v-if="event.tool?.description" class="tool-description">
      {{ event.tool.description }}
    </p>
    <div v-if="!timeline && schemaFields(event.tool).length" class="schema-table">
      <div v-for="field in schemaFields(event.tool)" :key="field.name">
        <strong>{{ field.name }}</strong>
        <span>{{ field.type }}</span>
        <em>{{ field.required ? "必填" : "可选" }}</em>
      </div>
    </div>
    <pre>{{ JSON.stringify(event.call.args, null, 2) }}</pre>
    <p v-if="event.result" class="result-preview">
      {{ summarizeResult(event.result.content) }}
    </p>
  </details>
</template>
