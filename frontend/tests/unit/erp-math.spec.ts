import { describe, expect, it } from 'vitest'
import {
  densify,
  lonLatFromDirection,
  directionFromLonLat,
  isPoleCapShape,
  longitudeSpan,
  pointInPolygon,
  pointInRegion,
  polygonSelfIntersects,
  polygonWinding,
  regionBoundary,
  regionErpPolygon,
  regionShapeIssue,
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

  it('识别绕经度超过一圈的区域', () => {
    // RL_video_02 的「区域 3」：绕顶部点了一圈还多，unwrap 后跨度 383°。
    const verts: Array<[number, number]> = [
      [3.7, 8.9], [-69.7, 18.2], [-138.1, 38.8], [-146.4, 50.2],
      [120.4, 31.1], [108.8, 19.5], [53.8, 16.6], [-19.8, 20.4],
    ]
    expect(longitudeSpan(verts)).toBeGreaterThan(360)
    expect(longitudeSpan([[0, 0], [90, 10], [180, 0], [-90, -10]])).toBeLessThan(360)
  })

  it('识别自交的边', () => {
    const bowtie: Array<[number, number]> = [[0, 0], [10, 10], [10, 0], [0, 10]]
    expect(polygonSelfIntersects(bowtie)).toBe(true)
    const rectangle: Array<[number, number]> = [[0, 0], [10, 0], [10, 10], [0, 10]]
    expect(polygonSelfIntersects(rectangle)).toBe(false)
  })

  it('绕极点一整圈的边界 winding 非零', () => {
    const ring: Array<[number, number]> = []
    for (let index = 0; index < 8; index += 1) ring.push([index * 45, 60])
    expect(Math.abs(polygonWinding(ring))).toBe(1)
    // 加了极点顶点也仍然绕一圈
    expect(Math.abs(polygonWinding([...ring, [0, 89]]))).toBe(1)
    expect(polygonWinding([[0, 0], [90, 0], [90, 10], [0, 10]])).toBe(0)
  })

  it('跨度 339° 的绕极盖帽也判为绕极点', () => {
    // 用户实际遇到的形状：-170° 走到 169°，纬度 60°，只差 21° 没走到接缝。
    const cap: Array<[number, number]> = []
    for (let index = 0; index <= 15; index += 1) cap.push([-170 + index * 22.6, 60])
    expect(longitudeSpan(cap)).toBeCloseTo(339, 6)
    expect(polygonWinding(cap)).toBe(1)
    expect(regionShapeIssue(cap)?.kind).toBe('winding')
  })

  it('形状校验把绕极点、跨度超限与自交分开', () => {
    const rectangle: Array<[number, number]> = [[0, 0], [10, 0], [10, 10], [0, 10]]
    expect(regionShapeIssue(rectangle)).toBeNull()

    const ring: Array<[number, number]> = []
    for (let index = 0; index < 8; index += 1) ring.push([index * 45, 60])
    const winding = regionShapeIssue(ring)
    expect(winding?.kind).toBe('winding')
    if (winding?.kind === 'winding') {
      expect(Math.abs(winding.winding)).toBe(1)
      expect(winding.hasPole).toBe(false)
    }
    const withPole = regionShapeIssue([...ring, [0, 89]])
    expect(withPole?.kind).toBe('winding')
    if (withPole?.kind === 'winding') expect(withPole.hasPole).toBe(true)

    // 绕出去再折返：扭转量为 0，但跨度 680°，切平面闭合后仍自重叠。
    const ribbon: Array<[number, number]> = [
      [0, 0], [170, 10], [340, 0], [510, 10], [680, 0],
      [680, -10], [510, -20], [340, -20], [170, -20], [0, -20],
    ]
    expect(polygonWinding(ribbon)).toBe(0)
    expect(longitudeSpan(ribbon)).toBeCloseTo(680, 6)
    expect(regionShapeIssue(ribbon)?.kind).toBe('span')

    const bowtie: Array<[number, number]> = [[0, 0], [10, 10], [10, 0], [0, 10]]
    expect(regionShapeIssue(bowtie)?.kind).toBe('self-intersect')
  })

  it('盖帽合成接缝短边与极点边，命中判定覆盖缺口', () => {
    const cap: Array<[number, number]> = []
    for (let index = 0; index <= 15; index += 1) cap.push([-170 + index * 22.6, 60])
    expect(isPoleCapShape(cap)).toBe(true)
    expect(regionShapeIssue(cap, 'north')).toBeNull()
    expect(polygonSelfIntersects(regionBoundary(cap, 'north'), false)).toBe(false)

    const boundary = regionBoundary(cap, 'north')
    expect(boundary).toHaveLength(cap.length + 1)
    expect(boundary[boundary.length - 1]).toEqual([190, 60])

    const polygon = regionErpPolygon(cap, 'north')
    expect(polygon.slice(-2)).toEqual([[190, 90], [-170, 90]])

    expect(pointInRegion(0, 80, cap, 'north')).toBe(true)
    expect(pointInRegion(175, 80, cap, 'north')).toBe(true)
    expect(pointInRegion(-175, 80, cap, 'north')).toBe(true)
    expect(pointInRegion(0, 50, cap, 'north')).toBe(false)
    expect(pointInRegion(0, -80, cap, 'north')).toBe(false)

    const south = cap.map(([lon]) => [lon, -60] as [number, number])
    expect(pointInRegion(0, -80, south, 'south')).toBe(true)
    expect(pointInRegion(0, 80, south, 'south')).toBe(false)
    // 同一个环标记成相反极点时，取的是球面上相反的一侧。
    expect(pointInRegion(0, -80, cap, 'south')).toBe(true)
  })

  it('盖帽要求恰好绕一圈，普通多边形不能带 pole', () => {
    const rectangle: Array<[number, number]> = [[0, 0], [10, 0], [10, 10], [0, 10]]
    expect(regionShapeIssue(rectangle, 'north')?.kind).toBe('winding')
    expect(isPoleCapShape(rectangle)).toBe(false)

    const twice: Array<[number, number]> = []
    for (let index = 0; index < 16; index += 1) twice.push([index * 45, 60])
    expect(isPoleCapShape(twice)).toBe(false)
    expect(regionShapeIssue(twice, 'north')?.kind).toBe('winding')
  })
})
