<script setup lang="ts">
import { Code2, File, FileImage, FileText, RefreshCw } from "lucide-vue-next";
import { assetUrl } from "../api";
import type { ArtifactItem, FilePayload } from "../types";
import { humanSize, renderMarkdown } from "../utils";

defineProps<{
  artifacts: ArtifactItem[];
  artifactError: string | null;
  selectedArtifact: ArtifactItem | null;
  selectedArtifactKind: string;
  artifactPayload: FilePayload | null;
}>();

const emit = defineEmits<{
  loadArtifacts: [];
  selectArtifact: [item: ArtifactItem];
}>();

function fileIcon(entry: ArtifactItem) {
  if (entry.kind === "image") return FileImage;
  if (entry.kind === "markdown" || entry.kind === "text") return FileText;
  if (entry.kind === "code") return Code2;
  return File;
}
</script>

<template>
  <section class="panel">
    <header class="panel-header">
      <div>
        <h3>中间产物</h3>
        <p>{{ artifacts.length }} 个文件</p>
      </div>
      <button class="icon-button" type="button" @click="emit('loadArtifacts')">
        <RefreshCw :size="15" />
      </button>
    </header>
    <div class="panel-content split-panel">
      <div class="asset-list">
        <button
          v-for="item in artifacts"
          :key="item.path"
          type="button"
          class="asset-row"
          :class="{ active: selectedArtifact?.path === item.path }"
          @click="emit('selectArtifact', item)"
        >
          <component :is="fileIcon(item)" :size="16" />
          <span>{{ item.name }}</span>
          <small>{{ item.kind }} · {{ humanSize(item.size) }}</small>
        </button>
        <p v-if="artifactError" class="error-text">{{ artifactError }}</p>
        <p v-if="artifacts.length === 0" class="muted">
          agent 生成博客、研究、故事或分析文件后，会自动出现在这里。
        </p>
      </div>
      <div class="preview-surface">
        <template v-if="selectedArtifact">
          <img
            v-if="selectedArtifactKind === 'image'"
            :src="assetUrl(selectedArtifact.path)"
            :alt="selectedArtifact.name"
          />
          <iframe
            v-else-if="selectedArtifactKind === 'pdf'"
            :src="assetUrl(selectedArtifact.path)"
            title="PDF preview"
          />
          <div
            v-else-if="selectedArtifactKind === 'markdown' && artifactPayload"
            class="markdown-body"
            v-html="renderMarkdown(artifactPayload.content)"
          />
          <pre v-else-if="artifactPayload">{{ artifactPayload.content }}</pre>
          <a
            v-else
            class="download-link"
            :href="assetUrl(selectedArtifact.path)"
            target="_blank"
            rel="noreferrer"
          >
            打开或下载原文件
          </a>
        </template>
        <p v-else class="muted">选择一个产物查看预览。</p>
      </div>
    </div>
  </section>
</template>
