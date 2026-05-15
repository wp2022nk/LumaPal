<script setup lang="ts">
import { CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3 } from "lucide-vue-next";
import type { TodoItem } from "../types";
import { statusText, todoLabel } from "../utils";

defineProps<{
  collapsed: boolean;
  todos: TodoItem[];
  todoStats: {
    total: number;
    completed: number;
    active: number;
    percent: number;
  };
}>();

const emit = defineEmits<{
  toggle: [];
}>();
</script>

<template>
  <section class="panel">
    <header class="panel-header">
      <div>
        <h3>任务规划</h3>
        <p>{{ todoStats.completed }}/{{ todoStats.total }} 完成</p>
      </div>
      <button class="icon-button" type="button" @click="emit('toggle')">
        <ChevronRight v-if="collapsed" :size="16" />
        <ChevronDown v-else :size="16" />
      </button>
    </header>
    <div v-show="!collapsed" class="panel-content">
      <div class="progress-track">
        <span :style="{ width: `${todoStats.percent}%` }" />
      </div>
      <ol v-if="todos.length" class="todo-list">
        <li
          v-for="(todo, index) in todos"
          :key="`${todoLabel(todo)}-${index}`"
          :class="todo.status"
        >
          <CheckCircle2 v-if="todo.status === 'completed'" :size="16" />
          <Clock3 v-else-if="todo.status === 'in_progress'" :size="16" />
          <Circle v-else :size="16" />
          <div>
            <strong>{{ todoLabel(todo) }}</strong>
            <span>{{ statusText(todo.status) }}</span>
          </div>
        </li>
      </ol>
      <p v-else class="muted">
        任务开始后，Deep Agents 的 todos 会实时出现在这里。
      </p>
    </div>
  </section>
</template>
