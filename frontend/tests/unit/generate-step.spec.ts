import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/case', () => ({
  fetchCase: vi.fn(),
  changeAssetRole: vi.fn(),
  removeAsset: vi.fn(),
  reorderAssets: vi.fn(),
  runOperation: vi.fn(),
  saveParams: vi.fn(),
  savePromptVar: vi.fn(),
  saveSubmitSelection: vi.fn(),
}))
vi.mock('@/api/sessions', () => ({
  fetchSessions: vi.fn(async () => ({ projects: [] })),
}))
vi.mock('@/api/tasks', () => ({
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
}))

import GenerateStep from '@/views/wizard/GenerateStep.vue'
import GenerateSubmitBar from '@/components/generate/GenerateSubmitBar.vue'
import { runOperation, saveParams } from '@/api/case'
import type { RoundSummary } from '@/types/case-info'
import type { AssetDetail, CaseInfo, OutputOption } from '@/types/case-info'

function asset(id: string, role: string, name: string): AssetDetail {
  return {
    id,
    kind: 'image',
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

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled"><slot /></button>',
})

const TagStub = defineComponent({
  template: '<span class="tag-stub"><slot /></span>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-select': true,
  'el-option': true,
  'el-checkbox': true,
  'el-empty': true,
  'el-tag': TagStub,
  'el-alert': true,
  'el-icon': true,
  'el-dialog': true,
  'el-drawer': true,
  'el-input': true,
}

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    assets: [
      asset('i2', 'first_frame', 'i2_first.png'),
      asset('i4', 'last_frame', 'i4_last.png'),
      asset('i9', 'subject_ref', 'i9_unselected.png'),
    ],
    submit_selected: ['i2', 'i4'],
    submit_roles: { i2: 'first_frame', i4: 'last_frame' },
    submit_entries: [
      { asset: 'i2', role: 'first_frame' },
      { asset: 'i4', role: 'last_frame' },
    ],
    role_help: {},
    roles: [
      {
        name: 'first_frame',
        label: '首帧定稿',
        help: '首帧定稿图（视频第一帧，锁起点构图与人物外观）',
        kinds: ['image'],
        modes: ['firstLastFrame'],
        open: true,
        reserved: false,
      },
      {
        name: 'last_frame',
        label: '尾帧定稿',
        help: '尾帧定稿图（视频最后一帧，锁终点位置、大小与朝向）',
        kinds: ['image'],
        modes: ['firstLastFrame'],
        open: true,
        reserved: false,
      },
    ],
    outputs: [],
    adopt_round: null,
    rounds: { rounds: [], hint: '' },
    gates: { generate: { ok: false, ready: false, reason: '缺素材' } },
    prompt: {
      template: 'character-repaint',
      available: [],
      template_text: '把 {subject|原主体} 重绘成 {target|重绘结果}',
      placeholders: ['subject', 'target'],
      geometry_keys: [],
      computed_keys: [],
      labels: { subject: '原主体', target: '重绘结果' },
      help: { subject: '写要被替换的主体' },
      vars: { target: '红色外套', subject: '蓝色机器人' },
      rendered: '把 蓝色机器人 重绘成 红色外套',
      render_error: null,
    },
    generation_params: {
      output: [{ value: '1080p', width: 1920, height: 1080 }],
      input: [{ value: 'auto', width: 1920, height: 1080, ok: true, reason: '' }],
      job: 'p1',
      modes: [
        {
          value: 'firstLastFrame',
          label: '首尾帧',
          takes_video: false,
          ratios: ['16:9'],
          max_count: 1,
          max_videos: 0,
          max_images: 2,
          video_required: false,
          duration: { type: 'range', min: 4, max: 10 },
        },
      ],
      current: {
        resolution: '1080p',
        scale: 'auto',
        mode: 'firstLastFrame',
        aspect_ratio: '16:9',
        session_id: 0,
        project_id: 0,
        episode: 0,
      },
      fixed: {
        video_model_id: 37,
        image_model_id: 44,
        duration_s: 10,
        num: 1,
      },
      models: [
        { id: 22, name: 'Seedance 4.0', description: '' },
        { id: 37, name: 'Seedance 2.5', description: '' },
        { id: 39, name: 'Seedance 5.0 Pro', description: '' },
      ],
      image_models: [
        { id: 44, name: '专业图片-2.5', description: '' },
        { id: 46, name: '万能编辑2.1', description: '' },
      ],
      image_models_error: null,
      saved: true,
    },
    step_files: {},
    meta: {
      owner_id: null,
      owner_name: null,
      created_at: null,
      updated_at: null,
      updated_by_id: null,
      updated_by_name: null,
      revision: 1,
    },
  } as unknown as CaseInfo
}

async function mountStep(infoData: CaseInfo = info()) {
  const wrapper = mount(GenerateStep, {
    props: { caseName: 'p1', info: infoData },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

function videoRound(name: string, updated: string): RoundSummary {
  return {
    mark: '*',
    round: name,
    backend: 'mglc',
    stage: 'checked',
    gate: 'ok',
    result: 'yes',
    updated,
    round_dir: `E:/repo/vrgen_runs/p1/${name}`,
  }
}

describe('GenerateStep', () => {
  it('renders only the submit entries, not the whole library', async () => {
    const wrapper = await mountStep()
    expect(wrapper.findAll('.ref-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('i2_first.png')
    expect(wrapper.text()).toContain('i4_last.png')
    expect(wrapper.text()).not.toContain('i9_unselected.png')
    wrapper.unmount()
  })

  it('renders prompt aliases in template order without showing internal keys', async () => {
    const wrapper = await mountStep()
    const text = wrapper.text()
    expect(text).toContain('原主体')
    expect(text).toContain('重绘结果')
    expect(text).not.toContain('subject')
    wrapper.unmount()
  })

  it('disables submit when the generate gate is not ready', async () => {
    const wrapper = await mountStep()
    const submit = wrapper.findAll('button').find((item) => item.text().includes('提交生成'))
    expect(submit?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('saves both model ids when the submit bar changes them', async () => {
    vi.mocked(saveParams).mockResolvedValue(info())
    const data = info()
    data.gates.generate = { ok: true, ready: true, reason: '' }
    const wrapper = await mountStep(data)
    const bar = wrapper.findComponent(GenerateSubmitBar)
    bar.vm.$emit('update:videoModelId', 39)
    bar.vm.$emit('update:imageModelId', 46)
    await flushPromises()
    const submit = wrapper.findAll('button').find((item) => item.text().includes('提交生成'))
    expect(submit?.attributes('disabled')).toBeUndefined()
    await submit!.trigger('click')
    await flushPromises()
    expect(saveParams).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ video_model_id: 39, image_model_id: 46 }),
    )
    wrapper.unmount()
  })

  it('keeps a saved model visible when it is missing from the list', async () => {
    const data = info()
    data.generation_params!.models = [{ id: 22, name: 'Seedance 4.0', description: '' }]
    data.generation_params!.fixed.video_model_id = 37
    const wrapper = await mountStep(data)
    const bar = wrapper.findComponent(GenerateSubmitBar)
    expect(bar.props('videoModelOptions')).toEqual([
      { id: 37, name: '模型 #37', description: '不在当前清单里' },
      { id: 22, name: 'Seedance 4.0', description: '' },
    ])
    wrapper.unmount()
  })

  it('adopts the clicked round by its round_dir', async () => {
    const data = info()
    data.rounds = {
      rounds: [
        videoRound('gen-a', '2026-10-30T04:30:39Z'),
        videoRound('gen-b', '2026-10-30T06:37:05Z'),
      ],
      hint: '',
    }
    const wrapper = await mountStep(data)
    const buttons = wrapper
      .findAll('button')
      .filter((item) => item.text().includes('选用'))
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'adopt', {
      round: 'E:/repo/vrgen_runs/p1/gen-a',
    })
    wrapper.unmount()
  })

  it('marks the adopted round instead of offering its button', async () => {
    const data = info()
    data.rounds = { rounds: [videoRound('gen-a', '2026-10-30T04:30:39Z')], hint: '' }
    data.adopt_round = 'gen-a'
    const wrapper = await mountStep(data)
    expect(
      wrapper.findAll('button').filter((item) => item.text().includes('选用')),
    ).toHaveLength(0)
    expect(wrapper.text()).toContain('已采用')
    expect(wrapper.text()).toContain('当前采用轮次：gen-a')
    wrapper.unmount()
  })

  it('refreshes the output tiers right after the model changes, without a reload', async () => {
    vi.useFakeTimers()
    try {
      const fresh = info()
      fresh.generation_params = {
        ...fresh.generation_params!,
        output: [
          { value: '480p', width: 854, height: 480 },
          { value: '720p', width: 1280, height: 720 },
          { value: '1080p', width: 1920, height: 1080 },
          { value: '4K', width: 3840, height: 2160 },
        ],
        fixed: { ...fresh.generation_params!.fixed, video_model_id: 22 },
      }
      vi.mocked(saveParams).mockResolvedValue(fresh)
      const wrapper = await mountStep()
      const bar = wrapper.findComponent(GenerateSubmitBar)
      expect(bar.props('resolutionOptions')).toHaveLength(1)

      bar.vm.$emit('update:videoModelId', 22)
      await vi.advanceTimersByTimeAsync(400)
      await flushPromises()

      const values = (bar.props('resolutionOptions') as OutputOption[]).map(
        (item) => item.value,
      )
      expect(values).toEqual(['480p', '720p', '1080p', '4K'])
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls back to a supported tier when the new model drops the saved one', async () => {
    vi.useFakeTimers()
    try {
      const fresh = info()
      fresh.generation_params = {
        ...fresh.generation_params!,
        output: [
          { value: '480p', width: 854, height: 480 },
          { value: '720p', width: 1280, height: 720 },
        ],
        fixed: { ...fresh.generation_params!.fixed, video_model_id: 23 },
      }
      vi.mocked(saveParams).mockResolvedValue(fresh)
      const wrapper = await mountStep()
      const bar = wrapper.findComponent(GenerateSubmitBar)

      bar.vm.$emit('update:videoModelId', 23)
      await vi.advanceTimersByTimeAsync(400)
      await flushPromises()

      expect(bar.props('resolution')).toBe('480p')
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
