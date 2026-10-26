import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/workflow', () => ({
  setViewport: vi.fn(),
  setViewportSize: vi.fn(),
  setInner: vi.fn(),
  nudgeViewport: vi.fn(),
  resetDraft: vi.fn(),
  saveDraft: vi.fn(),
  ensureProxy: vi.fn(),
  fetchProxy: vi.fn(),
}))
vi.mock('@/composables/useInnerDrag', async () => {
  const { ref } = await import('vue')
  return {
    useInnerDrag: () => ({ dragging: ref(false), rect: ref(null), begin: vi.fn() }),
  }
})

import { nudgeViewport } from '@/api/workflow'
import PickStep from '@/views/wizard/PickStep.vue'
import type { CaseInfo } from '@/types/case-info'

const ButtonStub = defineComponent({
  template: '<button type="button"><slot /></button>',
})

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const SliderStub = defineComponent({
  name: 'ElSliderStub',
  props: { modelValue: { type: Number, default: 0 }, min: Number, max: Number, step: Number },
  emits: ['update:modelValue'],
  template:
    '<input class="slider-stub" type="range" :value="modelValue" ' +
    ':min="min" :max="max" :step="step" ' +
    '@input="$emit(\'update:modelValue\', Number($event.target.value))" />',
})

const stubs = {
  'el-button': ButtonStub,
  'el-link': LinkStub,
  'el-select': true,
  'el-option': true,
  'el-input-number': true,
  'el-icon': true,
  'el-slider': SliderStub,
}

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    viewport: { yaw_deg: 0, pitch_deg: 0, fov_h_deg: 59, width: 320, height: 180 },
    inner: { x: 80, y: 45, width: 160, height: 90 },
    frames: { start: 0, end: 7 },
    fps: 30,
    frame_count: 8,
    source_url: '/video/source/p1',
    source_file: {
      name: 'src.mp4',
      path: 'E:/repo/sources/src.mp4',
      rel: 'sources/src.mp4',
      exists: true,
      mtime: null,
      size: 4096,
      open_url: '/file?root=sources&p=src.mp4',
    },
    previews: {},
    viewport_footprint: [],
    viewport_options: [],
    proxy: { state: 'ready', url: '/video/proxy/p1' },
    gates: {},
  } as unknown as CaseInfo
}

function geometryPayload() {
  return {
    viewport: { yaw_deg: 0.5, pitch_deg: 0, fov_h_deg: 59, width: 320, height: 180 },
    inner: { x: 80, y: 45, width: 160, height: 90 },
    viewport_footprint: [],
    viewport_options: [],
  }
}

async function mountStep() {
  vi.mocked(nudgeViewport).mockReset()
  vi.mocked(nudgeViewport).mockResolvedValue(geometryPayload() as never)
  const wrapper = mount(PickStep, {
    props: { caseName: 'p1', info: info() },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  localStorage.removeItem('vr.pickNudgeDegPerSec')
  vi.useRealTimers()
})

describe('PickStep 源片文件行', () => {
  it('shows the source file with name, path and an open link', async () => {
    const wrapper = await mountStep()
    const row = wrapper.find('.source-file')
    expect(row.text()).toContain('src.mp4')
    expect(row.text()).toContain('sources/src.mp4')
    const link = row.find('a')
    expect(link.attributes('href')).toBe('/file?root=sources&p=src.mp4')
    expect(link.text()).toContain('播放')
    wrapper.unmount()
  })
})

describe('PickStep 微调速度滑块（2026-10-26 新增）', () => {
  it('renders a speed slider and nudges at 1.0°/s by default (old 0.1°/100ms)', async () => {
    vi.useFakeTimers()
    const wrapper = await mountStep()
    const slider = wrapper.find('.nudge-row .slider-stub')
    expect(slider.attributes('min')).toBe('0.2')
    expect(slider.attributes('max')).toBe('5')
    expect(wrapper.find('.nudge-row').text()).toContain('1.0°/秒')

    const up = wrapper.findAll('button').find((item) => item.text() === '↑')!
    await up.trigger('pointerdown')
    // 按下立即走一次 tick：0.1s × 1.0°/s = 0.1°，与旧行为一致。
    expect(nudgeViewport).toHaveBeenCalledWith('p1', 0, 0.1)
    await vi.advanceTimersByTimeAsync(100)
    expect(nudgeViewport).toHaveBeenCalledTimes(2)
    await up.trigger('pointerup')
    wrapper.unmount()
  })

  it('applies the slider speed in real time and remembers it', async () => {
    vi.useFakeTimers()
    const wrapper = await mountStep()
    await wrapper.find('.nudge-row .slider-stub').setValue('2.5')
    await flushPromises()
    expect(wrapper.find('.nudge-row').text()).toContain('2.5°/秒')
    expect(localStorage.getItem('vr.pickNudgeDegPerSec')).toBe('2.5')

    const right = wrapper.findAll('button').find((item) => item.text() === '→')!
    await right.trigger('pointerdown')
    // 0.1s × 2.5°/s = 0.25°/tick。
    expect(nudgeViewport).toHaveBeenCalledWith('p1', 0.25, 0)
    await right.trigger('pointerup')
    wrapper.unmount()
  })

  it('does not draw the old center ring on the panorama', async () => {
    const wrapper = await mountStep()
    expect(wrapper.find('.erp-cross').exists()).toBe(false)
    wrapper.unmount()
  })
})
