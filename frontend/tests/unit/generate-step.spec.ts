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
import type { CaseInfo } from '@/types/case-info'

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled"><slot /></button>',
})

const stubs = {
  'el-button': ButtonStub,
  'el-select': true,
  'el-option': true,
  'el-checkbox': true,
  'el-empty': true,
  'el-tag': true,
  'el-alert': true,
  'el-icon': true,
  'el-drawer': true,
  'el-input': true,
  AssetUploadDialog: true,
}

function info(): CaseInfo {
  return {
    name: 'p1',
    flow: 'patch',
    assets: [],
    submit_selected: [],
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
      fixed: { model_id: 37, duration_s: 10, num: 1 },
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

async function mountStep() {
  const wrapper = mount(GenerateStep, {
    props: { caseName: 'p1', info: info() },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('GenerateStep', () => {
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
})
