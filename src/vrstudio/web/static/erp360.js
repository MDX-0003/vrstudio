/** erp360.js —— 对比页的 360° 全景观看器（WebGL 手写 ERP 投影，无第三方库）。
 *
 * 一块画布同时呈现两路等距柱状（ERP）视频：整幅先画原片，再用 scissor 把
 * 分割线右侧重画成成片。两路共用同一组视角 uniform——拖动/变焦永远同步，
 * 逐列检查接缝时两边看到的是同一个方向。
 *
 * 交互：按住拖动 = 转头（内容跟手），滚轮 = 变焦（水平 fov 30°–120°）；
 * 分割线是 wizard.html 里的 DOM 竖条，拖动后调 setSplit() 即可。
 * 纹理以「currentSrc + currentTime 变化」为脏标记——暂停/seek 后各补一帧，
 * 播放中每帧上传，不重复喂 GPU。
 */
"use strict";

const VERT_SRC = `
attribute vec2 aPos;
varying vec2 vNdc;
void main() {
  vNdc = aPos;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

const FRAG_SRC = `
precision highp float;
varying vec2 vNdc;
uniform sampler2D uTex;
uniform float uYaw;      // 弧度，正 = 向左看
uniform float uPitch;    // 弧度，正 = 向上看
uniform float uTanHalf;  // tan(垂直视野 / 2)
uniform float uAspect;   // 画布宽高比
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
}`;

const FOV_MIN = 30 * Math.PI / 180;
const FOV_MAX = 120 * Math.PI / 180;
const PITCH_MAX = 89 * Math.PI / 180;

class Erp360Compare {
  constructor(canvas, videoA, videoB) {
    this.canvas = canvas;
    this.videos = [videoA, videoB];
    this.split = 0.5;                    // 0..1，分割线的水平位置
    this.yaw = 0;                        // 弧度
    this.pitch = 0;                      // 弧度
    this.fovH = 90 * Math.PI / 180;
    this._lastKey = ["", ""];

    const gl = canvas.getContext("webgl");
    this.gl = gl;
    if (!gl) {
      canvas.replaceWith(Object.assign(document.createElement("div"),
        { textContent: "这个浏览器不支持 WebGL，全景对比看不了（其余功能不受影响）",
          className: "meta" }));
      return;
    }
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      return s;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT_SRC));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG_SRC));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    this.loc = {};
    for (const name of ["uTex", "uYaw", "uPitch", "uTanHalf", "uAspect"])
      this.loc[name] = gl.getUniformLocation(prog, name);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    gl.uniform1i(this.loc.uTex, 0);
    this.textures = this.videos.map(() => {
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      return t;
    });
    this._resize();
    window.addEventListener("resize", () => this._resize());
    this._wirePointer();
    const loop = () => { this._frame(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  setSplit(fx) { this.split = Math.min(1, Math.max(0, fx)); }

  resetView() {
    this.yaw = 0;
    this.pitch = 0;
    this.fovH = 90 * Math.PI / 180;
  }

  _resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.round(this.canvas.clientWidth * dpr);
    const h = Math.round(this.canvas.clientHeight * dpr);
    if (w && h && (w !== this.canvas.width || h !== this.canvas.height)) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
  }

  _wirePointer() {
    const c = this.canvas;
    let dragging = null;
    c.addEventListener("pointerdown", (e) => {
      dragging = { x: e.clientX, y: e.clientY };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const fovPerPx = this.fovH / c.clientWidth;
      this.yaw += (e.clientX - dragging.x) * fovPerPx;      // 内容跟手
      this.pitch = Math.min(PITCH_MAX, Math.max(
        -PITCH_MAX, this.pitch + (e.clientY - dragging.y) * fovPerPx));
      dragging = { x: e.clientX, y: e.clientY };
    });
    c.addEventListener("pointerup", () => { dragging = null; });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.fovH = Math.min(FOV_MAX, Math.max(FOV_MIN,
        this.fovH * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
  }

  _upload(gl, i) {
    const v = this.videos[i];
    if (!v.videoWidth || v.readyState < 2) return;
    // seek 进行中 currentTime 已经跳到目标值，但元素里还是旧帧。此时上传会把
    // “新时间”记成已上传；暂停状态下再没有事件触发重传，画面就停在旧帧，
    // 必须点一次播放让时间前进才会刷新。等 seek 完成（seeking=false）再上传。
    if (v.seeking) return;
    const key = v.currentSrc + ":" + v.currentTime;
    if (key === this._lastKey[i]) return;
    this._lastKey[i] = key;
    gl.bindTexture(gl.TEXTURE_2D, this.textures[i]);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v);
  }

  _frame() {
    const gl = this.gl;
    if (!gl) return;
    this._resize();
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    const aspect = this.canvas.width / Math.max(1, this.canvas.height);
    gl.uniform1f(this.loc.uYaw, this.yaw);
    gl.uniform1f(this.loc.uPitch, this.pitch);
    gl.uniform1f(this.loc.uTanHalf, Math.tan(this.fovH / 2) / aspect);
    gl.uniform1f(this.loc.uAspect, aspect);
    for (let i = 0; i < 2; i++) this._upload(gl, i);
    gl.activeTexture(gl.TEXTURE0);
    gl.disable(gl.SCISSOR_TEST);
    gl.bindTexture(gl.TEXTURE_2D, this.textures[0]);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    const sx = Math.round(this.split * this.canvas.width);
    if (sx < this.canvas.width) {
      gl.enable(gl.SCISSOR_TEST);
      gl.scissor(sx, 0, this.canvas.width - sx, this.canvas.height);
      gl.bindTexture(gl.TEXTURE_2D, this.textures[1]);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.disable(gl.SCISSOR_TEST);
    }
  }
}
