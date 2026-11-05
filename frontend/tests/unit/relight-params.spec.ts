import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ParamRow from '@/components/relight/ParamRow.vue'
import { PARAMS, controlIds } from '@/constants/relightParams'
import type { ParamDesc } from '@/constants/relightParams'

/**
 * 默认卡与区域卡共用同一份描述表与同一个组件：对每一项断言两侧的控件集合相同。
 * 覆盖开关是唯一允许的差异（区域卡多一个），它由 data-control="override" 标记。
 */
function controlSet(desc: ParamDesc, scope: 'default' | 'region'): string[] {
  const value = desc.kind === 'clip' ? { knee: 0.72, max: 0.98 } : 0.5
  const wrapper = mount(ParamRow, { props: { desc, value, scope, fallback: 1 } })
  return wrapper
    .findAll('[data-control]')
    .map((node) => node.attributes('data-control') ?? '')
    .filter((id) => id !== 'override')
    .sort()
}

describe('ParamRow', () => {
  it('每个参数在默认卡与区域卡里渲染同一组控件', () => {
    for (const desc of PARAMS) {
      expect(controlSet(desc, 'region')).toEqual(controlSet(desc, 'default'))
      expect(controlSet(desc, 'region')).toEqual(controlIds(desc).slice().sort())
    }
  })

  it('区域卡的数字参数点开关在覆盖与默认之间切换', async () => {
    const desc = PARAMS[0]
    const wrapper = mount(ParamRow, {
      props: { desc, value: null, scope: 'region', fallback: 0.8 },
    })
    expect(wrapper.text()).toContain('默认')
    await wrapper.find('[data-control="override"]').trigger('click')
    expect(wrapper.emitted('update:value')?.[0]).toEqual([0.8])
  })

  it('软裁切在默认卡上可以开启并改拐点与上限', async () => {
    const clip = PARAMS[2]
    const wrapper = mount(ParamRow, {
      props: { desc: clip, value: null, scope: 'default', fallback: 1 },
    })
    await wrapper.find('[data-control="enabled"]').trigger('change')
    expect(wrapper.emitted('update:value')?.[0]).toEqual([{ knee: 0.72, max: 0.98 }])

    const on = mount(ParamRow, {
      props: { desc: clip, value: { knee: 0.72, max: 0.98 }, scope: 'default', fallback: 1 },
    })
    const knee = on.find('[data-control="knee"]')
    await knee.setValue('0.6')
    expect(on.emitted('update:value')?.[0]).toEqual([{ knee: 0.6, max: 0.98 }])
  })
})
