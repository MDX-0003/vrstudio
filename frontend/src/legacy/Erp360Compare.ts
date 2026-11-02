const VERT_SRC = `
attribute vec2 aPos;
varying vec2 vNdc;
void main() {
  vNdc = aPos;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`

const FRAG_SRC = `
precision highp float;
varying vec2 vNdc;
uniform sampler2D uTex;
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
  gl_FragColor = texture2D(uTex, vec2(u, v));
}`

const FOV_MIN = (30 * Math.PI) / 180
const FOV_MAX = (120 * Math.PI) / 180
const PITCH_MAX = (89 * Math.PI) / 180

/** 两路纹理的来源：视频按帧去重，静态图只上传一次。 */
export type ErpSource = HTMLVideoElement | HTMLImageElement

/**
 * 对比页的 360° 全景观看器：一块画布画两路 ERP（视频或静态图），分割线右侧用成片覆盖。
 * 着色器与双路时钟对齐从旧前端原样迁入，不在这里改投影规则。
 */
export class Erp360Compare {
  private canvas: HTMLCanvasElement
  private sources: [ErpSource, ErpSource]
  private gl: WebGLRenderingContext | null = null
  private textures: WebGLTexture[] = []
  private lastKey = ['', '']
  private loc: Record<string, WebGLUniformLocation | null> = {}
  private raf = 0
  private observer: ResizeObserver | null = null
  private dragging: { x: number; y: number } | null = null
  private scratch: HTMLCanvasElement | null = null
  private onPointerDown = (event: PointerEvent): void => {
    this.dragging = { x: event.clientX, y: event.clientY }
    this.canvas.setPointerCapture(event.pointerId)
  }
  private onPointerMove = (event: PointerEvent): void => {
    if (!this.dragging) return
    const fovPerPx = this.fovH / Math.max(1, this.canvas.clientWidth)
    this.yaw += (event.clientX - this.dragging.x) * fovPerPx
    this.pitch = Math.min(
      PITCH_MAX,
      Math.max(-PITCH_MAX, this.pitch + (event.clientY - this.dragging.y) * fovPerPx),
    )
    this.dragging = { x: event.clientX, y: event.clientY }
  }
  private onPointerUp = (): void => {
    this.dragging = null
  }
  private onWheel = (event: WheelEvent): void => {
    event.preventDefault()
    this.fovH = Math.min(FOV_MAX, Math.max(FOV_MIN, this.fovH * Math.exp(event.deltaY * 0.001)))
  }

  split = 0.5
  yaw = 0
  pitch = 0
  fovH = (90 * Math.PI) / 180

  constructor(canvas: HTMLCanvasElement, sourceA: ErpSource, sourceB: ErpSource) {
    this.canvas = canvas
    this.sources = [sourceA, sourceB]
    this.gl = canvas.getContext('webgl')
    if (!this.gl) return
    this.init()
    this.resize()
    // 画布尺寸只在真正变化时重设；每帧读 clientWidth 会强制同步布局。
    this.observer = new ResizeObserver(() => this.resize())
    this.observer.observe(canvas)
    canvas.addEventListener('pointerdown', this.onPointerDown)
    canvas.addEventListener('pointermove', this.onPointerMove)
    canvas.addEventListener('pointerup', this.onPointerUp)
    canvas.addEventListener('wheel', this.onWheel, { passive: false })
    const loop = (): void => {
      this.frame()
      this.raf = requestAnimationFrame(loop)
    }
    this.raf = requestAnimationFrame(loop)
  }

  get supported(): boolean {
    return this.gl !== null
  }

  setSplit(fx: number): void {
    this.split = Math.min(1, Math.max(0, fx))
  }

  resetView(): void {
    this.yaw = 0
    this.pitch = 0
    this.fovH = (90 * Math.PI) / 180
  }

  destroy(): void {
    cancelAnimationFrame(this.raf)
    this.observer?.disconnect()
    this.observer = null
    this.canvas.removeEventListener('pointerdown', this.onPointerDown)
    this.canvas.removeEventListener('pointermove', this.onPointerMove)
    this.canvas.removeEventListener('pointerup', this.onPointerUp)
    this.canvas.removeEventListener('wheel', this.onWheel)
    for (const texture of this.textures) this.gl?.deleteTexture(texture)
    this.gl = null
  }

  private init(): void {
    const gl = this.gl
    if (!gl) return
    const shader = (type: number, source: string): WebGLShader | null => {
      const item = gl.createShader(type)
      if (!item) return null
      gl.shaderSource(item, source)
      gl.compileShader(item)
      return item
    }
    const program = gl.createProgram()
    const vertex = shader(gl.VERTEX_SHADER, VERT_SRC)
    const fragment = shader(gl.FRAGMENT_SHADER, FRAG_SRC)
    if (!program || !vertex || !fragment) return
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
    gl.useProgram(program)
    for (const name of ['uTex', 'uYaw', 'uPitch', 'uTanHalf', 'uAspect']) {
      this.loc[name] = gl.getUniformLocation(program, name)
    }
    const aPos = gl.getAttribLocation(program, 'aPos')
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)
    gl.uniform1i(this.loc.uTex, 0)
    this.textures = this.sources.map(() => {
      const texture = gl.createTexture()
      gl.bindTexture(gl.TEXTURE_2D, texture)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      return texture
    })
  }

  private resize(): void {
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const width = Math.round(this.canvas.clientWidth * dpr)
    const height = Math.round(this.canvas.clientHeight * dpr)
    if (width && height && (width !== this.canvas.width || height !== this.canvas.height)) {
      this.canvas.width = width
      this.canvas.height = height
    }
  }

  /** 源可上传时的真实尺寸；视频等帧、静态图等解码完成。 */
  private readySize(source: ErpSource): [number, number] | null {
    if (source instanceof HTMLVideoElement) {
      if (!source.videoWidth || source.readyState < 2) return null
      // seek 进行中 currentTime 已跳到目标值但元素里还是旧帧，等 seek 完成再传。
      if (source.seeking) return null
      return [source.videoWidth, source.videoHeight]
    }
    if (!source.complete || !source.naturalWidth) return null
    return [source.naturalWidth, source.naturalHeight]
  }

  private upload(index: number): void {
    const gl = this.gl
    const source = this.sources[index]
    if (!gl) return
    const size = this.readySize(source)
    if (!size) return
    const key = source instanceof HTMLVideoElement
      ? `${source.currentSrc}:${source.currentTime}`
      : (source.currentSrc || source.src)
    if (!key || key === this.lastKey[index]) return
    this.lastKey[index] = key
    gl.bindTexture(gl.TEXTURE_2D, this.textures[index])
    const [width, height] = size
    // 超过 MAX_TEXTURE_SIZE 的源当纹理上传会失败（8K 图在 8192 上限的 GPU 上正好边界，
    // 9558 宽的 padded 画布必然超限）：按比例降采样到离屏 canvas 再上传。
    const max = Number(gl.getParameter(gl.MAX_TEXTURE_SIZE)) || 0
    if (max > 0 && (width > max || height > max)) {
      const scale = Math.min(max / width, max / height)
      const canvas = this.scratch ?? (this.scratch = document.createElement('canvas'))
      canvas.width = Math.max(1, Math.floor(width * scale))
      canvas.height = Math.max(1, Math.floor(height * scale))
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.drawImage(source, 0, 0, canvas.width, canvas.height)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas)
      return
    }
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
  }

  private frame(): void {
    const gl = this.gl
    if (!gl) return
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    const aspect = this.canvas.width / Math.max(1, this.canvas.height)
    gl.uniform1f(this.loc.uYaw, this.yaw)
    gl.uniform1f(this.loc.uPitch, this.pitch)
    gl.uniform1f(this.loc.uTanHalf, Math.tan(this.fovH / 2) / aspect)
    gl.uniform1f(this.loc.uAspect, aspect)
    for (let index = 0; index < 2; index += 1) this.upload(index)
    gl.activeTexture(gl.TEXTURE0)
    gl.disable(gl.SCISSOR_TEST)
    gl.bindTexture(gl.TEXTURE_2D, this.textures[0])
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    const split = Math.round(this.split * this.canvas.width)
    if (split < this.canvas.width) {
      gl.enable(gl.SCISSOR_TEST)
      gl.scissor(split, 0, this.canvas.width - split, this.canvas.height)
      gl.bindTexture(gl.TEXTURE_2D, this.textures[1])
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      gl.disable(gl.SCISSOR_TEST)
    }
  }
}
