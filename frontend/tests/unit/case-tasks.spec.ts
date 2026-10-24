import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/tasks', () => ({ fetchTasks: vi.fn() }))

import { fetchTasks } from '@/api/tasks'
import { useCaseTasks } from '@/composables/useCaseTasks'
import type { TaskView } from '@/types/task'

const fetchTasksMock = vi.mocked(fetchTasks)

function task(overrides: Partial<TaskView> & Pick<TaskView, 'id' | 'case'>): TaskView {
  return {
    id: overrides.id,
    kind: 'extract',
    case: overrides.case,
    owner_id: null,
    owner_name: null,
    project_id: null,
    episode_id: null,
    session_id: null,
    status: 'done',
    queue_position: null,
    created_at: '2026-10-24T00:00:00',
    started_at: null,
    finished_at: null,
    progress: null,
    log_path: null,
    error: null,
    request_id: null,
    tail: [],
    ...overrides,
  }
}

function setup() {
  const caseName = ref('A')
  let api!: ReturnType<typeof useCaseTasks>
  const Comp = defineComponent({
    setup() {
      api = useCaseTasks(caseName, ['extract'])
      return () => null
    },
  })
  const wrapper = mount(Comp)
  return { caseName, api, wrapper }
}

describe('useCaseTasks', () => {
  beforeEach(() => {
    fetchTasksMock.mockReset()
  })

  it('filters tasks by the requested case and kind', async () => {
    fetchTasksMock.mockResolvedValue({
      tasks: [
        task({ id: 'a-extract', case: 'A' }),
        task({ id: 'b-extract', case: 'B' }),
        task({ id: 'a-merge', case: 'A', kind: 'merge' }),
      ],
    })
    const { api, wrapper } = setup()

    await api.refresh()

    expect(api.tasks.value.map((item) => item.id)).toEqual(['a-extract'])
    wrapper.unmount()
  })

  it('does not apply a response for an old case after switching', async () => {
    let resolveOld!: (value: { tasks: TaskView[] }) => void
    const oldResponse = new Promise<{ tasks: TaskView[] }>((resolve) => {
      resolveOld = resolve
    })
    fetchTasksMock.mockReturnValueOnce(oldResponse)

    const { caseName, api, wrapper } = setup()
    const loadOld = api.refresh()

    caseName.value = 'B'
    await nextTick()
    resolveOld({ tasks: [task({ id: 'a-extract', case: 'A' })] })
    await loadOld

    expect(api.tasks.value).toEqual([])
    wrapper.unmount()
  })

  it('clears and reloads tasks when the case changes', async () => {
    fetchTasksMock.mockResolvedValueOnce({
      tasks: [task({ id: 'a-extract', case: 'A', created_at: '2026-10-24T00:00:00' })],
    })
    const { caseName, api, wrapper } = setup()
    await api.refresh()
    expect(api.tasks.value.map((item) => item.id)).toEqual(['a-extract'])

    caseName.value = 'B'
    fetchTasksMock.mockResolvedValueOnce({
      tasks: [task({ id: 'b-extract', case: 'B', created_at: '2026-10-24T01:00:00' })],
    })
    await nextTick()
    await vi.waitFor(() => {
      expect(api.tasks.value.map((item) => item.id)).toEqual(['b-extract'])
    })

    wrapper.unmount()
  })
})
