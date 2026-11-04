/**
 * Vue landing-page adaptation of React Bits Ripple Distortion by David Haz.
 * Source: https://reactbits.dev/animations/ripple-distortion
 * License: ../vendor/react-bits-LICENSE.md (MIT + Commons Clause).
 */
import { Renderer, Program, Mesh, Geometry, Triangle, Texture, RenderTarget } from 'ogl'

const MAX_WAVES = 64;
const START_SCALE = 1.5;
const LIFE_CONSTANT = Math.log(500);

const waveVertex = `
precision highp float;

attribute vec2 position;
attribute vec2 uv;
attribute vec2 iOffset;
attribute vec2 iScale;
attribute float iOpacity;

varying vec2 vUv;
varying float vOpacity;

void main() {
  vUv = uv;
  vOpacity = iOpacity;
  gl_Position = vec4(iOffset + position * iScale, 0.0, 1.0);
}
`;

const waveFragment = `
precision highp float;

varying vec2 vUv;
varying float vOpacity;

uniform float uRings;

const float PI = 3.141592653589793;
const float EDGE = 0.006737947;

void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float r = dot(p, p);
  if (r > 1.0) discard;

  float brush = (exp(-r * 5.0) - EDGE) / (1.0 - EDGE);

  brush *= 0.55 + 0.45 * cos(sqrt(r) * PI * 2.0 * uRings);

  gl_FragColor = vec4(vec3(brush * vOpacity * vOpacity), 1.0);
}
`;

const screenVertex = `
precision highp float;
attribute vec2 position;
attribute vec2 uv;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0.0, 1.0);
}
`;

const compositeFragment = `
precision highp float;

varying vec2 vUv;

uniform sampler2D uTexture;
uniform sampler2D uDisplacement;
uniform vec2 uResolution;
uniform vec2 uTextureSize;
uniform vec2 uTexel;
uniform vec3 uTint;
uniform vec3 uHighlight;
uniform float uStrength;
uniform float uSwirl;
uniform float uDispersion;
uniform float uGlint;
uniform float uTintAmount;
uniform float uGrayscale;

const float TAU = 6.283185307179586;

vec2 coverUV(vec2 uv) {
  vec2 safe = max(uTextureSize, vec2(1.0));
  vec2 s = uResolution / safe;
  vec2 scaledSize = safe * max(s.x, s.y);
  vec2 offset = (uResolution - scaledSize) * 0.5;
  return (uv * uResolution - offset) / scaledSize;
}

void main() {
  float amount = texture2D(uDisplacement, vUv).r;
  vec2 base = coverUV(vUv);

  float theta = amount * uSwirl * TAU;
  vec2 dir = vec2(sin(theta), cos(theta));
  vec2 push = dir * amount * uStrength;

  vec3 color;
  if (uDispersion > 0.001) {
    float split = uDispersion * 0.25;
    color.r = texture2D(uTexture, base + push * (1.0 + split)).r;
    color.g = texture2D(uTexture, base + push).g;
    color.b = texture2D(uTexture, base + push * (1.0 - split)).b;
  } else {
    color = texture2D(uTexture, base + push).rgb;
  }

  if (uGrayscale > 0.001) {
    color = mix(color, vec3(dot(color, vec3(0.2126, 0.7152, 0.0722))), uGrayscale);
  }

  if (uTintAmount > 0.001) {
    color = mix(color, color * uTint * 1.9, clamp(amount * 1.6, 0.0, 1.0) * uTintAmount);
  }

  if (uGlint > 0.001) {
    float ex = texture2D(uDisplacement, vUv + vec2(uTexel.x, 0.0)).r - texture2D(uDisplacement, vUv - vec2(uTexel.x, 0.0)).r;
    float ey = texture2D(uDisplacement, vUv + vec2(0.0, uTexel.y)).r - texture2D(uDisplacement, vUv - vec2(0.0, uTexel.y)).r;
    vec3 normal = normalize(vec3(-ex * 26.0, -ey * 26.0, 1.0));
    vec3 light = normalize(vec3(-0.35, 0.55, 1.0));
    float raw = pow(max(dot(normal, light), 0.0), 22.0);
    float flatSpec = pow(max(light.z, 0.0), 22.0);
    color += uHighlight * clamp((raw - flatSpec) / max(1.0 - flatSpec, 0.0001), 0.0, 1.0) * uGlint;
  }

  gl_FragColor = vec4(color, 1.0);
}
`;

/** Mount the image displacement effect. Returns an idempotent cleanup callback. */
export function mountRipple(mount, src, onReady) {
  const renderer = new Renderer({
    alpha: false, antialias: false, depth: false,
    dpr: Math.min(window.devicePixelRatio || 1, 1.5),
    powerPreference: 'low-power',
  })
  const gl = renderer.gl
  const canvas = gl.canvas
  let disposed = false
  let loaded = false
  let raf = 0
  let previousTime = 0
  let width = 1, height = 1, current = 0
  let previousX = -1000, previousY = -1000
  let observer
  let image
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')

  const cleanup = () => {
    if (disposed) return
    disposed = true
    cancelAnimationFrame(raf)
    observer?.disconnect()
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerdown', onDown)
    document.removeEventListener('visibilitychange', onVisibility)
    motion.removeEventListener('change', onMotion)
    canvas.removeEventListener('webglcontextlost', onContextLost)
    if (image) { image.onload = null; image.onerror = null }
    canvas.remove()
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    onReady(false)
  }
  function onContextLost(event) { event.preventDefault(); cleanup() }
  function onMotion() { if (motion.matches) cleanup() }
  function onVisibility() {
    cancelAnimationFrame(raf)
    raf = 0
    previousTime = 0
    if (!document.hidden) schedule()
  }
  function schedule() {
    if (!raf && loaded && !disposed && !document.hidden) raf = requestAnimationFrame(render)
  }
  function point(event) {
    const rect = mount.getBoundingClientRect()
    if (!rect.width || !rect.height || event.clientX < rect.left || event.clientX > rect.right ||
      event.clientY < rect.top || event.clientY > rect.bottom) return null
    return [event.clientX - rect.left, rect.bottom - event.clientY]
  }
  function emitWave(x, y, power) {
    if (disposed || !loaded || motion.matches) return
    const wave = waves[current]
    current = (current + 1) % MAX_WAVES
    Object.assign(wave, { x, y, scale: START_SCALE * power, target: START_SCALE * 4 * power, opacity: 1 })
    schedule()
  }
  function onMove(event) {
    const p = point(event)
    if (!p || Math.hypot(p[0] - previousX, p[1] - previousY) < 18) return
    emitWave(p[0], p[1], 1)
    ;[previousX, previousY] = p
  }
  function onDown(event) {
    const p = point(event)
    if (p) emitWave(p[0], p[1], 1.5)
  }

  // Keep initialization guarded too: a shader/driver failure must leave the static hero usable.
  const offsets = new Float32Array(MAX_WAVES * 2)
  const scales = new Float32Array(MAX_WAVES * 2)
  const opacities = new Float32Array(MAX_WAVES)
  const waves = Array.from({ length: MAX_WAVES }, () => ({ x: 0, y: 0, scale: START_SCALE, target: START_SCALE, opacity: 0 }))
  let geometry, displacement, waveMesh, compositeMesh, uniforms
  function render(now) {
    raf = 0
    if (disposed || document.hidden) return
    try {
      const delta = previousTime ? Math.min(.05, (now - previousTime) / 1000) : 1 / 60
      previousTime = now
      const growth = 1 - Math.exp(-delta * 1.09)
      const decay = Math.exp(-delta * LIFE_CONSTANT / 2.6)
      let active = false
      for (let i = 0; i < MAX_WAVES; i++) {
        const wave = waves[i]
        wave.opacity *= decay
        if (wave.opacity < .002) { wave.opacity = 0; opacities[i] = 0; continue }
        active = true
        wave.scale += (wave.target - wave.scale) * growth
        const half = wave.scale * 130 / 2
        offsets[i * 2] = wave.x / width * 2 - 1
        offsets[i * 2 + 1] = wave.y / height * 2 - 1
        scales[i * 2] = half / width * 2
        scales[i * 2 + 1] = half / height * 2
        opacities[i] = wave.opacity
      }
      geometry.attributes.iOffset.needsUpdate = true
      geometry.attributes.iScale.needsUpdate = true
      geometry.attributes.iOpacity.needsUpdate = true
      renderer.render({ scene: waveMesh, target: displacement, clear: true })
      renderer.render({ scene: compositeMesh })
      onReady(true)
      if (active) schedule()
      else previousTime = 0
    } catch { cleanup() }
  }
  try {
    gl.clearColor(0, 0, 0, 1)
    const texture = new Texture(gl, {
      generateMipmaps: false, minFilter: gl.LINEAR, magFilter: gl.LINEAR,
      wrapS: gl.CLAMP_TO_EDGE, wrapT: gl.CLAMP_TO_EDGE,
    })
    geometry = new Geometry(gl, {
      position: { size: 2, data: new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]) },
      uv: { size: 2, data: new Float32Array([0,0,1,0,0,1,0,1,1,0,1,1]) },
      iOffset: { instanced: 1, size: 2, data: offsets },
      iScale: { instanced: 1, size: 2, data: scales },
      iOpacity: { instanced: 1, size: 1, data: opacities },
    })
    const waveProgram = new Program(gl, {
      vertex: waveVertex, fragment: waveFragment, uniforms: { uRings: { value: 4 } },
      transparent: true, depthTest: false, depthWrite: false, cullFace: false,
    })
    waveProgram.setBlendFunc(gl.ONE, gl.ONE)
    waveMesh = new Mesh(gl, { geometry, program: waveProgram, frustumCulled: false })
    displacement = new RenderTarget(gl, {
      width: 2, height: 2, depth: false, minFilter: gl.LINEAR, magFilter: gl.LINEAR,
      wrapS: gl.CLAMP_TO_EDGE, wrapT: gl.CLAMP_TO_EDGE,
    })
    uniforms = {
      uTexture: { value: texture }, uDisplacement: { value: displacement.texture },
      uResolution: { value: [1,1] }, uTextureSize: { value: [1,1] }, uTexel: { value: [1,1] },
      uTint: { value: [.65,.86,.84] }, uHighlight: { value: [.75,.94,1] },
      uStrength: { value: .12 }, uSwirl: { value: 1 }, uDispersion: { value: .12 },
      uGlint: { value: .12 }, uTintAmount: { value: .08 }, uGrayscale: { value: 0 },
    }
    compositeMesh = new Mesh(gl, {
      geometry: new Triangle(gl),
      program: new Program(gl, { vertex: screenVertex, fragment: compositeFragment, uniforms, depthTest: false, depthWrite: false }),
    })
    const resize = () => {
      width = Math.max(1, mount.clientWidth)
      height = Math.max(1, mount.clientHeight)
      renderer.setSize(width, height)
      uniforms.uResolution.value = [width, height]
      const fw = Math.max(2, Math.round(width * .5)), fh = Math.max(2, Math.round(height * .5))
      displacement.setSize(fw, fh)
      uniforms.uTexel.value = [1 / fw, 1 / fh]
      schedule()
    }
    mount.appendChild(canvas)
    canvas.addEventListener('webglcontextlost', onContextLost)
    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('pointerdown', onDown, { passive: true })
    document.addEventListener('visibilitychange', onVisibility)
    motion.addEventListener('change', onMotion)
    observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()
    image = new Image()
    image.decoding = 'async'
    image.onload = () => {
      if (disposed) return
      texture.image = image
      uniforms.uTextureSize.value = [image.naturalWidth, image.naturalHeight]
      loaded = true
      schedule()
    }
    image.onerror = cleanup
    image.src = src
  } catch (error) { cleanup(); throw error }
  return cleanup
}

