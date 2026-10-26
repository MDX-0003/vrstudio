<script setup lang="ts">
import { computed } from 'vue'
import FileOpenLink from '@/components/common/FileOpenLink.vue'
import type { KeyframeRound } from '@/api/keyframe'

const props = defineProps<{
  round: KeyframeRound
  pickedIndex: number | null
  disabled: boolean
}>()

const emit = defineEmits<{ pick: [index: number] }>()

const label = computed(() => props.round.stage || '未知阶段')
</script>

<template>
  <div class="round-block">
    <div class="round-head">
      <span class="mono">{{ round.round }}</span>
      <el-tag size="small" effect="light">{{ label }}</el-tag>
    </div>
    <div class="candidate-grid">
      <div v-for="item in round.results" :key="item.index" class="candidate-wrap">
        <button
          type="button"
          class="candidate-card"
          :class="{ picked: pickedIndex === item.index }"
          :disabled="disabled"
          @click="emit('pick', item.index)"
        >
          <img :src="item.url" :alt="`候选 ${item.index}`" />
          <span v-if="pickedIndex === item.index" class="picked-mark">已定稿</span>
        </button>
        <FileOpenLink :file="item" label="打开候选原图" class="card-open" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.round-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}

.candidate-wrap {
  position: relative;
}

.candidate-card {
  position: relative;
  width: 100%;
  padding: 0;
  overflow: hidden;
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
  cursor: pointer;
}

.candidate-card img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.candidate-card:hover {
  border-color: var(--color-primary);
}

.candidate-card.picked {
  border-color: var(--color-success);
}

.picked-mark {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-success);
  color: #fff;
  font-size: 11px;
}

.card-open {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.7);
  color: #fff;
}

@media (max-width: 1280px) {
  .candidate-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
