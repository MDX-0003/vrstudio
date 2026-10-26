import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import GenerateResults from '@/components/generate/GenerateResults.vue'
import type { RoundSummary } from '@/types/case-info'

const LinkStub = defineComponent({
  props: { href: String, target: String },
  template: '<a :href="href" :target="target"><slot /></a>',
})

const stubs = {
  'el-link': LinkStub,
  'el-icon': true,
  'el-tag': true,
  'el-empty': true,
  'el-alert': true,
  'el-button': true,
}

function round(): RoundSummary {
  return {
    mark: '*',
    round: 'gen-1',
    backend: 'mglc',
    stage: 'checked',
    gate: 'ok',
    result: 'yes',
    updated: '2026-10-26T01:00:00Z',
    round_dir: 'E:/repo/vrgen_runs/p1/gen-1',
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

function mountResults(rows: RoundSummary[]) {
  return mount(GenerateResults, {
    props: {
      recentOutput: null,
      canAdopt: false,
      adopting: false,
      latestUsableRound: null,
      visibleRoundRows: rows,
      roundsCount: rows.length,
      roundsHint: '',
      activeTask: null,
    },
    global: { stubs },
  })
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
