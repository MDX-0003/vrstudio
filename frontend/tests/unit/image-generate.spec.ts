import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { routerKey } from 'vue-router'

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
import { runOperation } from '@/api/case'
import { fetchTasks } from '@/api/tasks'
import type { CaseInfo } from '@/types/case-info'
import type { TaskView } from '@/types/task'

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
  'el-input-number': true,
}

function imageInfo(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patchimg',
    assets: [],
    submit_selected: [],
    submit_roles: {},
    submit_entries: [],
    role_help: {},
    roles: [],
    outputs: [],
    adopt_round: null,
    adopt_index: null,
    rounds: { rounds: [], hint: '' },
    image_rounds: [
      {
        round: 'img-a',
        stage: 'checked',
        gate: 'ok',
        adopted: false,
        results: [
          { index: 1, name: 'c1.png', exists: true, url: '/img/round/p1/img-a/1' },
          { index: 2, name: 'c2.png', exists: true, url: '/img/round/p1/img-a/2' },
        ],
      },
    ],
    image_report: null,
    gates: {
      extract: { ok: true },
      generate: { ok: false, ready: true, reason: '' },
      merge: { ok: false, ready: false },
    },
    prompt: {
      template: 'image-edit',
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
    },
    generation_params: {
      output: [{ value: '2K', width: 2560, height: 1280 }],
      input: [],
      job: 'p1',
      modes: [],
      models: [],
      image_models: [{ id: 44, name: '专业图片-2.5', description: '' }],
      image_models_error: null,
      current: {
        resolution: '2K',
        scale: 'auto',
        mode: '',
        aspect_ratio: 'auto',
        session_id: 0,
        project_id: 0,
        episode: 0,
      },
      fixed: {
        video_model_id: 0,
        image_model_id: 44,
        duration_s: 0,
        num: 2,
      },
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

async function mountStep(info: CaseInfo = imageInfo(), push = vi.fn()) {
  vi.mocked(runOperation).mockClear()
  vi.mocked(fetchTasks).mockResolvedValue({ tasks: [] })
  const wrapper = mount(GenerateStep, {
    props: { caseName: 'p1', info },
    global: { stubs, provide: { [routerKey]: { push } } },
  })
  await flushPromises()
  return { wrapper, push }
}

describe('GenerateStep 图片流程', () => {
  it('adopts a candidate with both round and index', async () => {
    const { wrapper } = await mountStep()
    const buttons = wrapper.findAll('button').filter((item) => item.text().includes('选用这张'))
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    await flushPromises()
    expect(runOperation).toHaveBeenCalledWith('p1', 'adopt', { round: 'img-a', index: 1 })
    wrapper.unmount()
  })

  it('blocks submit until a project session is chosen', async () => {
    const { wrapper } = await mountStep()
    const submit = wrapper.findAll('button').find((item) => item.text().includes('提交生成'))
    expect(submit?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('图片生成按项目会话计费')
    wrapper.unmount()
  })

  it('marks the adopted candidate instead of offering its button', async () => {
    const data = imageInfo()
    data.adopt_round = 'img-a'
    data.adopt_index = 2
    data.image_rounds = [
      {
        round: 'img-a',
        stage: 'checked',
        gate: 'ok',
        adopted: true,
        results: [
          { index: 1, name: 'c1.png', exists: true, url: '/img/round/p1/img-a/1' },
          { index: 2, name: 'c2.png', exists: true, url: '/img/round/p1/img-a/2' },
        ],
      },
    ]
    const wrapper = mount(GenerateStep, {
      props: { caseName: 'p1', info: data },
      global: { stubs, provide: { [routerKey]: { push: vi.fn() } } },
    })
    await flushPromises()
    expect(
      wrapper.findAll('button').filter((item) => item.text().includes('选用这张')),
    ).toHaveLength(1)
    expect(wrapper.text()).toContain('当前采用：img-a 第 2 张')
    wrapper.unmount()
  })

  it('derives the status from the ledger instead of showing 未预演', async () => {
    const { wrapper } = await mountStep()
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).not.toContain('未预演')
    wrapper.unmount()
  })

  it('keeps showing results when a newer prepared round has no candidates', async () => {
    const data = imageInfo()
    data.image_rounds = [
      { round: 'img-empty', stage: 'prepared', adopted: false, results: [] },
      ...(data.image_rounds ?? []),
    ]
    const { wrapper } = await mountStep(data)
    expect(
      wrapper.findAll('button').filter((item) => item.text().includes('选用这张')),
    ).toHaveLength(2)
    expect(wrapper.text()).toContain('img-a · gate=ok')
    wrapper.unmount()
  })

  it('offers view / merge / compare entries for the adopted round', async () => {
    const data = imageInfo()
    data.adopt_round = 'img-a'
    data.adopt_index = 2
    data.image_rounds = (data.image_rounds ?? []).map((round) => ({ ...round, adopted: true }))
    const { wrapper, push } = await mountStep(data)

    const view = wrapper.find('a.adopted-link[href="/img/round/p1/img-a/2"]')
    expect(view.exists()).toBe(true)
    expect(view.text()).toContain('查看大图')

    const merge = wrapper.findAll('button').find((item) => item.text().includes('去拼接'))
    await merge?.trigger('click')
    expect(push).toHaveBeenCalledWith('/case/p1/merge')

    const compare = wrapper.findAll('button').find((item) => item.text().includes('去对比'))
    await compare?.trigger('click')
    expect(push).toHaveBeenCalledWith('/case/p1/compare')
    wrapper.unmount()
  })

  it('shows the recent task and its log tail on the image page', async () => {
    const task = {
      id: 'abc123',
      kind: 'submit',
      case: 'p1',
      owner_id: null,
      owner_name: null,
      project_id: null,
      episode_id: null,
      session_id: null,
      status: 'done',
      queue_position: null,
      created_at: '2026-11-02T09:49:45+08:00',
      started_at: '2026-11-02T09:49:46+08:00',
      finished_at: '2026-11-02T09:50:59+08:00',
      progress: null,
      log_path: 'logs/tasks/20261102/abc123_submit_p1.log',
      error: null,
      request_id: null,
      tail: ['gate ok 1 candidate(s)'],
    } as TaskView
    vi.mocked(fetchTasks).mockResolvedValue({ tasks: [task] })
    const wrapper = mount(GenerateStep, {
      props: { caseName: 'p1', info: imageInfo() },
      global: { stubs, provide: { [routerKey]: { push: vi.fn() } } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('最近任务')
    expect(wrapper.text()).toContain('submit')

    const details = wrapper.findAll('button').find((item) => item.text().includes('查看技术详情'))
    await details?.trigger('click')
    expect(wrapper.text()).toContain('gate ok 1 candidate(s)')
    wrapper.unmount()
  })
})
