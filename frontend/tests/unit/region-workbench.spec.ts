import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/relight', () => ({ saveRelightRegions: vi.fn() }))
vi.mock('@/api/case', () => ({ saveRelightDraft: vi.fn() }))

import RegionWorkbench from '@/components/relight/RegionWorkbench.vue'
import { saveRelightRegions } from '@/api/relight'
import { pointInPolygon } from '@/utils/erpMath'
import type { RelightPayload, RelightRegion } from '@/types/case-info'
import type { DefaultParams } from '@/constants/relightParams'

// jsdom 没有 ResizeObserver 与 WebGL；视图层整体换成透传事件的桩。
vi.stubGlobal('ResizeObserver', class {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
})

const ViewerStub = {
  props: ['sourceUrl', 'media', 'regions', 'selectedId', 'draft', 'mode', 'highlight'],
  template: '<div class="viewer-stub" :data-selected="selectedId" :data-count="regions.length" :data-mode="mode"></div>',
}
const TimelineStub = { props: ['regions', 'selectedId'], template: '<div class="timeline-stub"></div>' }

function region(id: string, verts: Array<[number, number]>, extra: Partial<RelightRegion> = {}): RelightRegion {
  return {
    id,
    name: id,
    color: '#f59e0b',
    enabled: true,
    verts,
    pole: null,
    feather_deg: 3,
    params: { strength: null, local_shading: null, clip: null },
    ranges: null,
    ...extra,
  }
}

const defaults: DefaultParams = { strength: 0.6, local_shading: 0, clip: null }

function payload(regions: RelightRegion[]): RelightPayload {
  return {
    media: 'image',
    regions: { version: 1, regions },
    regions_error: null,
  } as unknown as RelightPayload
}

function mountWorkbench(regions: RelightRegion[]) {
  return mount(RegionWorkbench, {
    props: {
      caseName: 'p1',
      payload: payload(regions),
      sourceUrl: null,
      defaults,
    },
    global: {
      stubs: {
        ErpRegionViewer: ViewerStub,
        RegionTimeline: TimelineStub,
        'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
        'el-alert': { template: '<div />' },
      },
    },
  })
}

describe('RegionWorkbench 排序与布尔绘制模式', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(saveRelightRegions).mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('区域行提供四档排序，排序就是数组顺序并触发保存', async () => {
    const wrapper = mountWorkbench([
      region('a', [[0, 0], [10, 0], [10, 10], [0, 10]]),
      region('b', [[5, 5], [15, 5], [15, 15], [5, 15]]),
      region('c', [[20, 0], [30, 0], [30, 10], [20, 10]]),
    ])
    const rows = wrapper.findAll('.region-row')
    expect(rows).toHaveLength(3)
    // 「移到最上层」把第一个区域挪到数组末尾：列表里靠后的行覆盖靠前的重叠处。
    await rows[0].find('button[title^="移到最上层"]').trigger('click')
    expect((wrapper.vm.regions as RelightRegion[]).map((item) => item.id)).toEqual(['b', 'c', 'a'])
    vi.advanceTimersByTime(800)
    await flushPromises()
    expect(saveRelightRegions).toHaveBeenCalled()
  })

  it('减去 / 相交要求先选中一个普通区域', async () => {
    const wrapper = mountWorkbench([])
    await wrapper.findAll('button').find((button) => button.text() === '减去')!.trigger('click')
    expect(wrapper.vm.mode).toBe('orbit')
  })

  it('减去模式从选中区域裁掉咬口并保存裁剪后的 verts', async () => {
    const target = region('a', [[0, 0], [10, 0], [10, 10], [0, 10]])
    const wrapper = mountWorkbench([target])
    wrapper.vm.selectedId = 'a'
    await wrapper.findAll('button').find((button) => button.text() === '减去')!.trigger('click')
    expect(wrapper.vm.mode).toBe('poly')
    // 模拟在画布上落点并闭合（工作台把草稿闭合翻译成裁剪）。
    wrapper.vm.addPoint(4, -2)
    wrapper.vm.addPoint(6, -2)
    wrapper.vm.addPoint(6, 4)
    wrapper.vm.addPoint(4, 4)
    wrapper.vm.closeDraft()
    const after = (wrapper.vm.regions as RelightRegion[])[0]
    // 咬口穿过底边、上沿留在目标内：环由 4 个目标角 + 2 个入点 + 咬口上沿 2 个角组成。
    expect(after.verts).toHaveLength(8)
    expect(pointInPolygon(5, 2, after.verts)).toBe(false)
    expect(pointInPolygon(2, 2, after.verts)).toBe(true)
    expect(pointInPolygon(8, 2, after.verts)).toBe(true)
    vi.advanceTimersByTime(800)
    await flushPromises()
    expect(saveRelightRegions).toHaveBeenCalled()
  })

  it('盖帽区域不能做布尔目标', async () => {
    const cap: Array<[number, number]> = []
    for (let index = 0; index <= 7; index += 1) cap.push([-170 + index * 45, 60])
    const wrapper = mountWorkbench([region('cap', cap, { pole: 'north' })])
    wrapper.vm.selectedId = 'cap'
    await wrapper.findAll('button').find((button) => button.text() === '相交')!.trigger('click')
    expect(wrapper.vm.mode).toBe('orbit')
  })
})
