import { describe, expect, it } from 'vitest'
import {
  densify,
  lonLatFromDirection,
  directionFromLonLat,
  pointInPolygon,
  project,
  unproject,
  unwrapLongitudes,
} from '@/utils/erpMath'
import type { Pose, Viewport } from '@/utils/erpMath'

const pose: Pose = { yaw: 0.3, pitch: -0.15, fovH: (90 * Math.PI) / 180 }
const view: Viewport = { width: 1280, height: 720 }

describe('erpMath', () => {
  it('经纬度与球面方向互为逆运算', () => {
    for (const [lon, lat] of [[0, 0], [-120, 35], [179, -60], [-45, 80]]) {
      const [backLon, backLat] = lonLatFromDirection(directionFromLonLat(lon, lat))
      expect(backLon).toBeCloseTo(lon, 6)
      expect(backLat).toBeCloseTo(lat, 6)
    }
  })

  it('project 与 unproject 在视野内互为逆运算', () => {
    for (const [lon, lat] of [[17, 0], [17, 10], [17, -18]]) {
      const screen = project(lon, lat, pose, view)
      expect(screen).not.toBeNull()
      const [backLon, backLat] = unproject(screen![0], screen![1], pose, view)
      expect(backLon).toBeCloseTo(lon, 4)
      expect(backLat).toBeCloseTo(lat, 4)
    }
  })

  it('相机背后的点没有屏幕位置', () => {
    const behind: Pose = { yaw: Math.PI, pitch: 0, fovH: (90 * Math.PI) / 180 }
    expect(project(0, 0, behind, view)).toBeNull()
  })

  it('跨接缝的经度被连续化', () => {
    const verts = unwrapLongitudes([[179, 0], [-179, 0], [-177, 5]])
    expect(verts.map(([lon]) => lon)).toEqual([179, 181, 183])
  })

  it('densify 按最大步长细分每一条边', () => {
    const dense = densify([[0, 0], [30, 0], [30, 20]], 2)
    expect(dense.length).toBeGreaterThan(20)
    // 起点保留，且相邻点距离不超过步长
    expect(dense[0]).toEqual([0, 0])
    for (let i = 1; i < dense.length; i += 1) {
      const [lon0, lat0] = dense[i - 1]
      const [lon1, lat1] = dense[i]
      expect(Math.max(Math.abs(lon1 - lon0), Math.abs(lat1 - lat0))).toBeLessThanOrEqual(2.0001)
    }
  })

  it('点内判定在跨接缝时仍然正确', () => {
    const verts: Array<[number, number]> = [[170, -10], [190, -10], [190, 10], [170, 10]]
    expect(pointInPolygon(180, 0, verts)).toBe(true)
    expect(pointInPolygon(-175, 0, verts)).toBe(true)
    expect(pointInPolygon(-90, 0, verts)).toBe(false)
    expect(pointInPolygon(180, 30, verts)).toBe(false)
  })

  it('少于三个顶点不构成区域', () => {
    expect(pointInPolygon(0, 0, [[0, 0], [10, 0]])).toBe(false)
  })
})
