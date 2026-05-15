<script setup lang="ts">
import { Braces, Code2, File, FileImage, FileText, Folder, Play, RefreshCw } from "lucide-vue-next";
import type { ArtifactItem, FilePayload, WorkspaceEntry } from "../types";
import { humanSize, pathDepth } from "../utils";

defineProps<{
  workspaceEntries: WorkspaceEntry[];
  workspaceError: string | null;
  selectedFile: FilePayload | null;
  fileDiff: Array<{ kind: "same" | "add" | "remove"; text: string }>;
}>();

const emit = defineEmits<{
  loadWorkspace: [];
  openWorkspaceFile: [entry: WorkspaceEntry];
}>();

function fileIcon(entry: WorkspaceEntry | ArtifactItem) {
  if (entry.type === "directory") return Folder;
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
        <h3>代码沙箱</h3>
        <p>{{ workspaceEntries.length }} 个可见条目</p>
      </div>
      <button class="icon-button" type="button" @click="emit('loadWorkspace')">
        <RefreshCw :size="15" />
      </button>
    </header>
    <div class="panel-content split-panel">
      <div class="file-tree">
        <button
          v-for="entry in workspaceEntries"
          :key="entry.path"
          type="button"
          :class="{ active: selectedFile?.path === entry.path }"
          :style="{ paddingLeft: `${12 + pathDepth(entry.path) * 14}px` }"
          @click="emit('openWorkspaceFile', entry)"
        >
          <component :is="fileIcon(entry)" :size="15" />
          <span>{{ entry.name }}</span>
        </button>
        <p v-if="workspaceError" class="error-text">{{ workspaceError }}</p>
      </div>
      <div class="code-viewer">
        <template v-if="selectedFile">
          <header>
            <strong>{{ selectedFile.path }}</strong>
            <span>{{ selectedFile.mime }} · {{ humanSize(selectedFile.size) }}</span>
          </header>
          <pre v-if="selectedFile.encoding === 'text'">{{ selectedFile.content }}</pre>
          <div v-else class="binary-note">
            <Braces :size="20" /> 二进制文件以 base64 读取，可通过产物面板打开。
          </div>
          <details v-if="selectedFile.encoding === 'text'" class="diff-box">
            <summary><Play :size="14" /> 与首次打开版本对比</summary>
            <pre><template v-for="(row, index) in fileDiff" :key="index"><span :class="row.kind">{{ row.kind === 'add' ? '+ ' : row.kind === 'remove' ? '- ' : '  ' }}{{ row.text }}</span>
</template></pre>
          </details>
        </template>
        <p v-else class="muted">选择文件查看代码、文本或执行生成的内容。</p>
      </div>
    </div>
  </section>
</template>
