import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import FileRow from '@/components/common/FileRow.vue'
import type { FileInfo } from '@/types/case-info'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-link': LinkStub,
  'el-icon': true,
}

function entry(overrides: Partial<FileInfo> = {}): FileInfo {
  return {
    name: 'clip.mp4',
    path: 'E:/repo/cases/p1/extracts/v1/clip.mp4',
    rel: 'cases/p1/extracts/v1/clip.mp4',
    exists: true,
    mtime: null,
    size: 2048,
    open_url: '/file?root=cases&p=cases%2Fp1%2Fextracts%2Fv1%2Fclip.mp4',
    ...overrides,
  }
}

function mountRow(value: FileInfo) {
  return mount(FileRow, { props: { entry: value }, global: { stubs } })
}

describe('FileRow', () => {
  it('shows name, rel path, size and a 播放 link for videos', () => {
    const wrapper = mountRow(entry())
    expect(wrapper.text()).toContain('clip.mp4')
    expect(wrapper.text()).toContain('cases/p1/extracts/v1/clip.mp4')
    expect(wrapper.text()).toContain('2.0 KB')
    const link = wrapper.find('a')
    expect(link.text()).toContain('播放')
    expect(link.attributes('href')).toBe(entry().open_url)
    expect(link.attributes('target')).toBe('_blank')
    wrapper.unmount()
  })

  it('opens images with 打开 and falls back to the absolute path', () => {
    const wrapper = mountRow(
      entry({
        name: 'clip_mask.png',
        rel: null,
        path: 'D:/external/clip_mask.png',
      }),
    )
    expect(wrapper.text()).toContain('D:/external/clip_mask.png')
    expect(wrapper.text()).toContain('打开')
    wrapper.unmount()
  })

  it('marks a missing file and renders no link', () => {
    const wrapper = mountRow(entry({ exists: false, size: null, open_url: undefined }))
    expect(wrapper.text()).toContain('文件缺失')
    expect(wrapper.classes()).toContain('missing')
    expect(wrapper.find('a').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders no link when the file exists but is outside every whitelist root', () => {
    const wrapper = mountRow(entry({ open_url: undefined }))
    expect(wrapper.find('a').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders caller actions in the actions slot without changing the row contract', () => {
    const wrapper = mount(FileRow, {
      props: { entry: entry() },
      slots: { actions: '<button class="extra">下载</button>' },
      global: { stubs },
    })
    expect(wrapper.find('.extra').exists()).toBe(true)
    expect(wrapper.find('a').attributes('href')).toBe(entry().open_url)
    wrapper.unmount()
  })
})
