<script setup lang="ts">
import { Bot } from "lucide-vue-next";
import type { ToolEventView } from "../types";
import {
  messageId,
  messageImages,
  messageText,
  renderMarkdown,
  roleOf,
} from "../utils";
import SubagentCard from "./SubagentCard.vue";
import ToolCallCard from "./ToolCallCard.vue";

defineProps<{
  message: any;
  index: number;
  toolEvents: ToolEventView[];
  subagents: any[];
  subagentDescriptions: Map<string, string>;
}>();
</script>

<template>
  <article
    class="message"
    :class="roleOf(message)"
    :data-message-id="messageId(message, index)"
  >
    <div class="avatar">
      <Bot v-if="['ai', 'assistant'].includes(roleOf(message))" :size="18" />
      <span v-else>你</span>
    </div>
    <div class="message-body">
      <div class="message-meta">
        <strong>{{ ["ai", "assistant"].includes(roleOf(message)) ? "智能体" : "用户" }}</strong>
        <span>{{ roleOf(message) }}</span>
      </div>
      <div
        v-if="messageText(message)"
        class="markdown-body"
        v-html="renderMarkdown(messageText(message))"
      />
      <div v-if="messageImages(message).length" class="image-grid">
        <img
          v-for="image in messageImages(message)"
          :key="image"
          :src="image"
          alt="uploaded image"
        />
      </div>

      <div v-if="toolEvents.length" class="tool-card-list">
        <ToolCallCard v-for="event in toolEvents" :key="event.id" :event="event" />
      </div>

      <div v-if="subagents.length" class="subagent-card-list">
        <SubagentCard
          v-for="subagent in subagents"
          :key="subagent.id"
          :subagent="subagent"
          :description="subagentDescriptions.get(subagent.name)"
        />
      </div>
    </div>
  </article>
</template>
