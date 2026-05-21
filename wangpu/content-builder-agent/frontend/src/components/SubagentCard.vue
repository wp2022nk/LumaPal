<script setup lang="ts">
import { Bot } from "lucide-vue-next";
import { statusText, summarizeResult } from "../utils";

const props = defineProps<{
  subagent: any;
  description?: string;
  timeline?: boolean;
}>();

function displayPayload(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
</script>

<template>
  <details
    :class="timeline ? 'timeline-item subagent-timeline' : 'subagent-card'"
    :open="!timeline"
  >
    <summary>
      <span>
        <Bot :size="timeline ? 15 : 16" />
        {{
          props.subagent.name ||
          props.subagent.toolCall?.args?.subagent_type ||
          props.subagent.id
        }}
      </span>
      <span v-if="!timeline">{{ statusText(props.subagent.status) }}</span>
      <small v-else>{{ statusText(props.subagent.status) }}</small>
    </summary>
    <p>
      {{
        props.subagent.taskInput ||
        props.subagent.toolCall?.args?.description ||
        props.description ||
        "子智能体任务"
      }}
    </p>
    <div v-if="props.subagent.steps?.length" class="subagent-steps">
      <div v-for="step in props.subagent.steps" :key="step.id" class="subagent-step">
        <span>{{ step.name }}</span>
        <small>{{ statusText(step.status) }}</small>
        <pre v-if="step.args && !timeline">{{ displayPayload(step.args) }}</pre>
        <p v-if="step.result">{{ summarizeResult(step.result) }}</p>
      </div>
    </div>
    <pre v-if="props.subagent.output || props.subagent.result || props.subagent.error">{{
      displayPayload(props.subagent.output || props.subagent.result || props.subagent.error)
    }}</pre>
  </details>
</template>
