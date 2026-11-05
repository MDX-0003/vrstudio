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
import OutputModeSelect from '@/components/assets/OutputModeSelect.vue'
import { runOperation, setMergeInput } from '@/api/case'
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
  props: { modelValue: Boolean, mode: String, title: String, onlyKind: String },
  emits: ['confirm', 'updated'],
  template: '<div class="picker-stub" />',
})

const UploadStub = defineComponent({
  props: {
    modelValue: Boolean,
    destination: String,
    caseName: String,
    mergeInputKind: String,
  },
  emits: ['added'],
  template: '<div class="upload-stub" />',
})

const SelectStub = defineComponent({
  props: { modelValue: String },
  emits: ['update:modelValue'],
  template: '<select><slot /></select>',
})

const LinkStub = defineComponent({
  props: { href: String, type: String, underline: String, target: String },
  template: '<a :href="href"><slot /></a>',
})

const OptionStub = defineComponent({
  props: { value: String, label: String },
  template: '<option :value="value">{{ label }}</option>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-alert': AlertStub,
  'el-tag': true,
  'el-link': LinkStub,
  'el-icon': true,
  'el-empty': true,
  'el-checkbox': true,
  'el-select': SelectStub,
  'el-option': OptionStub,
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

async function mountWith(
  pad: CaseInfo['gates']['pad'],
  recompose: CaseInfo['gates']['recompose'],
  step = 'pad',
) {
  const wrapper = mount(MergeStep, {
    props: { caseName: 'p1', info: info(pad, recompose), step },
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

function imageInfo(
  mergeInput: string | null = null,
  withAssets = true,
  withOutputOptions = true,
): CaseInfo {
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
    output_options: withOutputOptions
      ? [
          { mode: 'case', width: 8192, height: 4096 },
          { mode: 'input', width: 856, height: 428 },
        ]
      : [{ mode: 'case', width: 8192, height: 4096 }],
    assets: withAssets
      ? [
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
        ]
      : [],
    step_files: {
      extract: { inputs: [], outputs: [] },
      generate: { inputs: [], outputs: [] },
      merge: { inputs: [], outputs: [] },
    },
  } as unknown as CaseInfo
}

async function mountImage(
  mergeInput: string | null = null,
  withAssets = true,
  withOutputOptions = true,
) {
  const wrapper = mount(MergeStep, {
    props: { caseName: 'p1', info: imageInfo(mergeInput, withAssets, withOutputOptions) },
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
    expect(dialog.props('mergeInputKind')).toBe('image')
    expect(dialog.props('modelValue')).toBe(true)
    wrapper.unmount()
  })

  it('writes the picked library file as [merge] input', async () => {
    vi.mocked(setMergeInput).mockClear()
    const wrapper = await mountImage()
    const picker = wrapper.findComponent(PickerStub)
    expect(picker.props('mode')).toBe('image')
    expect(picker.props('onlyKind')).toBe('image')
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

  it('offers both output sizes and sends the chosen mode', async () => {
    vi.mocked(runOperation).mockClear()
    const wrapper = await mountImage()
    const labels = wrapper.findAll('option').map((item) => item.text())
    expect(labels).toContain('案例分辨率 8192×4096')
    expect(labels).toContain('跟随输入 856×428')

    const select = wrapper.findComponent(OutputModeSelect)
    expect(select.props('modelValue')).toBe('case')
    select.vm.$emit('update:modelValue', 'input')
    await flushPromises()
    expect(select.props('modelValue')).toBe('input')

    await button(wrapper, '开始拼接').trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'merge', { output_mode: 'input' })
    wrapper.unmount()
  })

  it('hides the output select when only the case size is available', async () => {
    const wrapper = await mountImage(null, true, false)
    expect(wrapper.findComponent(SelectStub).exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('MergeStep 拼接输入（fullredraw 视频）', () => {
  function videoInfo(mergeInput: string | null = null, withAssets = true): CaseInfo {
    return {
      flow: 'fullredraw',
      gates: {
        pad: { ok: true, ready: true },
        generate: { ok: false, ready: true },
        recompose: { ok: false, ready: true },
        compare: { ok: false },
      },
      outputs: [],
      merge_input: mergeInput,
      output_options: [
        { mode: 'case', width: 8192, height: 4096 },
        { mode: 'input', width: 856, height: 428 },
      ],
      assets: withAssets
        ? [
            {
              id: 'v1',
              kind: 'video',
              role: 'merge_input',
              label: '拼接输入',
              path: 'assets/v1_edited.mp4',
              source: 'asset',
              role_fixed: false,
              url: '',
              name: 'v1_edited.mp4',
              rel: 'cases/p1/assets/v1_edited.mp4',
              exists: true,
              mtime: null,
              size: 1024,
              width: 4668,
              height: 2000,
              duration: 10,
              fps: 30,
              open_url: '',
            },
          ]
        : [],
      step_files: {
        pad: { inputs: [], outputs: [] },
        recompose: { inputs: [], outputs: [] },
      },
    } as unknown as CaseInfo
  }

  async function mountVideo(
    mergeInput: string | null = null,
    withAssets = true,
    step = 'recompose',
  ) {
    const wrapper = mount(MergeStep, {
      props: { caseName: 'p1', info: videoInfo(mergeInput, withAssets), step },
      global: { stubs },
    })
    await flushPromises()
    return wrapper
  }

  it('renders the same controls and points them at video files', async () => {
    const wrapper = await mountVideo()
    expect(button(wrapper, '上传并用作拼接输入').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('21:9 画布视频')

    const dialog = wrapper.findComponent(UploadStub)
    expect(dialog.props('mergeInputKind')).toBe('video')
    const picker = wrapper.findComponent(PickerStub)
    expect(picker.props('mode')).toBe('video')
    expect(picker.props('onlyKind')).toBe('video')
    wrapper.unmount()
  })

  it('writes the picked video as [merge] input', async () => {
    vi.mocked(setMergeInput).mockClear()
    const wrapper = await mountVideo()
    wrapper.findComponent(PickerStub).vm.$emit('confirm', ['v1'])
    await flushPromises()
    expect(setMergeInput).toHaveBeenCalledWith('p1', 'v1')
    wrapper.unmount()
  })

  it('clears the pointer back to the adopted result', async () => {
    vi.mocked(setMergeInput).mockClear()
    const wrapper = await mountVideo('v1')
    await button(wrapper, '用回生成结果').trigger('click')
    await flushPromises()
    expect(setMergeInput).toHaveBeenCalledWith('p1', null)
    wrapper.unmount()
  })

  it('sends the chosen output mode with 裁回原尺寸', async () => {
    vi.mocked(runOperation).mockClear()
    const wrapper = await mountVideo()
    const labels = wrapper.findAll('option').map((item) => item.text())
    expect(labels).toContain('案例分辨率 8192×4096')
    expect(labels).toContain('跟随输入 856×428')

    const select = wrapper.findComponent(OutputModeSelect)
    expect(select.props('modelValue')).toBe('case')
    select.vm.$emit('update:modelValue', 'input')
    await flushPromises()

    await button(wrapper, '裁回原尺寸').trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'recompose', { output_mode: 'input' })
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
      'pad',
    )
    expect(button(wrapper, '扩充画面').attributes('disabled')).toBeUndefined()
    // F6：pad 自己的原因要显示出来。
    expect(wrapper.text()).toContain('还没有扩充过画面')
    wrapper.unmount()
  })

  it('shows the recompose gate reason on the recompose step only', async () => {
    const wrapper = await mountWith(
      { ok: true, ready: true },
      { ok: false, ready: false, reason: '先采用一次生成结果' },
      'recompose',
    )
    expect(wrapper.text()).toContain('先采用一次生成结果')
    expect(wrapper.text()).not.toContain('还没有扩充过画面')
    wrapper.unmount()
  })

  it('disables 扩充画面 only when the pad gate is not ready', async () => {
    const wrapper = await mountWith({ ok: true, ready: false }, { ok: false, ready: false })
    expect(button(wrapper, '重新扩充').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps 扩充画面 enabled after pad has run (ok=true)', async () => {
    const wrapper = await mountWith({ ok: true, ready: true }, { ok: false, ready: false })
    expect(button(wrapper, '重新扩充').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables 裁回原尺寸 until the recompose gate is ready', async () => {
    const wrapper = await mountWith(
      { ok: true, ready: true },
      { ok: false, ready: false, reason: '先采用' },
      'recompose',
    )
    expect(button(wrapper, '裁回原尺寸').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})

describe('MergeStep 按步骤拆分的扩充页（fullredraw 视频）', () => {
  const uploadFiles = [
    {
      name: 'upload_pad_4398x1884.mp4',
      path: '/c/derived/upload_pad_4398x1884.mp4',
      rel: 'derived/upload_pad_4398x1884.mp4',
      exists: true,
      mtime: 1,
      size: 9_100_000,
      open_url: '/file?root=cases&p=derived%2Fupload_pad_4398x1884.mp4',
    },
    {
      name: 'upload_pad_mask_4398x1884.png',
      path: '/c/derived/upload_pad_mask_4398x1884.png',
      rel: 'derived/upload_pad_mask_4398x1884.png',
      exists: true,
      mtime: 1,
      size: 8_400_000,
      open_url: '/file?root=cases&p=derived%2Fupload_pad_mask_4398x1884.png',
    },
    {
      name: 'upload_pad_4398x1884.json',
      path: '/c/derived/upload_pad_4398x1884.json',
      rel: 'derived/upload_pad_4398x1884.json',
      exists: true,
      mtime: 1,
      size: 1_024,
      open_url: '/file?root=cases&p=derived%2Fupload_pad_4398x1884.json',
    },
  ]

  function padInfo(
    options: {
      uploads?: typeof uploadFiles
      padUpload?: Record<string, unknown> | null
      scale?: string
    } = {},
  ): CaseInfo {
    return {
      flow: 'fullredraw',
      gates: {
        pad: { ok: true, ready: true },
        generate: { ok: false, ready: true },
        recompose: { ok: false, ready: true },
        compare: { ok: false },
      },
      outputs: [],
      output_options: [],
      assets: [],
      generation_params: {
        current: { scale: options.scale ?? 'auto', mode: 'videoEdit' },
        fixed: { video_model_id: 37 },
      },
      pad_upload: options.padUpload ?? null,
      step_files: {
        pad: {
          inputs: [],
          outputs: [
            {
              name: 'seedance_input.mp4',
              path: '/c/seedance_input.mp4',
              rel: 'seedance_input.mp4',
              exists: true,
              mtime: 1,
              size: 12_900_000,
              open_url: '/file?root=cases&p=seedance_input.mp4',
            },
          ],
          uploads: options.uploads ?? [],
        },
        recompose: { inputs: [], outputs: [] },
      },
    } as unknown as CaseInfo
  }

  async function mountPad(options: Parameters<typeof padInfo>[0] = {}) {
    const wrapper = mount(MergeStep, {
      props: { caseName: 'p1', info: padInfo(options), step: 'pad' },
      global: { stubs },
    })
    await flushPromises()
    return wrapper
  }

  it('shows only the pad controls and both file groups', async () => {
    const wrapper = await mountPad({ uploads: uploadFiles })
    expect(wrapper.text()).toContain('扩充产物')
    expect(wrapper.text()).toContain('上传件')
    expect(wrapper.text()).toContain('seedance_input.mp4')
    expect(wrapper.text()).toContain('upload_pad_4398x1884.mp4')
    // 裁回控件只在裁回步出现。
    expect(wrapper.findAll('button').some((b) => b.text().includes('上传并用作拼接输入'))).toBe(
      false,
    )
    expect(wrapper.findAll('button').some((b) => b.text().includes('裁回原尺寸'))).toBe(false)
    wrapper.unmount()
  })

  it('links every upload file to a download URL', async () => {
    const wrapper = await mountPad({ uploads: uploadFiles })
    const hrefs = wrapper.findAll('a').map((a) => a.attributes('href') ?? '')
    const downloads = hrefs.filter((href) => href.includes('download=1'))
    expect(downloads).toHaveLength(3)
    expect(downloads[0]).toContain('upload_pad_4398x1884.mp4')
    wrapper.unmount()
  })

  it('offers 生成上传件 when nothing has been exported yet', async () => {
    vi.mocked(runOperation).mockClear()
    const wrapper = await mountPad()
    expect(wrapper.text()).toContain('还没有上传件')
    await button(wrapper, '生成上传件').trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'export_upload', {})
    wrapper.unmount()
  })

  it('warns when the saved generation params no longer match the upload copy', async () => {
    const wrapper = await mountPad({
      uploads: uploadFiles,
      scale: 'half',
      padUpload: {
        file: uploadFiles[0],
        upload: { width: 4398, height: 1884, pixels: 8_285_832 },
        limit: { max_pixel: 8_295_044 },
        scale: 'auto',
        mode: 'videoEdit',
        video_model_id: 37,
      },
    })
    expect(wrapper.text()).toContain('当前生成参数已变')
    expect(wrapper.text()).toContain('8,295,044')
    wrapper.unmount()
  })

  it('describes the upload copy from its sidecar', async () => {
    const wrapper = await mountPad({
      uploads: uploadFiles,
      padUpload: {
        file: uploadFiles[0],
        upload: { width: 4398, height: 1884, pixels: 8_285_832 },
        limit: { max_pixel: 8_295_044 },
        scale: 'auto',
        mode: 'videoEdit',
        video_model_id: 37,
      },
    })
    expect(wrapper.text()).toContain('model 37 · videoEdit · scale auto')
    expect(wrapper.text()).toContain('上限 8,295,044 像素')
    expect(wrapper.text()).not.toContain('当前生成参数已变')
    wrapper.unmount()
  })
})
