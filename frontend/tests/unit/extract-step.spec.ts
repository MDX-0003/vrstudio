import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/case', () => ({
  fetchCase: vi.fn(),
  runOperation: vi.fn(),
}))

// useCaseTasks 用可注入的 latest ref 替身：测试直接推动任务状态。
const tasksState = vi.hoisted(() => ({
  setLatest: (_value: unknown) => {},
  start: vi.fn(),
}))
vi.mock('@/composables/useCaseTasks', async () => {
  const { ref } = await import('vue')
  const latest = ref(null)
  tasksState.setLatest = (value: unknown) => {
    latest.value = value as never
  }
  return {
    useCaseTasks: () => ({
      latest,
      tasks: ref([]),
      error: ref(''),
      refresh: vi.fn(async () => []),
      start: tasksState.start,
      stop: vi.fn(),
    }),
  }
})

import { fetchCase, runOperation } from '@/api/case'
import ExtractStep from '@/views/wizard/ExtractStep.vue'
import type { CaseInfo } from '@/types/case-info'
import type { TaskView } from '@/types/task'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled || loading"><slot /></button>',
})

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': true,
  'el-empty': true,
  'el-tag': true,
  'el-link': LinkStub,
  'el-icon': true,
}

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    gates: { pick: { ok: true }, extract: { ok: false } },
    step_files: { extract: { inputs: [], outputs: [] } },
    extract_current: null,
    extracts: [],
  } as unknown as CaseInfo
}

function pickInfo(): CaseInfo {
  const base = info()
  const viewport = {
    yaw_deg: 10,
    pitch_deg: -5,
    fov_h_deg: 90,
    width: 320,
    height: 180,
  }
  const inner = { x: 80, y: 40, width: 160, height: 100 }
  const frames = { start: 0, end: 5 }
  return {
    ...base,
    viewport,
    inner,
    frames,
    pick: {
      draft: { viewport, inner, frames, updated_at: '2026-11-09T17:05:00+08:00' },
      active: 'r1',
      presets: [
        {
          id: 'r1',
          created_at: '2026-11-09T16:30:00+08:00',
          updated_at: '2026-11-09T17:05:00+08:00',
          viewport,
          inner,
          frames,
        },
      ],
      dirty: false,
    },
    extracts: [
      {
        version: 'v1',
        extracted: true,
        has_result: true,
        viewport,
        inner,
        frames,
        fps: 30,
      },
    ],
  } as unknown as CaseInfo
}

function task(status: string, createdOffsetMs = 0): TaskView {
  return {
    id: 't1',
    kind: 'extract',
    status,
    case: 'p1',
    created_at: new Date(Date.now() + createdOffsetMs).toISOString(),
    error: '',
    tail: [],
    queue_position: 0,
  } as unknown as TaskView
}

async function mountStep() {
  const wrapper = mount(ExtractStep, {
    props: { caseName: 'p1', info: info() },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('ExtractStep 输出文件走统一文件视图', () => {
  it('links each output by the payload open_url instead of a family route', async () => {
    const data = info()
    data.step_files.extract = {
      inputs: [],
      outputs: [
        {
          name: 'clip.mp4',
          path: 'E:/repo/cases/p1/extracts/v1/clip.mp4',
          rel: 'cases/p1/extracts/v1/clip.mp4',
          exists: true,
          mtime: null,
          size: 1024,
          open_url: '/file?root=cases&p=cases%2Fp1%2Fextracts%2Fv1%2Fclip.mp4',
        },
      ],
    }
    const wrapper = mount(ExtractStep, {
      props: { caseName: 'p1', info: data },
      global: { stubs },
    })
    await flushPromises()
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('/file?root=cases&p=cases%2Fp1%2Fextracts%2Fv1%2Fclip.mp4')
    expect(link.text()).toContain('播放')
    wrapper.unmount()
  })
})

describe('ExtractStep 产物四格', () => {
  it('renders one tile per product and opens each by open_url', async () => {
    const data = info()
    const entry = (name: string) => ({
      name,
      path: `E:/repo/cases/p1/extracts/v1/${name}`,
      rel: `cases/p1/extracts/v1/${name}`,
      exists: true,
      mtime: null,
      size: 2048,
      open_url: `/file?root=cases&p=cases%2Fp1%2Fextracts%2Fv1%2F${name}`,
    })
    data.step_files.extract = {
      inputs: [],
      outputs: [
        entry('clip.mp4'),
        entry('clip_mask.png'),
        entry('clip.json'),
        entry('first_frame_raw.png'),
        entry('last_frame_raw.png'),
      ],
    }
    const wrapper = mount(ExtractStep, {
      props: { caseName: 'p1', info: data },
      global: { stubs },
    })
    await flushPromises()

    expect(wrapper.findAll('.ex-prod')).toHaveLength(4)
    expect(wrapper.find('.ex-prod:nth-child(2) img').attributes('src')).toBe(
      '/file?root=cases&p=cases%2Fp1%2Fextracts%2Fv1%2Fclip_mask.png',
    )
    expect(wrapper.findAll('.ex-pair a')).toHaveLength(2)
    wrapper.unmount()
  })
})

describe('ExtractStep 完成刷新（2026-10-26 陈旧文件列表修复）', () => {
  it('starts task polling on mount, so externally started tasks are visible', async () => {
    tasksState.start.mockClear()
    await mountStep()
    expect(tasksState.start).toHaveBeenCalled()
  })

  it('refreshes when a task finishes even if this page never clicked 运行抽取', async () => {
    // 旧行为：running 只在本页点击后为 true；中途刷新的页面永远不刷新，
    // 抽取完成仍显示旧版本的“文件缺失”。
    vi.mocked(fetchCase).mockClear()
    vi.mocked(fetchCase).mockResolvedValue(info())
    const wrapper = await mountStep()
    expect(fetchCase).not.toHaveBeenCalled()

    tasksState.setLatest(task('done'))
    await flushPromises()

    expect(fetchCase).toHaveBeenCalledWith('p1')
    expect(wrapper.emitted('updated')).toBeTruthy()
    wrapper.unmount()
  })

  it('ignores tasks that finished before this page mounted', async () => {
    vi.mocked(fetchCase).mockClear()
    vi.mocked(fetchCase).mockResolvedValue(info())
    const wrapper = await mountStep()

    tasksState.setLatest(task('done', -60_000))
    await flushPromises()

    expect(fetchCase).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows the running state from the task list, not from a local click flag', async () => {
    const wrapper = await mountStep()
    const runBtn = () => wrapper.findAll('button').find((item) => item.text().includes('抽取'))!

    expect(runBtn().attributes('disabled')).toBeUndefined()

    tasksState.setLatest(task('running'))
    await flushPromises()
    expect(runBtn().attributes('disabled')).toBeDefined()

    tasksState.setLatest(task('done'))
    await flushPromises()
    expect(runBtn().attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})

describe('ExtractStep 保存的范围与只读版本', () => {
  it('runs extract with the selected preset', async () => {
    vi.mocked(runOperation).mockClear()
    const wrapper = mount(ExtractStep, {
      props: { caseName: 'p1', info: pickInfo() },
      global: { stubs },
    })
    await flushPromises()
    expect(wrapper.findAll('.range-row')).toHaveLength(2)
    await wrapper
      .findAll('button')
      .find((item) => item.text().includes('运行抽取'))!
      .trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'extract', { preset: 'r1' })
    wrapper.unmount()
  })

  it('marks the latest extract and does not make version rows selectable', async () => {
    const wrapper = mount(ExtractStep, {
      props: { caseName: 'p1', info: pickInfo() },
      global: { stubs },
    })
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('最新')
    expect(wrapper.findAll('.version-row.selectable')).toHaveLength(0)
    wrapper.unmount()
  })
})
