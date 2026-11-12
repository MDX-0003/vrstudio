import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/case', () => ({ fetchCase: vi.fn(), saveParams: vi.fn() }))
vi.mock('@/api/sessions', () => ({
  fetchSessions: vi.fn(async () => ({
    fetched_at: '',
    projects: [
      {
        id: 1,
        name: '项目 A',
        episodes: [
          {
            no: 1,
            id: 1,
            name: '第一集',
            sessions: [
              { id: 10, name: '会话 A' },
              { id: 11, name: '会话 B' },
            ],
          },
        ],
      },
    ],
  })),
}))

const keyframeApi = vi.hoisted(() => ({
  rounds: [] as unknown[],
  pick: vi.fn(),
  setRefs: vi.fn(),
  run: vi.fn(),
}))

const promptPayload = {
  template: 'keyframe-first',
  available: [],
  template_text: '',
  placeholders: [],
  geometry_keys: [],
  computed_keys: [],
  labels: {},
  help: {},
  vars: {},
  rendered: '',
  render_error: null,
}

vi.mock('@/api/keyframe', () => ({
  fetchKeyframePrompt: vi.fn(async () => promptPayload),
  fetchKeyframeRounds: vi.fn(async () => ({ rounds: keyframeApi.rounds })),
  handoffKeyframe: vi.fn(),
  pickKeyframe: keyframeApi.pick,
  runKeyframe: keyframeApi.run,
  saveKeyframePrompt: vi.fn(async () => promptPayload),
  setKeyframeRefs: keyframeApi.setRefs,
}))

vi.mock('@/composables/useCaseTasks', async () => {
  const { ref } = await import('vue')
  return {
    useCaseTasks: () => ({
      latest: ref(null),
      tasks: ref([]),
      error: ref(''),
      refresh: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
    }),
  }
})

import KeyframeStep from '@/views/wizard/KeyframeStep.vue'
import KeyframeSubmitBar from '@/components/generate/KeyframeSubmitBar.vue'
import { saveParams } from '@/api/case'
import type { CaseInfo, FileInfo, KeyframePick, RawFrame } from '@/types/case-info'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-link': LinkStub,
}

function rawFrame(role: string, label: string): RawFrame {
  const name = role === 'first_frame_raw' ? 'first_frame_raw.png' : 'last_frame_raw.png'
  return {
    id: role === 'first_frame_raw' ? 'first_raw' : 'last_raw',
    role,
    label,
    name,
    path: `E:/repo/cases/p1/extracts/v1/${name}`,
    rel: `cases/p1/extracts/v1/${name}`,
    exists: true,
    mtime: null,
    width: 1920,
    height: 1080,
    size: 2048,
    url: `/img/asset/p1/${role}`,
    open_url: `/file?root=cases&p=p1%2Fextracts%2Fv1%2F${name}`,
  }
}

function frameFile(name: string): FileInfo {
  return {
    name,
    path: `E:/repo/cases/p1/assets/${name}`,
    rel: `cases/p1/assets/${name}`,
    exists: true,
    mtime: null,
    size: 10,
    open_url: `/file?root=cases&p=cases%2Fp1%2Fassets%2F${name}`,
  }
}

function pick(index: number, asset: string, name: string): KeyframePick {
  return { round: 'img-abc12345', index, asset, file: frameFile(name) }
}

function info(overrides: Partial<CaseInfo['keyframe']> = {}): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    gates: { keyframe: { ok: false, ready: true } },
    keyframe: {
      enabled: true,
      error: null,
      refs_default: false,
      role_help: {
        subject_ref: '人物三视图：唯一外观依据。',
        detail_ref: '细节参考：只继承所指细节的外观。',
      },
      refs: [
        {
          asset: 'i1',
          role: 'subject_ref',
          note: '',
          label: 'i1_大臣_三视图.png',
          url: '/img/asset/p1/i1',
          exists: true,
          file: frameFile('i1_大臣_三视图.png'),
        },
        {
          asset: 'i3',
          role: 'detail_ref',
          note: '头冠纹样',
          label: 'i3_detail.png',
          url: '/img/asset/p1/i3',
          exists: true,
          file: frameFile('i3_detail.png'),
        },
      ],
      first: pick(1, 'i2', 'i2_keyframe-1.png'),
      last: null,
      extract: 'v1',
      stale: false,
      complete: false,
      ...overrides,
    },
    raw_frames: [
      rawFrame('first_frame_raw', '首帧原始图'),
      rawFrame('last_frame_raw', '尾帧原始图'),
    ],
    assets: [],
    submit_entries: [],
    submit_roles: null,
    role_help: {},
  } as unknown as CaseInfo
}

async function mountStep(data: CaseInfo = info()) {
  const wrapper = mount(KeyframeStep, {
    props: { caseName: 'p1', info: data },
    global: { plugins: [ElementPlus], stubs },
  })
  await flushPromises()
  return wrapper
}

/** 带扣费会话与图片模型清单的 case：底栏要能读回已选会话。 */
function sessionInfo(): CaseInfo {
  const data = info()
  data.generation_params = {
    current: {
      resolution: '1080p',
      scale: 'auto',
      mode: 'firstLastFrame',
      aspect_ratio: '16:9',
      session_id: 10,
      project_id: 1,
      episode: 1,
    },
    fixed: { video_model_id: 37, image_model_id: 44, duration_s: 10, num: 1 },
    image_models: [
      { id: 44, name: '专业图片-2.5', description: '' },
      { id: 46, name: '万能编辑2.1', description: '' },
    ],
    image_models_error: null,
    image: {
      model_id: 44,
      name: '专业图片-2.5',
      ratios: ['1:1', '9:16', '16:9', '3:2', '2:3', '3:4', '4:3', '21:9'],
      resolutions: ['1K', '2K', '4K'],
      max_num: 4,
      default_num: 4,
    },
  } as unknown as CaseInfo['generation_params']
  return data
}

function round(name: string, step: string, count = 1) {
  return {
    round: name,
    key: name,
    stage: 'checked',
    step,
    image_model_id: null,
    num: count,
    gate: {},
    results: Array.from({ length: count }, (_, i) => ({
      index: i + 1,
      url: `/img/keyframe/p1/${name}/${i + 1}`,
      name: `result_${i + 1}.png`,
      path: `E:/repo/vrgen_runs/p1/${name}/result_${i + 1}.png`,
      rel: `vrgen_runs/p1/${name}/result_${i + 1}.png`,
      exists: true,
      mtime: null,
      size: 10,
      open_url: `/file?root=workdir&p=p1%2F${name}%2Fresult_${i + 1}.png`,
    })),
  }
}

beforeEach(() => {
  keyframeApi.rounds = []
  keyframeApi.run.mockClear()
  vi.mocked(saveParams).mockClear()
})

describe('KeyframeStep 四格输入', () => {
  it('links the current raw frame with name and rel path in the tooltip', async () => {
    const wrapper = await mountStep()
    const link = wrapper.findAll('.kf-slot')[0].find('a')
    expect(link.attributes('href')).toBe(
      '/file?root=cases&p=p1%2Fextracts%2Fv1%2Ffirst_frame_raw.png',
    )
    expect(link.attributes('title')).toBe(
      'first_frame_raw.png\ncases/p1/extracts/v1/first_frame_raw.png',
    )
    wrapper.unmount()
  })

  it('shows the bound sheet thumbnail from the asset route', async () => {
    const wrapper = await mountStep()
    const sheet = wrapper.findAll('.kf-slot')[1]
    expect(sheet.find('img').attributes('src')).toBe('/img/asset/p1/i1')
    expect(sheet.text()).toContain('i1_大臣_三视图.png')
    wrapper.unmount()
  })

  it('renders the reference list with a locked primary and an editable detail row', async () => {
    const wrapper = await mountStep()
    const rows = wrapper.findAll('.ref-row')
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => row.find('.ref-no').text())).toEqual(['2', '3'])
    expect(rows[0].text()).toContain('人物三视图（主图）')
    expect(rows[0].find('.role-select').exists()).toBe(false)
    expect(rows[1].find('.role-select').exists()).toBe(true)
    expect((rows[1].find('.ref-note input').element as HTMLInputElement).value).toBe('头冠纹样')
    wrapper.unmount()
  })

  it('keeps 图3 for the last step only and shows the first pick as the target', async () => {
    const wrapper = await mountStep()
    const slots = wrapper.findAll('.kf-slot')
    expect(slots[2].text()).toContain('生成并定稿首帧后填入')
    expect(slots[3].find('img').attributes('src')).toBe('/img/asset/p1/i2')
    wrapper.unmount()
  })

  it('switches the input strip and target when the last step is selected', async () => {
    const wrapper = await mountStep()
    const radios = wrapper.findAll('input[type="radio"]')
    expect(radios).toHaveLength(2)
    await radios[1].setValue(true)
    await flushPromises()
    const slots = wrapper.findAll('.kf-slot')
    expect(slots[0].text()).toContain('原始尾帧')
    expect(slots[0].find('img').attributes('src')).toBe('/img/asset/p1/last_frame_raw')
    expect(slots[2].find('img').attributes('src')).toBe('/img/asset/p1/i2')
    expect(slots[3].text()).toContain('从下方候选里挑一张')
    expect(wrapper.findAll('.ref-row').map((row) => row.find('.ref-no').text())).toEqual([
      '2',
      '3',
      '4',
    ])
    wrapper.unmount()
  })

  it('disables handoff and states why until both frames are picked', async () => {
    const wrapper = await mountStep()
    const handoff = wrapper.findAll('button').find((item) => item.text().includes('交接给视频步'))!
    expect(handoff.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('尾帧未定稿')
    wrapper.unmount()
  })
})

describe('KeyframeStep 候选批次折叠', () => {
  it('只展开最新一批，更早的收进折叠项', async () => {
    keyframeApi.rounds = [
      round('img-newest', 'first'),
      round('img-mid', 'first'),
      round('img-oldest', 'first'),
    ]
    const wrapper = await mountStep()

    expect(wrapper.findAll('.candidate-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('更早的批次（2）')

    const toggle = wrapper.findAll('button').find((item) => item.text().includes('更早的批次'))!
    await toggle.trigger('click')
    expect(wrapper.findAll('.candidate-card')).toHaveLength(3)
    expect(wrapper.text()).toContain('收起更早的批次')
    wrapper.unmount()
  })

  it('当前定稿在旧批次时，那一批也保持展开', async () => {
    keyframeApi.rounds = [
      round('img-newest', 'first'),
      round('img-abc12345', 'first'),
      round('img-oldest', 'first'),
    ]
    const wrapper = await mountStep()

    // 最新一批 + 定稿所在批次（img-abc12345）常显，剩下 1 批折叠
    expect(wrapper.findAll('.candidate-card')).toHaveLength(2)
    expect(wrapper.findAll('.picked-mark')).toHaveLength(1)
    expect(wrapper.text()).toContain('更早的批次（1）')
    wrapper.unmount()
  })

  it('只有一批时不显示折叠项', async () => {
    keyframeApi.rounds = [round('img-abc12345', 'first')]
    const wrapper = await mountStep()

    expect(wrapper.findAll('.candidate-card')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('更早的批次')
    wrapper.unmount()
  })
})

describe('KeyframeStep 图片模型', () => {
  it('switches the model from the bottom bar and saves it', async () => {
    vi.mocked(saveParams).mockResolvedValue(info())
    const wrapper = await mountStep()
    const select = wrapper.findComponent('.kf-image-model')
    expect(select.exists()).toBe(true)
    select.vm.$emit('update:modelValue', 25)
    select.vm.$emit('change', 25)
    await flushPromises()
    expect(saveParams).toHaveBeenCalledWith('p1', { image_model_id: 25 })
    wrapper.unmount()
  })
})

describe('KeyframeStep 底部常驻条', () => {
  it('不再在候选标题栏放生成参数，参数只出现在固定底栏', async () => {
    const wrapper = await mountStep()
    const candidate = wrapper
      .findAll('.panel')
      .find((panel) => panel.find('.panel-title').text() === '首帧候选')!

    expect(candidate.find('.panel-head').text()).not.toContain('图片模型')
    expect(candidate.find('.panel-head').text()).not.toContain('候选张数')
    expect(candidate.find('.kf-image-model').exists()).toBe(false)
    expect(candidate.find('.panel-head').text()).toContain('刷新批次')
    expect(wrapper.find('.submit-bar .kf-image-model').exists()).toBe(true)
    expect(wrapper.find('.submit-bar').text()).toContain('生成首帧候选')
    wrapper.unmount()
  })

  it('未选项目会话时禁用生成并说明原因', async () => {
    const wrapper = await mountStep()
    const generate = wrapper.findAll('button').find((item) => item.text().includes('生成首帧候选'))!
    expect(generate.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.submit-bar').text()).toContain('未选项目会话')
    expect(wrapper.find('.submit-bar').text()).toContain(
      '先在底栏选择项目生成会话；图片生成按项目会话计费',
    )
    wrapper.unmount()
  })

  it('模型清单读不到时用详情里的名字，不退化成 id', async () => {
    const data = sessionInfo()
    data.generation_params!.image_models = []
    const wrapper = await mountStep(data)
    const bar = wrapper.findComponent(KeyframeSubmitBar)
    expect(bar.props('imageModelOptions')).toEqual([
      { id: 44, name: '专业图片-2.5', description: '不在当前清单里' },
    ])
    expect(wrapper.find('.submit-bar').text()).toContain('专业图片-2.5')
    wrapper.unmount()
  })

  it('分辨率、比例与候选数跟随所选图片模型的能力', async () => {
    const data = sessionInfo()
    data.generation_params!.fixed.image_model_id = 25
    data.generation_params!.image_models = [{ id: 25, name: 'Seedream 4.5', description: '' }]
    data.generation_params!.image = {
      model_id: 25,
      name: 'Seedream 4.5',
      ratios: ['16:9', '21:9'],
      resolutions: ['2K', '4K'],
      max_num: 2,
      default_num: 2,
    }
    const wrapper = await mountStep(data)
    const bar = wrapper.findComponent(KeyframeSubmitBar)
    expect(bar.props('resolutionOptions')).toEqual(['2K', '4K'])
    // patch 视口固定 16:9：模型支持 21:9 也不出现在下拉里。
    expect(bar.props('ratioOptions')).toEqual(['16:9'])
    expect(bar.props('maxNum')).toBe(2)
    wrapper.unmount()
  })

  it('切换图片模型后按新模型重算档位', async () => {
    const wrapper = await mountStep(sessionInfo())
    const bar = wrapper.findComponent(KeyframeSubmitBar)
    expect(bar.props('resolutionOptions')).toEqual(['1K', '2K', '4K'])

    const next = sessionInfo()
    next.generation_params!.fixed.image_model_id = 25
    next.generation_params!.image_models = [{ id: 25, name: 'Seedream 4.5', description: '' }]
    next.generation_params!.image = {
      model_id: 25,
      name: 'Seedream 4.5',
      ratios: ['16:9', '21:9'],
      resolutions: ['2K', '4K'],
      max_num: 2,
      default_num: 2,
    }
    vi.mocked(saveParams).mockResolvedValue(next)
    bar.vm.$emit('update:imageModelId', 25)
    bar.vm.$emit('model-change', 25)
    await flushPromises()
    await wrapper.setProps({ info: next })
    await flushPromises()

    expect(bar.props('resolutionOptions')).toEqual(['2K', '4K'])
    expect(bar.props('ratioOptions')).toEqual(['16:9'])
    expect(bar.props('maxNum')).toBe(2)
    wrapper.unmount()
  })

  it('切换会话后保存扣费会话，再从底栏提交候选', async () => {
    const data = sessionInfo()
    vi.mocked(saveParams).mockResolvedValue(data)
    const wrapper = await mountStep(data)
    const bar = wrapper.findComponent(KeyframeSubmitBar)
    bar.vm.$emit('update:selectedSession', '11')
    await flushPromises()

    const generate = wrapper.findAll('button').find((item) => item.text().includes('生成首帧候选'))!
    expect(generate.attributes('disabled')).toBeUndefined()
    await generate.trigger('click')
    await flushPromises()

    expect(saveParams).toHaveBeenCalledWith('p1', {
      session_id: 11,
      project_id: 1,
      episode: 1,
    })
    expect(keyframeApi.run).toHaveBeenCalledWith('p1', {
      step: 'first',
      ratio: '16:9',
      resolution: '2K',
      num: 4,
      image_model_id: 44,
    })
    wrapper.unmount()
  })
})

describe('KeyframeStep 候选图入口', () => {
  it('puts the candidate open link beside the pick button, not inside it', async () => {
    keyframeApi.rounds = [
      {
        round: 'img-abc12345',
        key: 'k',
        stage: 'checked',
        step: 'first',
        image_model_id: null,
        num: 4,
        gate: {},
        results: [
          {
            index: 1,
            url: '/img/keyframe/p1/img-abc12345/1',
            name: 'result_1.png',
            path: 'E:/repo/vrgen_runs/p1/img-abc12345/result_1.png',
            rel: 'vrgen_runs/p1/img-abc12345/result_1.png',
            exists: true,
            mtime: null,
            size: 10,
            open_url: '/file?root=workdir&p=p1%2Fimg-abc12345%2Fresult_1.png',
          },
        ],
      },
    ]
    keyframeApi.pick.mockClear()
    const wrapper = await mountStep()
    const open = wrapper.find('.candidate-wrap > a')
    expect(open.attributes('href')).toBe('/file?root=workdir&p=p1%2Fimg-abc12345%2Fresult_1.png')
    expect(open.attributes('title')).toBe('result_1.png\nvrgen_runs/p1/img-abc12345/result_1.png')
    expect(wrapper.find('.candidate-card a').exists()).toBe(false)
    await open.trigger('click')
    expect(keyframeApi.pick).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
