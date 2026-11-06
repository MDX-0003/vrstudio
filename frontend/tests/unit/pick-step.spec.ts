import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

vi.mock('@/api/workflow', () => ({
  setViewport: vi.fn(),
  setViewportSize: vi.fn(),
  setInner: vi.fn(),
  nudgeViewport: vi.fn(),
  resetDraft: vi.fn(),
  saveDraft: vi.fn(),
  ensureProxy: vi.fn(),
  fetchProxy: vi.fn(),
  exportRegion: vi.fn(),
  importRegion: vi.fn(),
}))
vi.mock('@/utils/clipboard', () => ({
  ClipboardUnavailableError: class ClipboardUnavailableError extends Error {},
  readClipboard: vi.fn(),
  writeClipboard: vi.fn(),
}))
vi.mock('@/composables/useInnerDrag', async () => {
  const { ref } = await import('vue')
  return {
    useInnerDrag: () => ({ dragging: ref(false), rect: ref(null), begin: vi.fn() }),
  }
})

import { ElMessage } from 'element-plus'
import { exportRegion, importRegion, nudgeViewport } from '@/api/workflow'
import { ClipboardUnavailableError, readClipboard, writeClipboard } from '@/utils/clipboard'
import PickStep from '@/views/wizard/PickStep.vue'
import type { CaseInfo, RegionPayload } from '@/types/case-info'

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

const DialogStub = defineComponent({
  props: { modelValue: Boolean, title: String },
  emits: ['update:modelValue'],
  template:
    '<div v-if="modelValue" class="dialog-stub"><h3>{{ title }}</h3><slot />' +
    '<footer><slot name="footer" /></footer></div>',
})

const InputStub = defineComponent({
  props: { modelValue: { type: String, default: '' }, readonly: Boolean },
  emits: ['update:modelValue'],
  template:
    '<textarea class="input-stub" :value="modelValue" :readonly="readonly" ' +
    '@input="$emit(\'update:modelValue\', $event.target.value)" />',
})

const stubs = {
  'el-button': ButtonStub,
  'el-link': LinkStub,
  'el-select': true,
  'el-option': true,
  'el-input-number': true,
  'el-icon': true,
  'el-slider': SliderStub,
  'el-dialog': DialogStub,
  'el-input': InputStub,
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

function regionPayload(): RegionPayload {
  return {
    kind: 'vrstudio.patch.region',
    version: 1,
    case: 'p1',
    exported_at: '2026-11-06T16:20:00+08:00',
    viewport: { yaw_deg: 12.5, pitch_deg: -3, fov_h_deg: 59, width: 320, height: 180 },
    inner: { x: 100, y: 50, width: 200, height: 100 },
    frames: { start: 1, end: 6 },
    fps: 30,
    frame_count: 8,
  }
}

function importedGeometry() {
  return {
    viewport: regionPayload().viewport,
    inner: regionPayload().inner,
    previews: { vp: '/img/p1/vp.png?k=new' },
    viewport_footprint: [],
    frames: { start: 1, end: 6 },
    scaled: true,
  }
}

async function mountStep(overrides: Partial<CaseInfo> = {}) {
  vi.mocked(nudgeViewport).mockReset()
  vi.mocked(nudgeViewport).mockResolvedValue(geometryPayload() as never)
  const wrapper = mount(PickStep, {
    props: { caseName: 'p1', info: { ...info(), ...overrides } },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.resetAllMocks()
})

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

describe('PickStep 选区范围的剪贴板导入导出（决策 92）', () => {
  it('shows the two buttons for patch and hides them for patchimg', async () => {
    const patch = await mountStep()
    expect(patch.findAll('.region-actions button').map((item) => item.text())).toEqual([
      '导出范围',
      '导入范围',
    ])
    patch.unmount()

    const image = await mountStep({ flow: 'patchimg' })
    expect(image.find('.region-actions').exists()).toBe(false)
    image.unmount()
  })

  it('exports the saved region JSON to the clipboard', async () => {
    const payload = regionPayload()
    vi.mocked(exportRegion).mockResolvedValue(payload)
    vi.mocked(writeClipboard).mockResolvedValue(true)
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[0].trigger('click')
    await flushPromises()

    expect(exportRegion).toHaveBeenCalledWith('p1')
    expect(writeClipboard).toHaveBeenCalledWith(JSON.stringify(payload, null, 2))
    expect(ElMessage.success).toHaveBeenCalledWith('已复制选区范围（320×180，帧 1–6）')
    wrapper.unmount()
  })

  it('opens the manual copy dialog when writing the clipboard fails', async () => {
    vi.mocked(exportRegion).mockResolvedValue(regionPayload())
    vi.mocked(writeClipboard).mockResolvedValue(false)
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[0].trigger('click')
    await flushPromises()

    expect(wrapper.find('.dialog-stub').exists()).toBe(true)
    expect(wrapper.find('.dialog-stub').text()).toContain('手动复制选区范围')
    expect(wrapper.find('.input-stub').attributes('readonly')).toBeDefined()
    wrapper.unmount()
  })

  it('imports the clipboard region and syncs preview, inner, fov and frames', async () => {
    vi.mocked(readClipboard).mockResolvedValue(JSON.stringify(regionPayload()))
    vi.mocked(importRegion).mockResolvedValue(importedGeometry())
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[1].trigger('click')
    await flushPromises()

    expect(importRegion).toHaveBeenCalledWith('p1', regionPayload())
    expect(wrapper.text()).toContain('yaw 12.50°')
    expect(wrapper.text()).toContain('pitch -3.00°')
    expect(wrapper.text()).toContain('inner x100 y50 200×100')
    expect(wrapper.find('.fov-value').text()).toBe('59.0°')
    const events = wrapper.emitted('geometry')!
    expect(events.at(-1)?.[0]).toMatchObject({
      frames: { start: 1, end: 6 },
      previews: { vp: '/img/p1/vp.png?k=new' },
    })
    expect(ElMessage.success).toHaveBeenCalledWith(
      '已导入选区范围；inner 已按当前视口 320×180 缩放',
    )
    wrapper.unmount()
  })

  it('reports a frame range clamped to a shorter target', async () => {
    vi.mocked(readClipboard).mockResolvedValue(JSON.stringify(regionPayload()))
    vi.mocked(importRegion).mockResolvedValue({
      ...importedGeometry(),
      frames: { start: 0, end: 7 },
      frames_clamped: true,
      scaled: false,
    })
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[1].trigger('click')
    await flushPromises()

    expect(ElMessage.success).toHaveBeenCalledWith('已导入选区范围；帧范围已按目标时长截到 0–7')
    expect(wrapper.text()).toContain('inner x100 y50 200×100')
    wrapper.unmount()
  })

  it('does not call the import endpoint when the clipboard is not JSON', async () => {
    vi.mocked(readClipboard).mockResolvedValue('not a region')
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[1].trigger('click')
    await flushPromises()

    expect(importRegion).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith('剪贴板内容不是选区范围（不是合法 JSON）')
    wrapper.unmount()
  })

  it('falls back to manual paste when clipboard reading is unavailable', async () => {
    vi.mocked(readClipboard).mockRejectedValue(new ClipboardUnavailableError('blocked'))
    vi.mocked(importRegion).mockResolvedValue(importedGeometry())
    const wrapper = await mountStep()

    await wrapper.findAll('.region-actions button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.dialog-stub').exists()).toBe(true)
    expect(wrapper.find('.dialog-stub').text()).toContain('手动粘贴选区范围')

    await wrapper.find('.input-stub').setValue(JSON.stringify(regionPayload()))
    const submit = wrapper.findAll('.dialog-stub button').find((item) => item.text() === '导入')!
    await submit.trigger('click')
    await flushPromises()

    expect(importRegion).toHaveBeenCalledWith('p1', regionPayload())
    expect(wrapper.find('.dialog-stub').exists()).toBe(false)
    wrapper.unmount()
  })
})
