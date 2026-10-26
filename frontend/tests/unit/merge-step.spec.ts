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

const AlertStub = defineComponent({
  props: { title: String },
  template: '<div class="alert">{{ title }}</div>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': AlertStub,
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
  it('enables 扩充画面 on a fresh case: the pad gate is ready before the first run', async () => {
    // F5（2026-10-25 验收发现）：pad gate 的 ready 表示“源在、可以开始扩充”，
    // ok 只表示“已经扩充过”。此前按钮只看 ok，新 case 永远点不了第一次。
    const wrapper = await mountWith(
      { ok: false, ready: true, reason: '还没有扩充过画面' },
      { ok: false, ready: false },
    )
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeUndefined()
    // F6：pad 自己的原因要显示出来，不再只有裁回尺寸的原因。
    expect(wrapper.text()).toContain('还没有扩充过画面')
    expect(wrapper.text()).toContain('先采用一次生成结果')
    wrapper.unmount()
  })

  it('disables 扩充画面 only when the pad gate is not ready', async () => {
    const wrapper = await mountWith(
      { ok: true, ready: false },
      { ok: false, ready: false },
    )
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps 扩充画面 enabled after pad has run (ok=true)', async () => {
    const wrapper = await mountWith({ ok: true, ready: true }, { ok: false, ready: false })
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables 裁回原尺寸 until the recompose gate is ready', async () => {
    const wrapper = await mountWith({ ok: true, ready: true }, { ok: false, ready: false, reason: '先采用' })
    expect(button(wrapper, '裁回原尺寸').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
