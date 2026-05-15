<script setup lang="ts">
import { Bot, ChevronDown, ChevronRight, Wrench } from "lucide-vue-next";
import type { ToolEventView } from "../types";
import SubagentCard from "./SubagentCard.vue";
import ToolCallCard from "./ToolCallCard.vue";

defineProps<{
  collapsed: boolean;
  toolEvents: ToolEventView[];
  subagents: any[];
  subagentDescriptions: Map<string, string>;
}>();

const emit = defineEmits<{
  toggle: [];
}>();
</script>

<template>
  <section class="panel">
    <header class="panel-header">
      <div>
        <h3>执行流</h3>
        <p>{{ toolEvents.length }} 个工具调用 · {{ subagents.length }} 个子智能体</p>
      </div>
      <button class="icon-button" type="button" @click="emit('toggle')">
        <ChevronRight v-if="collapsed" :size="16" />
        <ChevronDown v-else :size="16" />
      </button>
    </header>
    <div v-show="!collapsed" class="panel-content timeline">
      <ToolCallCard
        v-for="event in toolEvents"
        :key="event.id"
        :event="event"
        timeline
      />

      <SubagentCard
        v-for="subagent in subagents"
        :key="subagent.id"
        :subagent="subagent"
        :description="subagentDescriptions.get(subagent.name)"
        timeline
      />

      <p v-if="toolEvents.length === 0 && subagents.length === 0" class="muted">
        工具调用和子智能体执行会随 stream 自动补进来。
      </p>
    </div>
  </section>
</template>
