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

vi.mock('@/api/tasks', () => ({ fetchTasks: vi.fn() }))

vi.mock('@/api/case', () => ({ fetchCase: vi.fn() }))

import { fetchCase } from '@/api/case'
import { fetchTasks } from '@/api/tasks'
import CompareStep from '@/views/wizard/CompareStep.vue'
import type { CaseInfo, OutputFile } from '@/types/case-info'
import type { TaskView } from '@/types/task'

const fetchTasksMock = vi.mocked(fetchTasks)
const fetchCaseMock = vi.mocked(fetchCase)

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

async function mountCompare(info: CaseInfo, caseName = 'p1') {
  viewerInstances.length = 0
  const wrapper = mount(CompareStep, {
    props: { caseName, info },
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
    expect((viewer.sourceB as HTMLImageElement).src).toContain('20261102-094449_image.png')
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

function videoOut(file: string, mtime: number): OutputFile {
  return {
    file,
    name: file,
    exists: true,
    mtime,
    size: 1,
    open_url: `/file?root=cases&p=f1%2Fout%2F${file}`,
    proxy_url: `/video/outproxy/f1/${file}`,
    proxy_state: 'missing',
    proxy_width: null,
  }
}

/** fullredraw 的成片名是轮次哈希，不能按文件名判断新旧；顺序由后端按 mtime 给出。 */
function fullredrawCase(): CaseInfo {
  return {
    name: 'f1',
    flow: 'fullredraw',
    source_url: '/video/source/f1',
    source_file: {
      name: 'ue.mp4',
      rel: 'sources/ue.mp4',
      path: 'E:/x/ue.mp4',
      exists: true,
      open_url: '/file?root=sources&p=ue.mp4',
    },
    proxy: null,
    outputs: [videoOut('older.mp4', 1_000), videoOut('newer.mp4', 2_000)],
    gates: {},
    assets: [],
  } as unknown as CaseInfo
}

describe('CompareStep 视频流程', () => {
  it('offers the output dropdown for fullredraw and defaults to the newest output', async () => {
    const wrapper = await mountCompare(fullredrawCase(), 'f1')
    expect(wrapper.findAll('video')).toHaveLength(2)
    expect(wrapper.findAll('video')[1].attributes('src')).toContain('newer.mp4')
    const outputLabel = wrapper
      .findAll('label.ctl')
      .find((node) => node.text().includes('右路成片'))
    expect(outputLabel).toBeDefined()
    wrapper.unmount()
  })

  it('switches the right video when an older output is chosen', async () => {
    const wrapper = await mountCompare(fullredrawCase(), 'f1')
    const outputLabel = wrapper
      .findAll('label.ctl')
      .find((node) => node.text().includes('右路成片'))
    expect(outputLabel).toBeDefined()
    await outputLabel!.find('select').setValue('older.mp4')
    await flushPromises()
    expect(wrapper.findAll('video')[1].attributes('src')).toContain('older.mp4')
    wrapper.unmount()
  })

  it('downloads whichever output is selected on the right', async () => {
    const wrapper = await mountCompare(fullredrawCase(), 'f1')
    const download = wrapper.find('a.file-action[download]')
    expect(download.exists()).toBe(true)
    expect(download.text()).toContain('下载')
    expect(download.attributes('href')).toContain('newer.mp4')
    expect(download.attributes('href')).toContain('download=1')

    const outputLabel = wrapper
      .findAll('label.ctl')
      .find((node) => node.text().includes('右路成片'))
    await outputLabel!.find('select').setValue('older.mp4')
    await flushPromises()
    expect(wrapper.find('a.file-action[download]').attributes('href')).toContain('older.mp4')
    wrapper.unmount()
  })
})

function relightOut(file: string): OutputFile {
  return {
    file,
    name: file,
    exists: true,
    open_url: `/file?root=cases&p=relight_web_01%2Fout%2F${encodeURIComponent(file)}`,
  }
}

/** out/ 里混入的 relight-* 散件按文件名排在时间戳产物之后，是当初选错成片的现场。 */
function relightCase(overrides?: { current?: string; outputs?: string[] }): CaseInfo {
  const outputs = (
    overrides?.outputs ?? [
      '20261102-105534_relit.mp4',
      '20261103-103409_relit.mp4',
      'relight-test-原始视频.mp4',
      'relight-tset-结果视频.mp4',
    ]
  ).map(relightOut)
  const current = relightOut(overrides?.current ?? '20261103-103409_relit.mp4')
  return {
    name: 'relight_web_01',
    flow: 'relight',
    source_url: '/video/source/relight_web_01',
    source_file: null,
    proxy: null,
    outputs,
    relight: {
      media: 'video',
      gt: {
        asset: 'v4',
        kind: 'video',
        path: 'assets/v4.mp4',
        entry: { name: 'v4.mp4', open_url: '/file?root=cases&p=v4.mp4' },
      },
      atmo: null,
      draft: {},
      current,
      samples: [],
    },
    gates: {},
    assets: [],
  } as unknown as CaseInfo
}

function relightTask(overrides: Partial<TaskView> & Pick<TaskView, 'id' | 'status'>): TaskView {
  return {
    kind: 'relight',
    case: 'relight_web_01',
    owner_id: null,
    owner_name: null,
    project_id: null,
    episode_id: null,
    session_id: null,
    queue_position: null,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    progress: null,
    log_path: null,
    error: null,
    reused: null,
    request_id: null,
    tail: [],
    ...overrides,
  }
}

describe('CompareStep relight 流程', () => {
  it('defaults the right video to [relight] current, not the alphabetically-later stray file', async () => {
    const wrapper = await mountCompare(relightCase())
    const videos = wrapper.findAll('video')
    expect(videos).toHaveLength(2)
    expect(videos[1].attributes('src')).toContain('20261103-103409_relit.mp4')
    expect(videos[1].attributes('src')).not.toContain('relight-tset')
    wrapper.unmount()
  })

  it('offers the output dropdown for relight and switching swaps the right video', async () => {
    const wrapper = await mountCompare(relightCase())
    const outputLabel = wrapper
      .findAll('label.ctl')
      .find((node) => node.text().includes('右路成片'))
    expect(outputLabel).toBeDefined()
    await outputLabel!.find('select').setValue('20261102-105534_relit.mp4')
    await flushPromises()
    expect(wrapper.findAll('video')[1].attributes('src')).toContain('20261102-105534_relit.mp4')
    wrapper.unmount()
  })

  it('follows a new [relight] current after the case refreshes', async () => {
    const wrapper = await mountCompare(relightCase())
    const updated = relightCase({
      outputs: [
        '20261102-105534_relit.mp4',
        '20261103-103409_relit.mp4',
        'relight-test-原始视频.mp4',
        'relight-tset-结果视频.mp4',
        '20261104-120000_relit.mp4',
      ],
      current: '20261104-120000_relit.mp4',
    })
    await wrapper.setProps({ info: updated })
    await flushPromises()
    expect(wrapper.findAll('video')[1].attributes('src')).toContain('20261104-120000_relit.mp4')
    wrapper.unmount()
  })

  it('refreshes the case when a relight run finishes while the page is open', async () => {
    vi.useFakeTimers()
    try {
      fetchTasksMock.mockResolvedValue({
        tasks: [relightTask({ id: 't1', status: 'running' })],
      })
      const wrapper = await mountCompare(relightCase(), 'relight_web_01')
      await vi.advanceTimersByTimeAsync(2000)
      await flushPromises()
      expect(wrapper.emitted('updated')).toBeUndefined()

      const fresh = relightCase()
      fetchTasksMock.mockResolvedValue({
        tasks: [relightTask({ id: 't1', status: 'done' })],
      })
      fetchCaseMock.mockResolvedValue(fresh)
      await vi.advanceTimersByTimeAsync(2000)
      await flushPromises()
      expect(fetchCaseMock).toHaveBeenCalledWith('relight_web_01')
      expect(wrapper.emitted('updated')).toEqual([[fresh]])
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not refresh for a run that finished before the page mounted', async () => {
    vi.useFakeTimers()
    try {
      fetchTasksMock.mockResolvedValue({
        tasks: [relightTask({ id: 't0', status: 'done', created_at: '2026-01-01T00:00:00' })],
      })
      const wrapper = await mountCompare(relightCase())
      await vi.advanceTimersByTimeAsync(6000)
      await flushPromises()
      expect(wrapper.emitted('updated')).toBeUndefined()
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
