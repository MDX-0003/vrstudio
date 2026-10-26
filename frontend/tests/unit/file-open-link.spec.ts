import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import FileOpenLink from '@/components/common/FileOpenLink.vue'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = { 'el-link': LinkStub, 'el-icon': true }

function mountLink(file: Record<string, unknown> | null) {
  return mount(FileOpenLink, { props: { file, label: '打开原图' }, global: { stubs } })
}

describe('FileOpenLink', () => {
  it('links the original file and shows name + rel path in the tooltip', () => {
    const wrapper = mountLink({
      name: 'result_1.png',
      rel: 'vrgen_runs/walk_02/img-1ce2b5e4/result_1.png',
      path: 'E:/repo/vrgen_runs/walk_02/img-1ce2b5e4/result_1.png',
      exists: true,
      open_url: '/file?root=workdir&p=walk_02%2Fimg-1ce2b5e4%2Fresult_1.png',
    })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe(
      '/file?root=workdir&p=walk_02%2Fimg-1ce2b5e4%2Fresult_1.png',
    )
    expect(link.attributes('title')).toBe(
      'result_1.png\nvrgen_runs/walk_02/img-1ce2b5e4/result_1.png',
    )
    expect(link.attributes('aria-label')).toBe('打开原图')
    wrapper.unmount()
  })

  it('falls back to the absolute path when rel is unavailable', () => {
    const wrapper = mountLink({
      name: 'src.mp4',
      rel: null,
      path: 'D:/sources/src.mp4',
      exists: true,
      open_url: '/file?root=sources&p=src.mp4',
    })
    expect(wrapper.find('a').attributes('title')).toBe('src.mp4\nD:/sources/src.mp4')
    wrapper.unmount()
  })

  it('renders nothing when the file is missing or has no open_url', () => {
    expect(mountLink({ name: 'x.png', exists: false, rel: 'x.png' }).find('a').exists()).toBe(false)
    expect(mountLink({ name: 'x.png', exists: true, rel: 'x.png' }).find('a').exists()).toBe(false)
    expect(mountLink(null).find('a').exists()).toBe(false)
  })
})
