import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'

import GeneratePrompt from '@/components/generate/GeneratePrompt.vue'
import type { PromptVariable } from '@/types/case'

const DrawerStub = defineComponent({
  props: { modelValue: Boolean },
  template: '<div class="drawer"><slot /><slot name="footer" /></div>',
})

const InputStub = defineComponent({
  props: { modelValue: { type: null, default: '' } },
  emits: ['update:modelValue'],
  template:
    '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
})

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  emits: ['click'],
  template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
})

const AlertStub = defineComponent({
  props: { title: String },
  template: '<div class="alert">{{ title }}</div>',
})

const EmptyStub = defineComponent({ template: '<div class="empty" />' })

const stubs = {
  'el-drawer': DrawerStub,
  'el-input': InputStub,
  'el-button': ButtonStub,
  'el-alert': AlertStub,
  'el-empty': EmptyStub,
}

const VARIABLES: PromptVariable[] = [
  { key: 'subject', label: '原主体', help: '要被替换的主体', value: '人偶' },
  { key: 'target', label: '重绘结果', help: '', value: '' },
]

async function mountPrompt(props: Record<string, unknown> = {}) {
  const wrapper = mount(GeneratePrompt, {
    props: {
      variables: VARIABLES,
      rendered: '正文',
      renderError: '',
      saving: false,
      adding: false,
      open: true,
      ...props,
    },
    global: { stubs },
  })
  return wrapper
}

describe('GeneratePrompt 添加变量（F3）', () => {
  it('disables the add button until the key is a fresh identifier', async () => {
    const wrapper = await mountPrompt()
    const inputs = wrapper.findAll('input')
    const keyInput = inputs[inputs.length - 2]
    const valueInput = inputs[inputs.length - 1]
    const found = wrapper.findAll('button').find((b) => b.text() === '添加')
    if (!found) throw new Error('没有找到添加按钮')
    const addBtn = found

    expect(addBtn.attributes('disabled')).toBeDefined()

    await keyInput.setValue('style_ref')
    expect(addBtn.attributes('disabled')).toBeUndefined()

    // 与现有变量重名：不可添加。
    await keyInput.setValue('subject')
    expect(addBtn.attributes('disabled')).toBeDefined()

    // 非法字符：不可添加。
    await keyInput.setValue('风格参考')
    expect(addBtn.attributes('disabled')).toBeDefined()
    expect((valueInput.element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('emits add-var with key and value, then clears the form', async () => {
    const wrapper = await mountPrompt()
    const inputs = wrapper.findAll('input')
    await inputs[inputs.length - 2].setValue('style_ref')
    await inputs[inputs.length - 1].setValue('胶片质感')
    const addBtn = wrapper.findAll('button').find((b) => b.text() === '添加')
    if (!addBtn) throw new Error('没有找到添加按钮')
    await addBtn.trigger('click')

    expect(wrapper.emitted('add-var')).toEqual([['style_ref', '胶片质感']])
    const cleared = wrapper.findAll('input')
    expect((cleared[cleared.length - 2].element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })
})

describe('GeneratePrompt render_error 展示（F2）', () => {
  it('prefixes the raw render error with a Chinese explanation', async () => {
    const wrapper = await mountPrompt({
      renderError: 'prompt template uses {asset_list}, which nothing defines.',
    })
    const alerts = wrapper.findAll('.alert').map((a) => a.text())
    expect(alerts.join('\n')).toContain('提示词暂时无法完整展开：')
    expect(alerts.join('\n')).toContain('{asset_list}')
    wrapper.unmount()
  })
})
