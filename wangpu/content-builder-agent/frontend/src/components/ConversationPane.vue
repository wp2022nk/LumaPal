<script setup lang="ts">
import { Bot, Send, ShieldCheck, Upload, X } from "lucide-vue-next";
import { nextTick, ref, watch } from "vue";
import type {
  ApprovalDecision,
  ApprovalMode,
  InterruptRequest,
  PendingImage,
  ToolEventView,
} from "../types";
import { humanSize, messageId, shortId } from "../utils";
import MessageItem from "./MessageItem.vue";

const props = defineProps<{
  agentName: string;
  isLoading: boolean;
  streamThreadId: string | null | undefined;
  activeThreadId: string | null;
  chatMessages: any[];
  streamError: string | null;
  draft: string;
  pendingImages: PendingImage[];
  interruptRequest: InterruptRequest | null;
  approvalMode: ApprovalMode;
  approvalArgsText: string;
  rejectionReason: string;
  responseMessage: string;
  subagentDescriptions: Map<string, string>;
  callsForMessage: (message: any) => ToolEventView[];
  subagentsForMessage: (message: any) => any[];
}>();

const emit = defineEmits<{
  "update:draft": [value: string];
  "update:approvalMode": [value: ApprovalMode];
  "update:approvalArgsText": [value: string];
  "update:rejectionReason": [value: string];
  "update:responseMessage": [value: string];
  submitMessage: [];
  stopStream: [];
  handleFileInput: [event: Event];
  removePendingImage: [id: string];
  sendApproval: [decision: ApprovalDecision];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const messagePane = ref<HTMLElement | null>(null);
const messagesEnd = ref<HTMLElement | null>(null);

function shouldStickToBottom(): boolean {
  const pane = messagePane.value;
  if (!pane) return true;
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
}

function allowedDecision(decision: string): boolean {
  return props.interruptRequest?.review.allowedDecisions.includes(decision) ?? false;
}

function toggleApprovalMode(mode: ApprovalMode) {
  emit("update:approvalMode", props.approvalMode === mode ? "review" : mode);
}

watch(
  () => props.chatMessages,
  async () => {
    const stickToBottom = shouldStickToBottom();
    await nextTick();
    if (stickToBottom) {
      messagesEnd.value?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  },
  { deep: true },
);
</script>

<template>
  <main class="conversation">
    <header class="topbar">
      <div>
        <div class="eyebrow">LangGraph Agent Server</div>
        <h2>{{ agentName }}</h2>
      </div>
      <div class="runtime-strip">
        <span class="status-pill" :class="{ live: isLoading }">
          <span class="status-dot" />
          {{ isLoading ? "流式执行中" : "待命" }}
        </span>
        <span class="status-pill">
          Thread {{ shortId(streamThreadId || activeThreadId) }}
        </span>
        <button v-if="isLoading" class="ghost-button" type="button" @click="emit('stopStream')">
          停止接收
        </button>
      </div>
    </header>

    <section ref="messagePane" class="message-pane">
      <div v-if="chatMessages.length === 0" class="empty-state">
        <div class="empty-mark"><Bot :size="30" /></div>
        <h3>把任务、素材或草稿丢进来</h3>
        <p>适合推进博客、故事、研究分析、图像素材和代码执行相关任务。</p>
      </div>

      <MessageItem
        v-for="(message, index) in chatMessages"
        :key="messageId(message, index)"
        :message="message"
        :index="index"
        :tool-events="callsForMessage(message)"
        :subagents="subagentsForMessage(message)"
        :subagent-descriptions="subagentDescriptions"
      />

      <section v-if="interruptRequest" class="approval-card">
        <header>
          <ShieldCheck :size="20" />
          <div>
            <h3>需要人工审批</h3>
            <p>
              {{
                interruptRequest.action.description ||
                `智能体请求执行 ${interruptRequest.action.action}`
              }}
            </p>
          </div>
        </header>
        <pre>{{ JSON.stringify(interruptRequest.action.args, null, 2) }}</pre>
        <div class="approval-tabs">
          <button
            v-if="allowedDecision('approve')"
            class="approve"
            type="button"
            @click="emit('sendApproval', 'approve')"
          >
            批准
          </button>
          <button v-if="allowedDecision('edit')" type="button" @click="toggleApprovalMode('edit')">
            编辑参数
          </button>
          <button
            v-if="allowedDecision('reject')"
            type="button"
            @click="toggleApprovalMode('reject')"
          >
            拒绝
          </button>
          <button
            v-if="allowedDecision('respond')"
            type="button"
            @click="toggleApprovalMode('respond')"
          >
            直接回复
          </button>
        </div>
        <div v-if="approvalMode === 'edit'" class="approval-editor">
          <textarea
            :value="approvalArgsText"
            spellcheck="false"
            @input="emit('update:approvalArgsText', ($event.target as HTMLTextAreaElement).value)"
          />
          <button type="button" @click="emit('sendApproval', 'edit')">
            提交编辑后参数
          </button>
        </div>
        <div v-if="approvalMode === 'reject'" class="approval-editor">
          <textarea
            :value="rejectionReason"
            placeholder="拒绝原因"
            @input="emit('update:rejectionReason', ($event.target as HTMLTextAreaElement).value)"
          />
          <button type="button" @click="emit('sendApproval', 'reject')">
            提交拒绝
          </button>
        </div>
        <div v-if="approvalMode === 'respond'" class="approval-editor">
          <textarea
            :value="responseMessage"
            placeholder="给工具返回的人工回复"
            @input="emit('update:responseMessage', ($event.target as HTMLTextAreaElement).value)"
          />
          <button type="button" @click="emit('sendApproval', 'respond')">
            发送回复
          </button>
        </div>
      </section>
      <div ref="messagesEnd" />
    </section>

    <footer class="composer">
      <div v-if="streamError" class="error-banner">{{ streamError }}</div>
      <div v-if="pendingImages.length" class="pending-images">
        <div v-for="image in pendingImages" :key="image.id" class="pending-image">
          <img :src="image.dataUrl" :alt="image.name" />
          <span>{{ image.name }} · {{ humanSize(image.size) }}</span>
          <button type="button" @click="emit('removePendingImage', image.id)">
            <X :size="14" />
          </button>
        </div>
      </div>
      <form class="composer-row" @submit.prevent="emit('submitMessage')">
        <input
          ref="fileInput"
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          multiple
          hidden
          @change="emit('handleFileInput', $event)"
        />
        <button
          class="icon-button"
          type="button"
          title="上传图片"
          @click="fileInput?.click()"
        >
          <Upload :size="18" />
        </button>
        <textarea
          :value="draft"
          rows="1"
          placeholder="输入任务，或上传图片一起发送..."
          @input="emit('update:draft', ($event.target as HTMLTextAreaElement).value)"
          @keydown.enter.exact.prevent="emit('submitMessage')"
        />
        <button
          class="send-button"
          type="submit"
          :disabled="isLoading && !draft.trim() && pendingImages.length === 0"
        >
          <Send :size="18" />
        </button>
      </form>
    </footer>
  </main>
</template>
