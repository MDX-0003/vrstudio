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
export type Pole = 'north' | 'south'

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
/** 区域顶点的纬度上限，与引擎的 `LAT_LIMIT` 一致；极点由 `pole` 标记，不存 ±90。 */
export const MAX_REGION_LAT = 89.5

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

/**
 * 经纬度 → ERP 像素。``shiftPx`` 是整周平移的像素量，用于跨接缝的补画；
 * 它加在像素坐标上，不能加在经度上（加经度会变成 2048°，不是一整周）。
 */
export function erpPixel(
  lon: number,
  lat: number,
  width: number,
  height: number,
  shiftPx = 0,
): Vec2 {
  return [((lon + 180) / 360) * width + shiftPx, ((90 - lat) / 180) * height]
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

/**
 * 盖帽的真实边界：展开后的闭环，末尾补上接缝短边的复制点。
 *
 * 普通区域直接返回展开后的闭环。盖帽的末尾点是首个顶点平移 ±360 后的副本，
 * 它代表同一条经线；这一段就是用户闭合时画下的真实短边。
 */
export function regionBoundary(verts: LonLat[], pole: Pole | null): LonLat[] {
  const lifted = unwrapLongitudes(verts)
  if (!pole || lifted.length === 0) return lifted
  const first = lifted[0]
  const last = lifted[lifted.length - 1]
  const sign = last[0] >= first[0] ? 1 : -1
  return [...lifted, [first[0] + 360 * sign, first[1]]]
}

/**
 * 区域在 ERP 平面上的闭合多边形：普通区域是展开后的闭环；盖帽再补两条经线边
 * 与一条跨整幅的极点边。两条经线边在球面上是同一条经线、方向相反，只用于闭合。
 */
export function regionErpPolygon(verts: LonLat[], pole: Pole | null): LonLat[] {
  const boundary = regionBoundary(verts, pole)
  if (!pole || boundary.length === 0) return boundary
  const first = boundary[0]
  const last = boundary[boundary.length - 1]
  const poleLat = pole === 'north' ? 90 : -90
  return [...boundary, [last[0], poleLat], [first[0], poleLat]]
}

/** 按最大步长细分边：屏幕上的折线因此贴近球面边界，也便于裁到前半球。 */
export function densify(verts: LonLat[], maxStepDeg = 2, closed = true): LonLat[] {
  const unwrapped = unwrapLongitudes(verts)
  if (unwrapped.length < 2) return unwrapped
  const out: LonLat[] = []
  const edgeCount = closed ? unwrapped.length : unwrapped.length - 1
  for (let index = 0; index < edgeCount; index += 1) {
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
  closed = true,
): ProjectedPolygon {
  const points = densify(verts, 2, closed).map(([lon, lat]) => project(lon, lat, pose, view))
  return { points, allFront: points.every((point) => point !== null) }
}

/** 球面点是否落在已展开到切平面的多边形内；按平面射线法判定。 */
function pointInLiftedPolygon(lon: number, lat: number, polygon: LonLat[]): boolean {
  if (polygon.length < 3) return false
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i]
    const [xj, yj] = polygon[j]
    const crosses = yi > lat !== (yj > lat)
    if (crosses && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/**
 * 球面点是否落在区域里。
 *
 * 区域先展开到切平面；查询点在 x、x±360 三个代表位置上取并集，因此跨度接近 360°
 * 的盖帽与跨接缝区域都能正确命中。
 */
export function pointInRegion(
  lon: number,
  lat: number,
  verts: LonLat[],
  pole: Pole | null = null,
): boolean {
  const polygon = regionErpPolygon(verts, pole)
  if (polygon.length < 3) return false
  return pointInLiftedPolygon(lon, lat, polygon)
    || pointInLiftedPolygon(lon - 360, lat, polygon)
    || pointInLiftedPolygon(lon + 360, lat, polygon)
}

/** 球面点是否落在普通多边形内；是 {@link pointInRegion} 的无盖帽形式。 */
export function pointInPolygon(lon: number, lat: number, verts: LonLat[]): boolean {
  return pointInRegion(lon, lat, verts, null)
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
 * 闭合草稿能否建成盖帽：边界绕极点恰好一圈、经度跨度不超过 360°、真实边界不自交。
 *
 * 极点取哪一侧不影响这条判定，调用方再用 {@link regionShapeIssue} 带 `pole` 复核。
 */
export function isPoleCapShape(verts: LonLat[]): boolean {
  if (Math.abs(polygonWinding(verts)) !== 1) return false
  if (longitudeSpan(verts) > 360) return false
  return !polygonSelfIntersects(regionBoundary(verts, 'north'), false)
}

/**
 * 单个闭合多边形能否表示这个区域；不能时返回原因，能表示返回 null。
 *
 * 普通区域装不下两类形状：边界绕极点转圈（扭转量非零），以及绕出去再折返、经度
 * 跨度超过一整圈的带（扭转量可能为 0，但切平面闭合后仍自重叠）。盖帽要求边界绕
 * 该极点恰好一圈且真实边界不自交。
 */
export function regionShapeIssue(
  verts: LonLat[],
  pole: Pole | null = null,
): RegionShapeIssue | null {
  const span = longitudeSpan(verts)
  const winding = polygonWinding(verts)
  if (pole) {
    if (Math.abs(winding) !== 1) {
      return { kind: 'winding', winding, span, hasPole: true }
    }
    if (span > 360) return { kind: 'span', span }
    if (polygonSelfIntersects(regionBoundary(verts, pole), false)) {
      return { kind: 'self-intersect' }
    }
    return null
  }
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

/** 边与边是否交叉（相邻边不算）；``closed=false`` 时不检查末边回到首点。 */
export function polygonSelfIntersects(verts: LonLat[], closed = true): boolean {
  const polygon = unwrapLongitudes(verts)
  const count = polygon.length
  if (count < (closed ? 4 : 3)) return false
  const edgeCount = closed ? count : count - 1
  for (let i = 0; i < edgeCount; i += 1) {
    const a = polygon[i]
    const b = polygon[(i + 1) % count]
    if (a[0] === b[0] && a[1] === b[1]) continue
    for (let j = i + 1; j < edgeCount; j += 1) {
      if (j === i + 1 || (closed && i === 0 && j === edgeCount - 1)) continue
      const c = polygon[j]
      const d = polygon[(j + 1) % count]
      if (c[0] === d[0] && c[1] === d[1]) continue
      if (segmentsIntersect(a, b, c, d)) return true
    }
  }
  return false
}

/**
 * 平面多边形布尔（Greiner–Hormann）：区域工作台「减去 / 相交」绘制模式的几何层。
 *
 * 输入是两个简单多边形（不自交，调用方先用 {@link regionShapeIssue} 保证），
 * 输出必须仍是单个简单多边形——结果分裂成多块、被挖成环带或退化为空时拒绝，
 * 因为 regions.json 只装得下一个顶点环。经度先 unwrap 并把裁剪多边形对齐到
 * 目标多边形的经度连续域，跨接缝的区域因此能直接参与布尔。
 *
 * 退化情形（顶点恰好落在对方边上、共边重叠）由对裁剪多边形的微小平移
 * （1e-6° 量级，远小于画面精度）打散；打散不了的按 ``degenerate`` 拒绝。
 */
export type PolygonClipFailure = 'empty' | 'multi' | 'hole' | 'degenerate'
export type PolygonClipResult =
  | { ok: true; verts: LonLat[] }
  | { ok: false; kind: PolygonClipFailure }

interface ClipNode {
  x: number
  y: number
  inter: boolean
  entry: boolean
  visited: boolean
  neighbor: ClipNode | null
  prev: ClipNode
  next: ClipNode
}

function buildRing(verts: LonLat[]): ClipNode {
  const head: ClipNode = { x: verts[0][0], y: verts[0][1], inter: false, entry: false, visited: false, neighbor: null, prev: null!, next: null! }
  head.prev = head
  head.next = head
  for (let index = 1; index < verts.length; index += 1) {
    const node: ClipNode = { x: verts[index][0], y: verts[index][1], inter: false, entry: false, visited: false, neighbor: null, prev: head.prev, next: head }
    head.prev.next = node
    head.prev = node
  }
  return head
}

function ringNodes(head: ClipNode): ClipNode[] {
  const nodes: ClipNode[] = [head]
  let cursor = head.next
  while (cursor !== head) {
    nodes.push(cursor)
    cursor = cursor.next
  }
  return nodes
}

function insertAfter(anchor: ClipNode, x: number, y: number): ClipNode {
  const node: ClipNode = { x, y, inter: true, entry: false, visited: false, neighbor: null, prev: anchor, next: anchor.next }
  anchor.next.prev = node
  anchor.next = node
  return node
}

/** unwrap 后把裁剪多边形平移 ``shift`` 度经度。 */
function shiftClip(clip: LonLat[], shift: number): LonLat[] {
  return clip.map(([lon, lat]) => [lon + shift, lat])
}

/**
 * 裁剪多边形相对目标的候选经度平移量（整周的域对齐）。
 *
 * unwrap 后两个多边形可能一个落在 [-180,180]、一个落在跨缝的连续域里；
 * 首选把中心差折算成最近的整周（360 的倍数），再附 ±360 备选，调用方逐个
 * 尝试、取能求出交点的那一个。平移量始终是 360 的倍数，不改变球面位置。
 */
function clipShiftCandidates(subject: LonLat[], clip: LonLat[]): number[] {
  const liftedSubject = unwrapLongitudes(subject)
  const liftedClip = unwrapLongitudes(clip)
  const center = (verts: LonLat[]): number => {
    const lons = verts.map(([lon]) => lon)
    return (Math.min(...lons) + Math.max(...lons)) / 2
  }
  const shift = 360 * Math.round((center(liftedSubject) - center(liftedClip)) / 360)
  return [shift, shift + 360, shift - 360]
}

function clipPolygons(
  subject: LonLat[],
  clip: LonLat[],
  /** true = 减去（A − B），false = 相交（A ∩ B）。 */
  subtracting: boolean,
): LonLat[][] {
  // 顶点贴点、顶点贴边的退化交给微小平移打散；量级远小于画面可分辨精度。
  const jitter = 1e-6
  const subjectRing = buildRing(subject)
  const clipRing = buildRing(clip.map(([lon, lat]) => [lon + jitter, lat + jitter * 0.5]))

  // 第一阶段：逐边求交。交点先记进各自边的桶里按参数排序，再分别插进两个环，
  // 最后统一配对——同一条边上有多个交点时，插入顺序必须与边的方向一致。
  const subjectOriginals = ringNodes(subjectRing)
  const clipOriginals = ringNodes(clipRing)
  interface Hit {
    t: number
    u: number
    subjectFrom: ClipNode
    clipFrom: ClipNode
    subjectNode: ClipNode | null
    clipNode: ClipNode | null
  }
  const hits: Hit[] = []
  for (const subjectNode of subjectOriginals) {
    const x1 = subjectNode.x
    const y1 = subjectNode.y
    const x2 = subjectNode.next.x
    const y2 = subjectNode.next.y
    for (const clipNode of clipOriginals) {
      const x3 = clipNode.x
      const y3 = clipNode.y
      const x4 = clipNode.next.x
      const y4 = clipNode.next.y
      const denominator = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
      if (Math.abs(denominator) < 1e-12) continue
      const t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denominator
      const u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / denominator
      // 只收严格穿过两条边内部的交；端点命中已被平移打散成近似情形。
      if (t > 1e-9 && t < 1 - 1e-9 && u > 1e-9 && u < 1 - 1e-9) {
        hits.push({ t, u, subjectFrom: subjectNode, clipFrom: clipNode, subjectNode: null, clipNode: null })
      }
    }
  }
  if (!hits.length) return []
  const bucketBy = (from: 'subjectFrom' | 'clipFrom', anchor: ClipNode): Hit[] =>
    hits.filter((hit) => hit[from] === anchor)
  const insertInto = (
    from: 'subjectFrom' | 'clipFrom',
    param: 't' | 'u',
    into: 'subjectNode' | 'clipNode',
    anchors: ClipNode[],
  ): void => {
    for (const anchor of anchors) {
      const bucket = bucketBy(from, anchor).sort((a, b) => a[param] - b[param])
      let cursor = anchor
      for (const hit of bucket) {
        const node = insertAfter(
          cursor,
          anchor.x + (anchor.next.x - anchor.x) * hit[param],
          anchor.y + (anchor.next.y - anchor.y) * hit[param],
        )
        hit[into] = node
        cursor = node
      }
    }
  }
  insertInto('subjectFrom', 't', 'subjectNode', subjectOriginals)
  insertInto('clipFrom', 'u', 'clipNode', clipOriginals)
  for (const hit of hits) {
    hit.subjectNode!.neighbor = hit.clipNode!
    hit.clipNode!.neighbor = hit.subjectNode!
  }

  // 第二阶段：沿环走交点，交替标记进入 / 离开结果区域。目标环的首个交点是
  // 进入还是离开由运算与首顶点的包含关系决定；裁剪环两种运算都从目标内部进入。
  const clipSubjectVerts = unwrapLongitudes(subject)
  const markEntries = (ring: ClipNode, firstEntry: boolean): void => {
    let entry = firstEntry
    for (const node of ringNodes(ring)) {
      if (!node.inter) continue
      node.entry = entry
      entry = !entry
    }
  }
  const subjectHeadInside = pointInLiftedPolygon(subjectRing.x, subjectRing.y, unwrapLongitudes(clip))
  markEntries(subjectRing, subtracting === subjectHeadInside)
  // 裁剪环两种运算都在目标内部进入结果区域。
  markEntries(clipRing, !pointInLiftedPolygon(clipRing.x, clipRing.y, clipSubjectVerts))

  // 第三阶段：从没走到的交点出发，交替沿两个环收集结果边界。
  const polygons: LonLat[][] = []
  for (const start of ringNodes(subjectRing)) {
    if (!start.inter || start.visited) continue
    const points: LonLat[] = []
    let current: ClipNode = start
    let guard = 0
    do {
      if (guard++ > 100000) return polygons
      current.visited = true
      if (current.neighbor) current.neighbor.visited = true
      points.push([current.x, current.y])
      // 沿 entry 指示的方向走到下一个交点，路径上的普通顶点一并收进来。
      let walker = current.entry ? current.next : current.prev
      while (!walker.inter) {
        points.push([walker.x, walker.y])
        walker = current.entry ? walker.next : walker.prev
      }
      current = walker.neighbor!
    } while (!current.visited)
    polygons.push(points)
  }
  return polygons
}

/** 去掉相邻重复点并丢弃首尾重复；微小平移打散造成的碎片边一并合并。 */
function tidyRing(points: LonLat[]): LonLat[] {
  const out: LonLat[] = []
  for (const point of points) {
    const last = out[out.length - 1]
    if (!last || Math.hypot(point[0] - last[0], point[1] - last[1]) > 1e-4) {
      out.push(point)
    }
  }
  while (out.length > 1) {
    const first = out[0]
    const last = out[out.length - 1]
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) > 1e-4) break
    out.pop()
  }
  return out
}

function ringArea(verts: LonLat[]): number {
  let sum = 0
  for (let i = 0, j = verts.length - 1; i < verts.length; j = i, i += 1) {
    sum += (verts[j][0] + verts[i][0]) * (verts[j][1] - verts[i][1])
  }
  return Math.abs(sum) / 2
}

/** 单目标多边形布尔：结果必须仍是单个简单多边形，否则按失败原因拒绝。 */
function booleanClip(
  subject: LonLat[],
  clip: LonLat[],
  /** true = 减去（A − B），false = 相交（A ∩ B）。 */
  subtracting: boolean,
): PolygonClipResult {
  const liftedSubject = unwrapLongitudes(subject)
  const liftedClip = unwrapLongitudes(clip)
  const candidates = clipShiftCandidates(subject, clip)
  let polygons: LonLat[][] = []
  // 候选平移逐个试：对跖点附近首选可能与目标不重叠，能求出交点的平移才是对的；
  // 全都无交点时，包含关系判断用首选平移（正常情形两者本就在同一域内）。
  for (const shift of candidates) {
    polygons = clipPolygons(liftedSubject, shiftClip(liftedClip, shift), subtracting)
      .map(tidyRing)
      .filter((ring) => ring.length >= 3 && ringArea(ring) > 1e-9)
    if (polygons.length) break
  }
  const alignedClip = shiftClip(liftedClip, candidates[0])
  if (polygons.length > 1) return { ok: false, kind: 'multi' }
  if (polygons.length === 1) return { ok: true, verts: polygons[0] }
  // 没有边界可走：按包含关系区分「空结果」「挖出环带」与「整体保留」。
  const subjectInsideClip = pointInLiftedPolygon(
    liftedSubject[0][0],
    liftedSubject[0][1],
    unwrapLongitudes(alignedClip),
  )
  const clipInsideSubject = pointInLiftedPolygon(
    alignedClip[0][0],
    alignedClip[0][1],
    liftedSubject,
  )
  if (subtracting) {
    // 减去：目标整个在裁剪多边形里 → 空；裁剪多边形整个在目标里 → 挖出环带；
    // 完全不相交 → 目标原样保留。
    if (subjectInsideClip) return { ok: false, kind: 'empty' }
    if (clipInsideSubject) return { ok: false, kind: 'hole' }
    return { ok: true, verts: liftedSubject }
  }
  // 相交：任一方包含另一方时仍有结果，否则不相交。
  if (subjectInsideClip) return { ok: true, verts: liftedSubject }
  if (clipInsideSubject) return { ok: true, verts: unwrapLongitudes(alignedClip) }
  return { ok: false, kind: 'empty' }
}

/** 从目标多边形里减去裁剪多边形（A − B）；结果分裂或成环带时拒绝。 */
export function polygonSubtract(subject: LonLat[], clip: LonLat[]): PolygonClipResult {
  return booleanClip(subject, clip, true)
}

/** 目标多边形与裁剪多边形的交集（A ∩ B）；不相交时拒绝。 */
export function polygonIntersect(subject: LonLat[], clip: LonLat[]): PolygonClipResult {
  return booleanClip(subject, clip, false)
}
