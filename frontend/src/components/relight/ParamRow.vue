<script setup lang="ts">
import { computed } from 'vue'
import { CLIP_DESC } from '@/constants/relightParams'
import type { ParamDesc } from '@/constants/relightParams'

/**
 * 按描述表渲染一行参数控件。默认卡与区域卡共用这个组件：
 * 默认卡用它给全局值，区域卡用它给覆盖值；两者唯一的差别是区域卡多一个
 * 「默认 / 覆盖」开关。
 */
const props = defineProps<{
  desc: ParamDesc
  /** 数字参数：数值或 null；软裁切：{knee,max} 或 null。 */
  value: number | { knee: number; max: number } | null
  scope: 'default' | 'region'
  /** 数字参数在「默认」状态下显示的全局值。 */
  fallback: number
}>()

const emit = defineEmits<{
  'update:value': [value: number | { knee: number; max: number } | null]
}>()

const isRegion = computed(() => props.scope === 'region')
const followsDefault = computed(() => isRegion.value && props.value === null)
const numberValue = computed(() => {
  if (props.desc.kind !== 'number') return 0
  return typeof props.value === 'number' ? props.value : props.fallback
})

function clipParts(): { knee: number; max: number } {
  const value = props.value as { knee: number; max: number } | null
  return {
    knee: value?.knee ?? CLIP_DESC.knee.fallback,
    max: value?.max ?? CLIP_DESC.max.fallback,
  }
}

const clipOn = computed(() => (props.value as unknown) !== null)

function onNumber(event: Event): void {
  const target = event.target as HTMLInputElement
  emit('update:value', Number(target.value))
}

function toggleNumber(): void {
  emit('update:value', followsDefault.value ? props.fallback : null)
}

function toggleClip(): void {
  if (clipOn.value) emit('update:value', null)
  else emit('update:value', { knee: CLIP_DESC.knee.fallback, max: CLIP_DESC.max.fallback })
}

function patchClip(event: Event, key: 'knee' | 'max'): void {
  const target = event.target as HTMLInputElement
  emit('update:value', { ...clipParts(), [key]: Number(target.value) })
}
</script>

<template>
  <div v-if="desc.kind === 'number'" class="param-line" :class="{ off: followsDefault }">
    <span class="label">{{ desc.label }}</span>
    <input
      type="range"
      data-control="range"
      :min="desc.min"
      :max="desc.max"
      :step="desc.step"
      :value="numberValue"
      :disabled="followsDefault"
      @input="onNumber"
    />
    <span class="val" data-control="value">{{ followsDefault ? '默认' : numberValue.toFixed(2) }}</span>
    <button
      v-if="isRegion"
      class="switch"
      data-control="override"
      :class="{ on: !followsDefault }"
      :aria-label="followsDefault ? '改为覆盖' : '改回默认'"
      @click="toggleNumber"
    ></button>
  </div>

  <div v-else class="param-line tight">
    <span class="label">{{ desc.label }}</span>
    <span class="cell">
      <input
        v-if="!isRegion"
        type="checkbox"
        data-control="enabled"
        :checked="clipOn"
        :aria-label="desc.label"
        @change="toggleClip"
      />
      <button
        v-else
        class="switch"
        data-control="enabled"
        data-override="true"
        :class="{ on: clipOn }"
        :aria-label="clipOn ? '改回默认' : '改为覆盖'"
        @click="toggleClip"
      ></button>
      <span v-if="!clipOn" class="hint">{{ isRegion ? '默认' : '关' }}</span>
      <template v-else>
        <span class="hint">拐点</span>
        <input
          type="number"
          data-control="knee"
          :min="desc.knee.min"
          :max="desc.knee.max"
          :step="desc.knee.step"
          :value="clipParts().knee"
          @input="patchClip($event, 'knee')"
        />
        <span class="hint">上限</span>
        <input
          type="number"
          data-control="max"
          :min="desc.max.min"
          :max="desc.max.max"
          :step="desc.max.step"
          :value="clipParts().max"
          @input="patchClip($event, 'max')"
        />
      </template>
    </span>
  </div>
</template>

<style scoped>
.param-line {
  display: grid;
  grid-template-columns: 76px 1fr 52px 58px;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.param-line.tight {
  grid-template-columns: 76px 1fr;
}

.param-line.off input[type='range'] {
  opacity: 0.35;
}

.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.val {
  text-align: right;
  font-family: var(--font-mono);
  color: var(--color-text);
}

.cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
}

.cell input[type='number'] {
  width: 62px;
  padding: 2px 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.hint {
  font-size: 11px;
  color: var(--color-text-faint);
}

.switch {
  position: relative;
  width: 30px;
  height: 17px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: var(--color-border-strong);
  cursor: pointer;
}

.switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  transition: 150ms;
}

.switch.on {
  background: var(--color-primary);
}

.switch.on::after {
  left: 15px;
}
</style>
