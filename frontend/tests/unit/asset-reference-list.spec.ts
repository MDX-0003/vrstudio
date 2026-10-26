import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import AssetReferenceList from '@/components/assets/AssetReferenceList.vue'
import { KEYFRAME_ROLE_OPTIONS } from '@/constants/assetRoles'
import type { ReferenceRow } from '@/types/reference'

function rows(): ReferenceRow[] {
  return [
    {
      asset: 'i1',
      role: 'subject_ref',
      number: 2,
      locked: true,
      roleLabel: '人物三视图（主图）',
      noteLabel: '主三视图',
      exists: true,
    },
    {
      asset: 'i3',
      role: 'detail_ref',
      note: '头冠纹样',
      number: 3,
      exists: true,
    },
    {
      asset: 'i4',
      role: 'detail_ref',
      note: '袖口纹样',
      number: 4,
      exists: true,
    },
  ]
}

function mountList(data: ReferenceRow[] = rows()) {
  return mount(AssetReferenceList, {
    props: {
      rows: data,
      roleOptions: KEYFRAME_ROLE_OPTIONS,
      roleHelp: {
        subject_ref: '人物三视图：唯一外观依据。',
        detail_ref: '细节参考：只继承所指细节的外观。',
      },
      showNote: true,
      addLabel: '添加参考图',
    },
    global: { plugins: [ElementPlus] },
  })
}

describe('AssetReferenceList', () => {
  it('renders a locked primary row and an editable detail row', () => {
    const wrapper = mountList()
    const list = wrapper.findAll('.ref-row')
    expect(list).toHaveLength(3)
    expect(list[0].text()).toContain('人物三视图（主图）')
    expect(list[0].find('.role-select').exists()).toBe(false)
    expect(list[1].find('.role-select').exists()).toBe(true)
    wrapper.unmount()
  })

  it('emits role and note changes with the asset id', async () => {
    const wrapper = mountList()
    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('change', 'subject_ref')
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('role')?.[0]).toEqual(['i3', 'subject_ref'])

    const input = wrapper.find('.ref-note input')
    await input.setValue('袖口纹样')
    expect(wrapper.emitted('note')?.[0]).toEqual(['i3', '袖口纹样'])
    wrapper.unmount()
  })

  it('emits move and remove for the detail row', async () => {
    const wrapper = mountList()
    const detail = wrapper.findAll('.ref-row')[1]
    await detail.find('[aria-label="下移"]').trigger('click')
    await detail.find('[aria-label="从清单移除"]').trigger('click')
    expect(wrapper.emitted('move')?.[0]).toEqual(['i3', 1])
    expect(wrapper.emitted('remove')?.[0]).toEqual(['i3'])
    wrapper.unmount()
  })

  it('emits add from the footer button', async () => {
    const wrapper = mountList()
    const add = wrapper.findAll('button').find((button) => button.text().includes('添加参考图'))!
    await add.trigger('click')
    expect(wrapper.emitted('add')).toBeTruthy()
    wrapper.unmount()
  })
})
