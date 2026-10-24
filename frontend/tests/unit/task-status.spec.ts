import { describe, expect, it } from 'vitest'
import { taskStatusMeta } from '@/constants/taskStatus'

describe('taskStatusMeta', () => {
  it('maps the four task states to Chinese text and tag types', () => {
    expect(taskStatusMeta('queued')).toEqual({ text: '排队中', type: 'warning' })
    expect(taskStatusMeta('running')).toEqual({ text: '运行中', type: 'primary' })
    expect(taskStatusMeta('done')).toEqual({ text: '已完成', type: 'success' })
    expect(taskStatusMeta('failed')).toEqual({ text: '失败', type: 'danger' })
  })

  it('keeps an unknown status readable instead of silently saying queued', () => {
    expect(taskStatusMeta('canceled')).toEqual({ text: 'canceled', type: 'info' })
  })
})
