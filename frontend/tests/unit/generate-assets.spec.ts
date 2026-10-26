import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import GenerateAssets from '@/components/generate/GenerateAssets.vue'
import type { AssetRow } from '@/types/case'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-link': LinkStub,
  'el-icon': true,
  'el-checkbox': true,
  'el-select': true,
  'el-option': true,
  'el-empty': true,
  'el-button': true,
}

function asset(overrides: Partial<AssetRow> = {}): AssetRow {
  return {
    id: 'i1',
    name: 'Video1 · clip.mp4',
    kind: 'video',
    role: 'clip',
    roleRaw: 'clip',
    roleFixed: true,
    meta: '1920×1080',
    selected: true,
    tone: 'blue',
    url: '/img/asset/p1/i1',
    exists: true,
    rel: 'cases/p1/extracts/v1/clip.mp4',
    path: 'E:/repo/cases/p1/extracts/v1/clip.mp4',
    open_url: '/file?root=cases&p=p1%2Fextracts%2Fv1%2Fclip.mp4',
    ...overrides,
  }
}

describe('GenerateAssets', () => {
  it('opens the original asset file from the card', () => {
    const wrapper = mount(GenerateAssets, {
      props: { assets: [asset()], selectedCount: 1, saving: false },
      global: { stubs },
    })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('/file?root=cases&p=p1%2Fextracts%2Fv1%2Fclip.mp4')
    expect(link.attributes('title')).toBe('Video1 · clip.mp4\ncases/p1/extracts/v1/clip.mp4')
    wrapper.unmount()
  })

  it('renders no open link when the asset has no open_url', () => {
    const wrapper = mount(GenerateAssets, {
      props: { assets: [asset({ open_url: undefined })], selectedCount: 1, saving: false },
      global: { stubs },
    })
    expect(wrapper.find('a').exists()).toBe(false)
    wrapper.unmount()
  })
})
