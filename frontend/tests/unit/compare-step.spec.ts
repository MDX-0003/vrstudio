import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

const { viewerInstances } = vi.hoisted(() => ({
  viewerInstances: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/legacy/Erp360Compare', () => ({
  Erp360Compare: class {
    supported = true
    setSplit = vi.fn()
    resetView = vi.fn()
    destroy = vi.fn()
    canvas: unknown
    sourceA: unknown
    sourceB: unknown

    constructor(canvas: unknown, sourceA: unknown, sourceB: unknown) {
      this.canvas = canvas
      this.sourceA = sourceA
      this.sourceB = sourceB
      viewerInstances.push(this as unknown as Record<string, unknown>)
    }
  },
}))

vi.mock('@/api/workflow', () => ({
  ensureProxy: vi.fn(),
  fetchProxy: vi.fn(),
}))

import CompareStep from '@/views/wizard/CompareStep.vue'
import type { CaseInfo } from '@/types/case-info'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean, icon: [Object, Function] },
  template: '<button :disabled="disabled"><slot /></button>',
})

const SelectStub = defineComponent({
  props: { modelValue: [String, Number] },
  emits: ['update:modelValue'],
  methods: {
    onChange(event: Event) {
      this.$emit('update:modelValue', (event.target as HTMLSelectElement).value)
    },
  },
  template: '<select :value="modelValue" @change="onChange"><slot /></select>',
})

const OptionStub = defineComponent({
  props: { label: String, value: [String, Number] },
  template: '<option :value="value">{{ label }}</option>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-select': SelectStub,
  'el-option': OptionStub,
  'el-slider': true,
  'el-tag': true,
  'el-empty': true,
  'el-switch': true,
}

function imageCase(): CaseInfo {
  return {
    name: 'p1',
    flow: 'fullRedrawImg',
    source_url: '/video/source/p1',
    source_file: {
      name: 'light_source.png',
      rel: 'sources/light_source.png',
      path: 'E:/x/light_source.png',
      exists: true,
      open_url: '/file?root=sources&p=light_source.png',
    },
    proxy: null,
    outputs: [
      {
        file: '20261101-100000_image.png',
        name: '20261101-100000_image.png',
        exists: true,
        open_url: '/file?root=cases&p=p1%2Fout%2F20261101-100000_image.png',
      },
      {
        file: '20261102-094449_image.png',
        name: '20261102-094449_image.png',
        exists: true,
        open_url: '/file?root=cases&p=p1%2Fout%2F20261102-094449_image.png',
      },
    ],
    image_report: null,
    adopt_round: null,
    adopt_index: null,
    image_rounds: [],
    gates: {},
    assets: [],
    submit_entries: [],
  } as unknown as CaseInfo
}

async function mountCompare(info: CaseInfo) {
  viewerInstances.length = 0
  const wrapper = mount(CompareStep, {
    props: { caseName: 'p1', info },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('CompareStep 图片流程', () => {
  it('renders one 360 stage instead of two scrollable image panes', async () => {
    const wrapper = await mountCompare(imageCase())
    expect(wrapper.find('.compare-canvas').exists()).toBe(true)
    expect(wrapper.find('.split-line').exists()).toBe(true)
    expect(wrapper.find('.image-pane').exists()).toBe(false)
    expect(wrapper.text()).toContain('视角复位')
    expect(wrapper.text()).toContain('全屏')
    wrapper.unmount()
  })

  it('defaults to the newest output and feeds both images to the viewer', async () => {
    const wrapper = await mountCompare(imageCase())
    const viewer = viewerInstances[0]
    expect(viewer.sourceB).toBeInstanceOf(HTMLImageElement)
    expect((viewer.sourceB as HTMLImageElement).src).toContain(
      '20261102-094449_image.png',
    )
    expect((viewer.sourceA as HTMLImageElement).src).toContain('light_source.png')
    expect(wrapper.text()).toContain('20261102-094449_image.png')
    wrapper.unmount()
  })

  it('follows the output selector when an older merge is chosen', async () => {
    const wrapper = await mountCompare(imageCase())
    await wrapper.find('select').setValue('20261101-100000_image.png')
    await flushPromises()
    const latest = viewerInstances[viewerInstances.length - 1]
    expect((latest.sourceB as HTMLImageElement).src).toContain('20261101-100000_image.png')
    wrapper.unmount()
  })

  it('offers plain open and download links for the output', async () => {
    const wrapper = await mountCompare(imageCase())
    const open = wrapper.find('a.file-action:not([download])')
    expect(open.attributes('href')).toContain('20261102-094449_image.png')
    expect(open.attributes('target')).toBe('_blank')

    const download = wrapper.find('a.file-action[download]')
    expect(download.text()).toContain('下载')
    expect(download.attributes('href')).toContain('download=1')
    wrapper.unmount()
  })
})
