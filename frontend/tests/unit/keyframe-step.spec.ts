import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/case', () => ({ fetchCase: vi.fn() }))

const keyframeApi = vi.hoisted(() => ({
  rounds: [] as unknown[],
  pick: vi.fn(),
  setRefs: vi.fn(),
}))

vi.mock('@/api/keyframe', () => ({
  fetchKeyframePrompt: vi.fn(async () => ({
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
  })),
  fetchKeyframeRounds: vi.fn(async () => ({ rounds: keyframeApi.rounds })),
  handoffKeyframe: vi.fn(),
  pickKeyframe: keyframeApi.pick,
  runKeyframe: vi.fn(),
  saveKeyframePrompt: vi.fn(),
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
    raw_frames: [rawFrame('first_frame_raw', '首帧原始图'), rawFrame('last_frame_raw', '尾帧原始图')],
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
    const handoff = wrapper
      .findAll('button')
      .find((item) => item.text().includes('交接给视频步'))!
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
