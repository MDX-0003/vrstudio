import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import GenerateResults from '@/components/generate/GenerateResults.vue'
import type { RoundSummary } from '@/types/case-info'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  emits: ['click'],
  template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
})

const TagStub = defineComponent({
  template: '<span class="tag-stub"><slot /></span>',
})

const stubs = {
  'el-link': LinkStub,
  'el-icon': true,
  'el-tag': TagStub,
  'el-empty': true,
  'el-alert': true,
  'el-button': ButtonStub,
}

function round(overrides: Partial<RoundSummary> = {}): RoundSummary {
  return {
    mark: '*',
    round: 'gen-1',
    backend: 'mglc',
    stage: 'checked',
    gate: 'ok',
    result: 'yes',
    updated: '2026-10-26T01:00:00Z',
    round_dir: 'E:/repo/vrgen_runs/p1/gen-1',
    ...overrides,
    files: {
      result: {
        name: 'result.mp4',
        path: 'E:/repo/vrgen_runs/p1/gen-1/result.mp4',
        rel: 'vrgen_runs/p1/gen-1/result.mp4',
        exists: true,
        mtime: null,
        size: 2048,
        open_url: '/file?root=workdir&p=p1%2Fgen-1%2Fresult.mp4',
      },
      run_json: {
        name: 'run.json',
        path: 'E:/repo/vrgen_runs/p1/gen-1/run.json',
        rel: 'vrgen_runs/p1/gen-1/run.json',
        exists: true,
        mtime: null,
        size: 512,
        open_url: '/file?root=workdir&p=p1%2Fgen-1%2Frun.json',
      },
    },
  }
}

function mountResults(rows: RoundSummary[], props: Record<string, unknown> = {}) {
  return mount(GenerateResults, {
    props: {
      recentOutput: null,
      adoptedRound: '',
      adoptingRound: null,
      visibleRoundRows: rows,
      roundsCount: rows.length,
      roundsHint: '',
      activeTask: null,
      ...props,
    },
    global: { stubs },
  })
}

function adoptButtons(wrapper: ReturnType<typeof mountResults>) {
  return wrapper.findAll('button').filter((item) => item.text().includes('选用'))
}

describe('GenerateResults 生成记录文件行', () => {
  it('shows the raw result and run.json for every round', () => {
    const wrapper = mountResults([round()])
    const links = wrapper.findAll('.round-files a')
    expect(links).toHaveLength(2)
    expect(links[0].attributes('href')).toBe('/file?root=workdir&p=p1%2Fgen-1%2Fresult.mp4')
    expect(links[0].text()).toContain('播放')
    expect(links[1].attributes('href')).toBe('/file?root=workdir&p=p1%2Fgen-1%2Frun.json')
    expect(links[1].text()).toContain('打开')
    expect(wrapper.find('.round-files').text()).toContain('vrgen_runs/p1/gen-1/run.json')
    wrapper.unmount()
  })

  it('renders no file rows for rounds without a files payload', () => {
    const row = round()
    delete row.files
    const wrapper = mountResults([row])
    expect(wrapper.find('.round-files').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('GenerateResults 按轮次选用', () => {
  it('emits adopt with the round payload when its 选用 is clicked', async () => {
    const wrapper = mountResults([round({ round: 'gen-1' }), round({ round: 'gen-2' })])
    const buttons = adoptButtons(wrapper)
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    expect(wrapper.emitted('adopt')).toHaveLength(1)
    expect(wrapper.emitted('adopt')![0][0]).toMatchObject({
      round: 'gen-1',
      round_dir: 'E:/repo/vrgen_runs/p1/gen-1',
    })
    wrapper.unmount()
  })

  it('shows the adopted tag instead of a button on the adopted round', () => {
    const wrapper = mountResults([round({ round: 'gen-1' }), round({ round: 'gen-2' })], {
      adoptedRound: 'gen-2',
    })
    expect(adoptButtons(wrapper)).toHaveLength(1)
    expect(adoptButtons(wrapper)[0].text()).toContain('选用')
    const heads = wrapper.findAll('.candidate-head')
    expect(heads[1].text()).toContain('已采用')
    expect(wrapper.text()).toContain('当前采用轮次：gen-2')
    wrapper.unmount()
  })

  it('shows no adopt button for rounds without a usable result', () => {
    const wrapper = mountResults([
      round({ round: 'gen-1', result: 'no', stage: 'failed' }),
      round({ round: 'gen-2' }),
    ])
    expect(adoptButtons(wrapper)).toHaveLength(1)
    wrapper.unmount()
  })

  it('disables every adopt button while one round is being adopted', () => {
    const wrapper = mountResults([round({ round: 'gen-1' }), round({ round: 'gen-2' })], {
      adoptingRound: 'gen-2',
    })
    const buttons = adoptButtons(wrapper)
    expect(buttons).toHaveLength(2)
    for (const button of buttons) {
      expect(button.attributes('disabled')).toBeDefined()
    }
    wrapper.unmount()
  })

  it('states when no round has been adopted', () => {
    const wrapper = mountResults([round()], { adoptedRound: '' })
    expect(wrapper.text()).toContain('尚未采用任何轮次')
    wrapper.unmount()
  })
})
