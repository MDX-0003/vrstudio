import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/case', () => ({ fetchCase: vi.fn() }))

const keyframeApi = vi.hoisted(() => ({
  rounds: [] as unknown[],
  pick: vi.fn(),
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
  setKeyframeSheet: vi.fn(),
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
import type { CaseInfo, RawFrame } from '@/types/case-info'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-link': LinkStub,
  'el-icon': true,
  'el-button': true,
  'el-alert': true,
  'el-select': true,
  'el-option': true,
  'el-radio-group': true,
  'el-radio-button': true,
  'el-input-number': true,
  'el-tag': true,
  'el-empty': true,
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

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    gates: { keyframe: { ok: false, ready: true } },
    keyframe: {
      enabled: true,
      error: null,
      sheet: null,
      sheet_candidates: [],
      first: null,
      last: null,
      extract: 'v1',
      stale: false,
      complete: false,
    },
    raw_frames: [
      rawFrame('first_frame_raw', '首帧原始图'),
      rawFrame('last_frame_raw', '尾帧原始图'),
    ],
    assets: [],
  } as unknown as CaseInfo
}

async function mountStep() {
  const wrapper = mount(KeyframeStep, {
    props: { caseName: 'p1', info: info() },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('KeyframeStep 原图入口', () => {
  it('links the two raw frames with name and rel path in the tooltip', async () => {
    const wrapper = await mountStep()
    const links = wrapper.findAll('.frame-media a')
    expect(links).toHaveLength(2)
    expect(links[0].attributes('href')).toBe(
      '/file?root=cases&p=p1%2Fextracts%2Fv1%2Ffirst_frame_raw.png',
    )
    expect(links[0].attributes('title')).toBe(
      'first_frame_raw.png\ncases/p1/extracts/v1/first_frame_raw.png',
    )
    wrapper.unmount()
  })

  it('puts the candidate open link beside the pick button, not inside it', async () => {
    keyframeApi.rounds = [
      {
        round: 'img-abc12345',
        key: 'k',
        stage: 'checked',
        step: 'first',
        model_id: null,
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
