<script setup lang="ts">
import { BookOpen, Bot, Plus, RefreshCw, Sparkles, Wrench } from "lucide-vue-next";
import type { AgentManifest, SessionRecord } from "../types";
import { shortDate, shortId } from "../utils";

defineProps<{
  sessions: SessionRecord[];
  activeThreadId: string | null;
  manifest: AgentManifest | null;
  manifestError: string | null;
  collapsed: Record<string, boolean>;
}>();

const emit = defineEmits<{
  newConversation: [];
  selectSession: [session: SessionRecord];
  refreshSideData: [];
  syncCollapsed: [key: string, event: Event];
}>();
</script>

<template>
  <aside class="sidebar">
    <header class="brand-block">
      <div class="brand-mark">
        <Sparkles :size="20" />
      </div>
      <div>
        <h1>Content Builder</h1>
        <p>Deep Agents Vue Console</p>
      </div>
    </header>

    <button class="primary-action" type="button" @click="emit('newConversation')">
      <Plus :size="17" />
      新建对话
    </button>

    <section class="side-section">
      <div class="section-title">
        <span>会话</span>
        <span>{{ sessions.length }}</span>
      </div>
      <div class="session-list">
        <button
          v-for="session in sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: session.id === activeThreadId }"
          type="button"
          @click="emit('selectSession', session)"
        >
          <span>{{ session.title }}</span>
          <small>{{ shortDate(session.updatedAt) }} · {{ shortId(session.id) }}</small>
        </button>
        <p v-if="sessions.length === 0" class="muted">
          发送第一条消息后会自动创建 thread。
        </p>
      </div>
    </section>

    <section class="side-section manifest-card">
      <div class="section-title">
        <span>Manifest</span>
        <button
          class="icon-button"
          type="button"
          title="刷新 manifest"
          @click="emit('refreshSideData')"
        >
          <RefreshCw :size="15" />
        </button>
      </div>
      <p v-if="manifestError" class="error-text">{{ manifestError }}</p>
      <template v-else-if="manifest">
        <dl class="manifest-meta">
          <div>
            <dt>Assistant</dt>
            <dd>{{ manifest.agent.assistant_id }}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{{ manifest.agent.model }}</dd>
          </div>
          <div>
            <dt>Sandbox</dt>
            <dd>{{ manifest.backend.sandbox_python || manifest.backend.type }}</dd>
          </div>
        </dl>

        <details
          :open="!collapsed.manifestTools"
          @toggle="emit('syncCollapsed', 'manifestTools', $event)"
        >
          <summary>
            <Wrench :size="15" /> 工具 {{ manifest.tools.length }}
          </summary>
          <div class="chip-list">
            <span
              v-for="tool in manifest.tools"
              :key="tool.name"
              class="chip"
              :class="{ warn: tool.requires_approval }"
            >
              {{ tool.name }}
            </span>
          </div>
        </details>

        <details
          :open="!collapsed.manifestSubagents"
          @toggle="emit('syncCollapsed', 'manifestSubagents', $event)"
        >
          <summary>
            <Bot :size="15" /> 子智能体 {{ manifest.subagents.length }}
          </summary>
          <div class="stack-list">
            <div
              v-for="subagent in manifest.subagents"
              :key="subagent.name"
              class="mini-row"
            >
              <strong>{{ subagent.name }}</strong>
              <span>{{ subagent.description }}</span>
            </div>
          </div>
        </details>

        <details
          :open="!collapsed.manifestSkills"
          @toggle="emit('syncCollapsed', 'manifestSkills', $event)"
        >
          <summary>
            <BookOpen :size="15" /> Skills {{ manifest.skills.length }}
          </summary>
          <div class="stack-list">
            <div v-for="skill in manifest.skills" :key="skill.path" class="mini-row">
              <strong>{{ skill.title || skill.name }}</strong>
              <span>{{ skill.description || skill.path }}</span>
            </div>
          </div>
        </details>
      </template>
      <p v-else class="muted">正在读取后端 manifest...</p>
    </section>
  </aside>
</template>
