import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CaseListView from '@/views/CaseListView.vue'
import { flowMeta, flowOptions } from '@/constants/flow'

const mocks = vi.hoisted(() => ({
  fetchCases: vi.fn(),
  deleteCaseRequest: vi.fn(),
}))

vi.mock('@/api/cases', () => ({
  fetchCases: mocks.fetchCases,
  deleteCaseRequest: mocks.deleteCaseRequest,
  fetchModes: vi.fn(async () => ({ modes: [] })),
  fetchSources: vi.fn(async () => ({ files: [] })),
  createCaseRequest: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const BRIEF = {
  name: 'r1',
  flow: 'relight' as const,
  gates: {},
  updated_at: '2026-11-02 10:00',
  outputs: 0,
  owner: 'MDX',
  task_status: null,
}

beforeEach(() => {
  // Element Plus 表格依赖 ResizeObserver，jsdom 没有实现。
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  mocks.fetchCases.mockResolvedValue([BRIEF])
  mocks.deleteCaseRequest.mockResolvedValue({ deleted: 'r1' })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function mountView() {
  return mount(CaseListView, {
    global: { plugins: [ElementPlus] },
    attachTo: document.body,
  })
}

describe('CaseListView', () => {
  it('筛选选项覆盖 flowMeta 的全部流程（含 relight）', () => {
    expect(flowOptions.map((option) => option.value)).toEqual(Object.keys(flowMeta))
    expect(flowOptions.some((option) => option.value === 'relight')).toBe(true)
  })

  it('默认按最近活动倒序，同值回退名称', async () => {
    mocks.fetchCases.mockResolvedValue([
      { ...BRIEF, name: 'old', updated_at: '2026-11-01 10:00', updated_ts: 100 },
      { ...BRIEF, name: 'b-new', updated_at: '2026-11-03 10:00', updated_ts: 300 },
      { ...BRIEF, name: 'a-new', updated_at: '2026-11-03 10:00', updated_ts: 300 },
      { ...BRIEF, name: 'mid', updated_at: '2026-11-02 10:00', updated_ts: 200 },
    ])
    const wrapper = mountView()
    await flushPromises()

    const names = wrapper.findAll('.case-name').map((node) => node.text())
    expect(names).toEqual(['a-new', 'b-new', 'mid', 'old'])
    wrapper.unmount()
  })

  it('删除按钮走确认弹窗并重新加载列表', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const wrapper = mountView()
    await flushPromises()

    const button = wrapper.findAll('button').find((item) => item.text() === '删除')
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalled()
    expect(mocks.deleteCaseRequest).toHaveBeenCalledWith('r1')
    expect(mocks.fetchCases).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
