/**
 * ERP 全景的屏幕 ↔ 球面换算（区域选取的唯一权威点）。
 *
 * 视角参数与 `legacy/Erp360Compare.ts` 的着色器是同一套：yaw/pitch 弧度、fovH 为水平
 * 视场角、画布按 NDC 反算。新增部件（区域工作台、命中测试、缩略图）只能调用这里，
 * 不许再写第二份投影。
 */

export type LonLat = [number, number]
export type Vec3 = [number, number, number]
export type Vec2 = [number, number]

export interface Pose {
  yaw: number
  pitch: number
  fovH: number
}

export interface Viewport {
  width: number
  height: number
}

export const MIN_FOV = (30 * Math.PI) / 180
export const MAX_FOV = (120 * Math.PI) / 180
export const MAX_PITCH = (89 * Math.PI) / 180

export const ERP_VERT_SRC = `
attribute vec2 aPos;
varying vec2 vNdc;
void main() {
  vNdc = aPos;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`

export const ERP_FRAG_SRC = `
precision highp float;
varying vec2 vNdc;
uniform sampler2D uTex;
uniform sampler2D uMask;
uniform float uYaw;
uniform float uPitch;
uniform float uTanHalf;
uniform float uAspect;
void main() {
  vec3 dir = normalize(vec3(vNdc.x * uTanHalf * uAspect, vNdc.y * uTanHalf, -1.0));
  float cp = cos(uPitch), sp = sin(uPitch);
  float y1 = dir.y * cp - dir.z * sp;
  float z1 = dir.y * sp + dir.z * cp;
  float cy = cos(uYaw), sy = sin(uYaw);
  float x2 = dir.x * cy + z1 * sy;
  float z2 = -dir.x * sy + z1 * cy;
  float u = 0.5 + atan(x2, -z2) / 6.283185307179586;
  float v = 0.5 - asin(clamp(y1, -1.0, 1.0)) / 3.141592653589793;
  vec4 base = texture2D(uTex, vec2(u, v));
  vec4 mask = texture2D(uMask, vec2(u, v));
  gl_FragColor = vec4(mix(base.rgb, mask.rgb, mask.a), 1.0);
}`

export function clampFov(value: number): number {
  return Math.min(MAX_FOV, Math.max(MIN_FOV, value))
}

export function clampPitch(value: number): number {
  return Math.min(MAX_PITCH, Math.max(-MAX_PITCH, value))
}

/** 经纬度（度）→ 单位球方向，与着色器的 uv 约定一致。 */
export function directionFromLonLat(lon: number, lat: number): Vec3 {
  const la = (lat * Math.PI) / 180
  const lo = (lon * Math.PI) / 180
  return [Math.cos(la) * Math.sin(lo), Math.sin(la), -Math.cos(la) * Math.cos(lo)]
}

/** 单位球方向 → 经纬度（度），经度落在 [-180, 180]。 */
export function lonLatFromDirection(dir: Vec3): LonLat {
  const [x, y, z] = dir
  return [
    (Math.atan2(x, -z) * 180) / Math.PI,
    (Math.asin(Math.max(-1, Math.min(1, y))) * 180) / Math.PI,
  ]
}

/** 球面点投到画布像素；落在相机背后时返回 null。 */
export function project(
  lon: number,
  lat: number,
  pose: Pose,
  view: Viewport,
): Vec2 | null {
  const [x2, y1, z2] = directionFromLonLat(lon, lat)
  const cy = Math.cos(pose.yaw)
  const sy = Math.sin(pose.yaw)
  const cp = Math.cos(pose.pitch)
  const sp = Math.sin(pose.pitch)
  const x = x2 * cy - z2 * sy
  const z1 = x2 * sy + z2 * cy
  const y = y1 * cp + z1 * sp
  const z = -y1 * sp + z1 * cp
  if (z >= -1e-6) return null
  const aspect = view.width / Math.max(1, view.height)
  const tanHalf = Math.tan(pose.fovH / 2) / aspect
  const nx = x / -z / (tanHalf * aspect)
  const ny = y / -z / tanHalf
  if (Math.abs(nx) > 3 || Math.abs(ny) > 3) return null
  return [(nx * 0.5 + 0.5) * view.width, (1 - (ny * 0.5 + 0.5)) * view.height]
}

/** 画布像素 → 球面经纬度；是 {@link project} 的逆。 */
export function unproject(
  x: number,
  y: number,
  pose: Pose,
  view: Viewport,
): LonLat {
  const aspect = view.width / Math.max(1, view.height)
  const nx = (x / view.width) * 2 - 1
  const ny = 1 - (y / view.height) * 2
  const tanHalf = Math.tan(pose.fovH / 2) / aspect
  let dx = nx * tanHalf * aspect
  let dy = ny * tanHalf
  let dz = -1
  const length = Math.hypot(dx, dy, dz)
  dx /= length
  dy /= length
  dz /= length
  const cp = Math.cos(pose.pitch)
  const sp = Math.sin(pose.pitch)
  const y1 = dy * cp - dz * sp
  const z1 = dy * sp + dz * cp
  const cy = Math.cos(pose.yaw)
  const sy = Math.sin(pose.yaw)
  const x2 = dx * cy + z1 * sy
  const z2 = -dx * sy + z1 * cy
  return lonLatFromDirection([x2, y1, z2])
}

/**
 * 把相邻经度差压到 ±180 内：跨接缝的多边形因此变成连续的一串。
 * 存储与判定都用这一份，写回时保持不 wrap 的值。
 */
export function unwrapLongitudes(verts: LonLat[]): LonLat[] {
  const out: LonLat[] = []
  let previous: number | null = null
  for (const [rawLon, lat] of verts) {
    let lon = rawLon
    if (previous !== null) {
      while (lon - previous > 180) lon -= 360
      while (lon - previous < -180) lon += 360
    }
    out.push([lon, lat])
    previous = lon
  }
  return out
}

/** 按最大步长细分边：屏幕上的折线因此贴近球面边界，也便于裁到前半球。 */
export function densify(verts: LonLat[], maxStepDeg = 2): LonLat[] {
  const unwrapped = unwrapLongitudes(verts)
  if (unwrapped.length < 2) return unwrapped
  const out: LonLat[] = []
  for (let index = 0; index < unwrapped.length; index += 1) {
    const [lon0, lat0] = unwrapped[index]
    const [lon1, lat1] = unwrapped[(index + 1) % unwrapped.length]
    const steps = Math.max(
      1,
      Math.ceil(Math.max(Math.abs(lon1 - lon0), Math.abs(lat1 - lat0)) / maxStepDeg),
    )
    for (let step = 0; step < steps; step += 1) {
      const t = step / steps
      out.push([lon0 + (lon1 - lon0) * t, lat0 + (lat1 - lat0) * t])
    }
  }
  return out
}

export interface ProjectedPolygon {
  points: Array<Vec2 | null>
  /** 所有顶点都在相机前方时才允许填充，否则只画可见段。 */
  allFront: boolean
}

export function projectPolygon(
  verts: LonLat[],
  pose: Pose,
  view: Viewport,
): ProjectedPolygon {
  const points = densify(verts).map(([lon, lat]) => project(lon, lat, pose, view))
  return { points, allFront: points.every((point) => point !== null) }
}

/** 球面点是否落在多边形内；按 unwrap 后的经度做平面射线法。 */
export function pointInPolygon(lon: number, lat: number, verts: LonLat[]): boolean {
  const polygon = unwrapLongitudes(verts)
  if (polygon.length < 3) return false
  let x = lon
  while (x - polygon[0][0] > 180) x -= 360
  while (x - polygon[0][0] < -180) x += 360
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i]
    const [xj, yj] = polygon[j]
    const crosses = yi > lat !== (yj > lat)
    if (crosses && x < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** unwrap 后的经度跨度；超过 360° 就不是球面上的简单闭合多边形了。 */
export function longitudeSpan(verts: LonLat[]): number {
  const unwrapped = unwrapLongitudes(verts)
  if (!unwrapped.length) return 0
  const lons = unwrapped.map(([lon]) => lon)
  return Math.max(...lons) - Math.min(...lons)
}

/**
 * 多边形绕极点的圈数（0 表示不绕）。
 *
 * 逐边取最短经度差（闭合边也算）后求和再除以 360：正常区域是 0；边界绕极点一整圈
 * 时是 ±1。这种形状在经纬度平面上必然自重叠，引擎的单多边形模型表示不了。
 */
export function polygonWinding(verts: LonLat[]): number {
  const unwrapped = unwrapLongitudes(verts)
  if (unwrapped.length < 3) return 0
  let total = 0
  for (let index = 0; index < unwrapped.length; index += 1) {
    const current = unwrapped[index]
    const next = unwrapped[(index + 1) % unwrapped.length]
    let delta = next[0] - current[0]
    if (index === unwrapped.length - 1) {
      while (delta > 180) delta -= 360
      while (delta < -180) delta += 360
    }
    total += delta
  }
  return Math.round(total / 360)
}

export type RegionShapeIssue =
  | { kind: 'winding'; winding: number; span: number; hasPole: boolean }
  | { kind: 'span'; span: number }
  | { kind: 'self-intersect' }

/**
 * 单个闭合多边形能否表示这个区域；不能时返回原因，能表示返回 null。
 *
 * 两类形状装不下：边界绕极点转圈（扭转量非零），以及绕出去再折返、经度跨度超过
 * 一整圈的带（扭转量可能为 0，但切平面闭合后仍自重叠）。工作台只用这个函数判定，
 * 文案由调用方按 kind 生成。
 */
export function regionShapeIssue(verts: LonLat[]): RegionShapeIssue | null {
  const span = longitudeSpan(verts)
  const winding = polygonWinding(verts)
  if (winding !== 0) {
    return {
      kind: 'winding',
      winding,
      span,
      hasPole: verts.some(([, lat]) => Math.abs(lat) >= 89),
    }
  }
  if (span > 360) return { kind: 'span', span }
  if (polygonSelfIntersects(verts)) return { kind: 'self-intersect' }
  return null
}

function orientation(p: LonLat, q: LonLat, r: LonLat): number {
  const value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
  if (Math.abs(value) < 1e-9) return 0
  return value > 0 ? 1 : -1
}

function onSegment(p: LonLat, q: LonLat, r: LonLat): boolean {
  return (
    Math.min(p[0], r[0]) <= q[0] && q[0] <= Math.max(p[0], r[0])
    && Math.min(p[1], r[1]) <= q[1] && q[1] <= Math.max(p[1], r[1])
  )
}

function segmentsIntersect(a: LonLat, b: LonLat, c: LonLat, d: LonLat): boolean {
  const o1 = orientation(a, b, c)
  const o2 = orientation(a, b, d)
  const o3 = orientation(c, d, a)
  const o4 = orientation(c, d, b)
  if (o1 !== o2 && o3 !== o4) return true
  if (o1 === 0 && onSegment(a, c, b)) return true
  if (o2 === 0 && onSegment(a, d, b)) return true
  if (o3 === 0 && onSegment(c, a, d)) return true
  if (o4 === 0 && onSegment(c, b, d)) return true
  return false
}

/** 边与边是否交叉（相邻边不算）；用于闭合前拒绝自交多边形。 */
export function polygonSelfIntersects(verts: LonLat[]): boolean {
  const polygon = unwrapLongitudes(verts)
  const count = polygon.length
  if (count < 4) return false
  for (let i = 0; i < count; i += 1) {
    for (let j = i + 1; j < count; j += 1) {
      if (i === j) continue
      const adjacent = (i + 1) % count === j || (j + 1) % count === i
      if (adjacent) continue
      if (segmentsIntersect(
        polygon[i], polygon[(i + 1) % count],
        polygon[j], polygon[(j + 1) % count],
      )) return true
    }
  }
  return false
}
