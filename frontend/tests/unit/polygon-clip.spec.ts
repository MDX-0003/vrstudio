import { describe, expect, it } from 'vitest'
import {
  pointInPolygon,
  polygonIntersect,
  polygonSelfIntersects,
  polygonSubtract,
  regionShapeIssue,
} from '@/utils/erpMath'

type Verts = Array<[number, number]>

const square = (x0: number, y0: number, x1: number, y1: number): Verts => [
  [x0, y0],
  [x1, y0],
  [x1, y1],
  [x0, y1],
]

/** 两个环在容差内是同一个集合：顶点数一致且每个顶点都能在对方附近找到。 */
function expectSameRing(actual: Verts, expected: Verts, digits = 4): void {
  expect(actual).toHaveLength(expected.length)
  for (const point of actual) {
    const found = expected.some(
      (other) =>
        Math.abs(point[0] - other[0]) < 10 ** -digits
        && Math.abs(point[1] - other[1]) < 10 ** -digits,
    )
    expect(found, `顶点 ${point} 不在期望环里`).toBe(true)
  }
}

describe('polygonSubtract / polygonIntersect', () => {
  it('角上咬掉一块：结果是六边形，咬掉的角不在结果里', () => {
    const subject = square(0, 0, 10, 10)
    const bite = square(5, 5, 15, 15)
    const result = polygonSubtract(subject, bite)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.verts).toHaveLength(6)
    expect(polygonSelfIntersects(result.verts)).toBe(false)
    expect(pointInPolygon(9, 9, result.verts)).toBe(false)
    expect(pointInPolygon(9, 1, result.verts)).toBe(true)
    expect(pointInPolygon(1, 9, result.verts)).toBe(true)
  })

  it('边上咬掉一块：结果仍然单块', () => {
    const subject = square(0, 0, 10, 10)
    const bite = square(4, -2, 6, 3)
    const result = polygonSubtract(subject, bite)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(pointInPolygon(5, 2, result.verts)).toBe(false)
    expect(pointInPolygon(2, 2, result.verts)).toBe(true)
    expect(pointInPolygon(8, 2, result.verts)).toBe(true)
  })

  it('减去不重叠的形状：目标原样保留', () => {
    const subject = square(0, 0, 10, 10)
    const far = square(50, 50, 60, 60)
    const result = polygonSubtract(subject, far)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expectSameRing(result.verts, square(0, 0, 10, 10))
  })

  it('目标整个在裁剪形状里：减法拒绝为空', () => {
    const result = polygonSubtract(square(2, 2, 8, 8), square(0, 0, 10, 10))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('empty')
  })

  it('裁剪形状整个在目标里：减法会挖出环带，拒绝为 hole', () => {
    const result = polygonSubtract(square(0, 0, 10, 10), square(3, 3, 6, 6))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('hole')
  })

  it('从中间竖着贯穿：目标分裂成两块，拒绝为 multi', () => {
    const subject = square(0, 0, 10, 10)
    const cut = square(4, -2, 6, 12)
    const result = polygonSubtract(subject, cut)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('multi')
  })

  it('相交：两个方块的交是矩形', () => {
    const result = polygonIntersect(square(0, 0, 10, 10), square(5, 5, 15, 15))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expectSameRing(result.verts, square(5, 5, 10, 10))
    expect(pointInPolygon(7, 7, result.verts)).toBe(true)
    expect(pointInPolygon(2, 2, result.verts)).toBe(false)
  })

  it('相交：目标整个在裁剪形状里时结果就是目标', () => {
    const result = polygonIntersect(square(2, 2, 8, 8), square(0, 0, 10, 10))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expectSameRing(result.verts, square(2, 2, 8, 8))
  })

  it('相交：不相交的两个形状拒绝为空', () => {
    const result = polygonIntersect(square(0, 0, 10, 10), square(50, 50, 60, 60))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('empty')
  })

  it('凹多边形也能参与布尔', () => {
    // L 形：右上角凹进去。
    const ell: Verts = [
      [0, 0],
      [10, 0],
      [10, 4],
      [4, 4],
      [4, 10],
      [0, 10],
    ]
    const bite = square(3, 3, 7, 7)
    const result = polygonSubtract(ell, bite)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(regionShapeIssue(result.verts)).toBeNull()
    expect(pointInPolygon(5, 5, result.verts)).toBe(false)
    expect(pointInPolygon(2, 8, result.verts)).toBe(true)
    expect(pointInPolygon(8, 2, result.verts)).toBe(true)
  })

  it('跨接缝的目标与跨接缝的咬口：unwrap 后同域布尔', () => {
    // 目标与咬口都跨 ±180°，unwrap 后分别落在 [175, 185] 与 [179, 181]。
    const subject: Verts = [
      [175, -5],
      [-175, -5],
      [-175, 5],
      [175, 5],
    ]
    const notch: Verts = [
      [179, -2],
      [-179, -2],
      [-179, 6],
      [179, 6],
    ]
    const result = polygonSubtract(subject, notch)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(regionShapeIssue(result.verts)).toBeNull()
    expect(pointInPolygon(180, 0, result.verts)).toBe(false)
    expect(pointInPolygon(177, -4, result.verts)).toBe(true)
    expect(pointInPolygon(-177, -4, result.verts)).toBe(true)
  })

  it('跨接缝的贯穿切口把带切断：拒绝为 multi', () => {
    const subject: Verts = [
      [175, -5],
      [-175, -5],
      [-175, 5],
      [175, 5],
    ]
    const cut: Verts = [
      [179, -6],
      [-179, -6],
      [-179, 6],
      [179, 6],
    ]
    const result = polygonSubtract(subject, cut)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('multi')
  })

  it('跨接缝的目标与对跖侧的形状：减法保留目标原样', () => {
    const subject: Verts = [
      [175, -5],
      [-175, -5],
      [-175, 5],
      [175, 5],
    ]
    const far = square(-3, -2, 3, 2)
    const result = polygonSubtract(subject, far)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(pointInPolygon(180, 0, result.verts)).toBe(true)
  })

  it('减法幂等：再减同一个形状不改变生效范围', () => {
    const subject = square(0, 0, 10, 10)
    const bite = square(5, 5, 15, 15)
    const first = polygonSubtract(subject, bite)
    expect(first.ok).toBe(true)
    if (!first.ok) return
    // 贴边重减是退化情形（裁剪边与结果边近乎共线），允许拒绝，但生效范围不得变化。
    const second = polygonSubtract(first.verts, bite)
    const effective = second.ok ? second.verts : first.verts
    for (const [x, y] of [[2, 2], [8, 1], [1, 8], [9, 9]] as Verts) {
      expect(pointInPolygon(x, y, effective)).toBe(pointInPolygon(x, y, first.verts))
    }
  })
})
