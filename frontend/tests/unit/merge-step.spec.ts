import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/tasks', () => ({
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
}))
vi.mock('@/api/case', () => ({
  fetchCase: vi.fn(async () => ({})),
  runOperation: vi.fn(async () => ({})),
  setMergeInput: vi.fn(async () => ({ name: 'p1' })),
}))

import MergeStep from '@/views/wizard/MergeStep.vue'
import { setMergeInput } from '@/api/case'
import type { CaseInfo } from '@/types/case-info'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled"><slot /></button>',
})

const AlertStub = defineComponent({
  props: { title: String },
  template: '<div class="alert">{{ title }}</div>',
})

const PickerStub = defineComponent({
  props: { modelValue: Boolean, mode: String, title: String },
  emits: ['confirm', 'updated'],
  template: '<div class="picker-stub" />',
})

const UploadStub = defineComponent({
  props: { modelValue: Boolean, destination: String, caseName: String },
  emits: ['added'],
  template: '<div class="upload-stub" />',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': AlertStub,
  'el-tag': true,
  'el-link': true,
  'el-icon': true,
  'el-empty': true,
  'el-checkbox': true,
  AssetPickerDialog: PickerStub,
  AssetUploadDialog: UploadStub,
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

function imageInfo(mergeInput: string | null = null, withAssets = true): CaseInfo {
  return {
    flow: 'fullRedrawImg',
    gates: {
      extract: { ok: true },
      generate: { ok: false, ready: true },
      merge: { ok: false, ready: true },
      compare: { ok: false },
    },
    outputs: [],
    merge_input: mergeInput,
    assets: withAssets ? [
      {
        id: 'i1',
        kind: 'image',
        role: 'lighting_ref',
        label: '光照参考',
        path: 'assets/i1_edited.png',
        source: 'asset',
        role_fixed: false,
        url: '',
        name: 'i1_edited.png',
        rel: 'cases/p1/assets/i1_edited.png',
        exists: true,
        mtime: null,
        size: 1024,
        width: 9558,
        height: 4096,
        duration: null,
        fps: null,
        open_url: '',
      },
    ] : [],
    step_files: {
      extract: { inputs: [], outputs: [] },
      generate: { inputs: [], outputs: [] },
      merge: { inputs: [], outputs: [] },
    },
  } as unknown as CaseInfo
}

async function mountImage(mergeInput: string | null = null, withAssets = true) {
  const wrapper = mount(MergeStep, {
    props: { caseName: 'p1', info: imageInfo(mergeInput, withAssets) },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('MergeStep 拼接输入（fullRedrawImg）', () => {
  it('keeps 上传并用作拼接输入 available even with an empty library', async () => {
    const wrapper = await mountImage(null, false)
    expect(button(wrapper, '上传并用作拼接输入').attributes('disabled')).toBeUndefined()
    expect(button(wrapper, '从素材库选择').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('button').some((item) => item.text().includes('用回生成结果'))).toBe(
      false,
    )
    wrapper.unmount()
  })

  it('opens the merge-input upload dialog', async () => {
    const wrapper = await mountImage(null, false)
    await button(wrapper, '上传并用作拼接输入').trigger('click')
    const dialog = wrapper.findComponent(UploadStub)
    expect(dialog.props('destination')).toBe('merge-input')
    expect(dialog.props('modelValue')).toBe(true)
    wrapper.unmount()
  })

  it('writes the picked library file as [merge] input', async () => {
    vi.mocked(setMergeInput).mockClear()
    const wrapper = await mountImage()
    const picker = wrapper.findComponent(PickerStub)
    expect(picker.props('mode')).toBe('image')
    picker.vm.$emit('confirm', ['i1'])
    await flushPromises()
    expect(setMergeInput).toHaveBeenCalledWith('p1', 'i1')
    wrapper.unmount()
  })

  it('clears the pointer back to the generated result', async () => {
    vi.mocked(setMergeInput).mockClear()
    const wrapper = await mountImage('i1')
    await button(wrapper, '用回生成结果').trigger('click')
    await flushPromises()
    expect(setMergeInput).toHaveBeenCalledWith('p1', null)
    wrapper.unmount()
  })
})

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
    const wrapper = await mountWith({ ok: true, ready: false }, { ok: false, ready: false })
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps 扩充画面 enabled after pad has run (ok=true)', async () => {
    const wrapper = await mountWith({ ok: true, ready: true }, { ok: false, ready: false })
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables 裁回原尺寸 until the recompose gate is ready', async () => {
    const wrapper = await mountWith(
      { ok: true, ready: true },
      { ok: false, ready: false, reason: '先采用' },
    )
    expect(button(wrapper, '裁回原尺寸').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
