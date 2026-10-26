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

import { fetchCase } from '@/api/case'
import ExtractStep from '@/views/wizard/ExtractStep.vue'
import type { CaseInfo } from '@/types/case-info'
import type { TaskView } from '@/types/task'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled || loading"><slot /></button>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': true,
  'el-empty': true,
  'el-tag': true,
  'el-link': true,
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
    const runBtn = () =>
      wrapper.findAll('button').find((item) => item.text().includes('抽取'))!

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
