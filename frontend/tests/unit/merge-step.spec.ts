import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/tasks', () => ({
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
}))

import MergeStep from '@/views/wizard/MergeStep.vue'
import type { CaseInfo } from '@/types/case-info'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled"><slot /></button>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': true,
  'el-tag': true,
  'el-link': true,
  'el-icon': true,
  'el-empty': true,
  'el-checkbox': true,
}

function info(pad: CaseInfo['gates']['pad'], recompose: CaseInfo['gates']['recompose']) {
  return {
    flow: 'fullredraw',
    gates: { pad, recompose },
    outputs: [],
    step_files: {
      pad: { inputs: [], outputs: [] },
      recompose: { inputs: [], outputs: [] },
    },
  } as unknown as CaseInfo
}

async function mountWith(pad: CaseInfo['gates']['pad'], recompose: CaseInfo['gates']['recompose']) {
  const wrapper = mount(MergeStep, {
    props: { caseName: 'p1', info: info(pad, recompose) },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: ReturnType<typeof mount>, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().includes(label))
  if (!found) throw new Error(`没有找到按钮：${label}`)
  return found
}

describe('MergeStep gate buttons', () => {
  it('disables 扩充画面 when the pad gate is not ok, even with a reason', async () => {
    const wrapper = await mountWith({ ok: false, reason: '先扩充' }, { ok: false, ready: false })
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('enables 扩充画面 when the pad gate is ok', async () => {
    const wrapper = await mountWith({ ok: true }, { ok: false, ready: false })
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables 裁回原尺寸 until the recompose gate is ready', async () => {
    const wrapper = await mountWith({ ok: true }, { ok: false, ready: false, reason: '先采用' })
    expect(button(wrapper, '裁回原尺寸').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
