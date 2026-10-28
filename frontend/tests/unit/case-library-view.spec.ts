import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import CaseLibraryView from '@/views/case/CaseLibraryView.vue'
import type { AssetDetail, CaseInfo } from '@/types/case-info'

function asset(
  id: string,
  role: string,
  name: string,
  kind: 'image' | 'video' = 'image',
): AssetDetail {
  return {
    id,
    kind,
    role,
    label: role,
    path: `E:/repo/cases/p1/assets/${name}`,
    source: 'asset',
    role_fixed: false,
    url: `/img/asset/p1/${id}`,
    name,
    rel: `cases/p1/assets/${name}`,
    exists: true,
    mtime: null,
    size: 1024,
    width: 1920,
    height: 1080,
    duration: null,
    fps: null,
    open_url: `/file?root=cases&p=cases%2Fp1%2Fassets%2F${name}`,
  }
}

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    assets: [
      asset('i1', 'subject_ref', 'i1_sheet.png'),
      asset('i2', 'first_frame', 'i2_first.png'),
      asset('i3', 'subject_ref', 'i3_detail.png'),
    ],
    submit_entries: [{ asset: 'i2', role: 'first_frame' }],
    submit_roles: { i2: 'first_frame' },
    role_help: {},
    keyframe: {
      enabled: true,
      error: null,
      refs_default: false,
      role_help: {},
      refs: [
        {
          asset: 'i1',
          role: 'subject_ref',
          note: '',
          label: 'i1_sheet.png',
          url: '/img/asset/p1/i1',
          exists: true,
          file: null,
        },
      ],
      first: null,
      last: null,
      extract: 'v1',
      stale: false,
      complete: false,
    },
  } as unknown as CaseInfo
}

function mountView(data: CaseInfo = info()) {
  return mount(CaseLibraryView, {
    props: { caseName: 'p1', info: data },
    global: { plugins: [ElementPlus] },
  })
}

const SOURCE_FILE = {
  name: 'pano_source.mp4',
  rel: 'sources/pano_source.mp4',
  path: 'E:/repo/sources/pano_source.mp4',
  exists: true,
  mtime: null,
  size: 2684354560,
  open_url: '/file?root=sources&p=pano_source.mp4',
}

describe('CaseLibraryView', () => {
  it('lists files with usage counts and no library role select', () => {
    const wrapper = mountView()
    expect(wrapper.findAll('.library-row')).toHaveLength(4) // header + 3 files
    expect(wrapper.text()).toContain('i1_sheet.png')
    expect(wrapper.text()).toContain('i2_first.png')
    expect(wrapper.text()).toContain('1 处')
    expect(wrapper.text()).toContain('未引用')
    expect(wrapper.find('.library-table .role-select').exists()).toBe(false)
    wrapper.unmount()
  })

  it('filters by file name', async () => {
    const wrapper = mountView()
    const input = wrapper.find('.library-toolbar input')
    await input.setValue('i3_detail')
    expect(wrapper.findAll('.library-row')).toHaveLength(2) // header + one file
    expect(wrapper.text()).toContain('i3_detail.png')
    expect(wrapper.text()).not.toContain('i2_first.png')
    wrapper.unmount()
  })

  it('filters by reference status', async () => {
    const wrapper = mountView()
    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    selects[1].vm.$emit('update:modelValue', 'unreferenced')
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.library-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('i3_detail.png')
    wrapper.unmount()
  })

  it('shows the source video as a read-only flow file', () => {
    const data = info()
    data.source_file = SOURCE_FILE as unknown as CaseInfo['source_file']
    const wrapper = mountView(data)
    expect(wrapper.find('.library-flow').exists()).toBe(true)
    expect(wrapper.text()).toContain('流程文件')
    expect(wrapper.text()).toContain('源视频')
    expect(wrapper.text()).toContain('pano_source.mp4')
    expect(wrapper.find('.library-flow .file-row').exists()).toBe(true)
    // 源视频不进素材表格，也不占素材计数。
    expect(wrapper.findAll('.library-row')).toHaveLength(4)
    expect(wrapper.text()).toContain('素材 3')
    wrapper.unmount()
  })

  it('shows the flow master (pad/clip) with its submit state', () => {
    const data = info()
    data.assets = [
      { ...asset('pad', 'edit_master', 'seedance_input.mp4', 'video'), source: 'flow', role_fixed: true },
    ]
    data.submit_entries = [{ asset: 'pad', role: 'edit_master' }]
    const wrapper = mountView(data)
    const flow = wrapper.find('.library-flow')
    expect(flow.exists()).toBe(true)
    expect(flow.text()).toContain('母版')
    expect(flow.text()).toContain('seedance_input.mp4')
    expect(flow.text()).toContain('已加入本次提交')
    // 流程母版不参与素材表格与筛选计数，但算进「被引用」。
    expect(wrapper.findAll('.library-row')).toHaveLength(1) // 只有表头
    expect(wrapper.text()).toContain('素材 0')
    expect(wrapper.text()).toContain('流程文件 1')
    expect(wrapper.text()).toContain('被引用 1')
    wrapper.unmount()
  })

  it('marks an unselected flow master as not submitted', () => {
    const data = info()
    data.assets = [
      { ...asset('clip', 'edit_master', 'clip.mp4', 'video'), source: 'flow', role_fixed: true },
    ]
    data.submit_entries = []
    const wrapper = mountView(data)
    expect(wrapper.find('.library-flow').text()).toContain('未加入本次提交')
    wrapper.unmount()
  })

  it('explains an empty library that still has flow files', () => {
    const data = info()
    data.assets = []
    data.source_file = SOURCE_FILE as unknown as CaseInfo['source_file']
    const wrapper = mountView(data)
    expect(wrapper.text()).toContain('这个 case 还没有素材文件')
    expect(wrapper.text()).toContain('不作为生成素材')
    wrapper.unmount()
  })
})
