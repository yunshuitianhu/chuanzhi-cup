<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import heroImage from '../assets/water-drop-hero.jpg'
import TechTextLine from '../components/TechTextLine.vue'

const surface = ref(null)
const effectReady = ref(false)
let disposed = false
let disposeEffect = () => {}

onMounted(async () => {
  // The static image is always present, including reduced-motion / WebGL-free devices.
  if (window.matchMedia('(prefers-reduced-motion: reduce), (max-width: 600px)').matches) return
  try {
    const { mountRipple } = await import('../utils/welcomeRipple.js')
    if (disposed || !surface.value) return
    disposeEffect = mountRipple(surface.value, heroImage, ready => { effectReady.value = ready })
  } catch {
    effectReady.value = false
  }
})
onBeforeUnmount(() => { disposed = true; disposeEffect() })
</script>

<template>
  <main class="welcome" aria-labelledby="welcome-title">
    <div class="welcome-image" :style="{ backgroundImage: `url(${heroImage})` }" aria-hidden="true"></div>
    <div ref="surface" class="welcome-ripple" :class="{ ready: effectReady }" aria-hidden="true"></div>
    <div class="welcome-shade" aria-hidden="true"></div>

    <header class="welcome-header">
      <a class="welcome-brand" href="/" aria-label="水质溯源与检测助手首页">
        <svg viewBox="0 0 32 38" fill="none" aria-hidden="true"><path d="M16 2C13 10 3 18 3 25a13 13 0 0 0 26 0C29 18 19 10 16 2Z" stroke="currentColor" stroke-width="1.4"/><path d="M5 25c5-5 9 5 14 0s7-1 8 0M9 29c3 2 5 2 7 1" stroke="currentColor" stroke-width="1.2"/></svg>
        <span>水境<span class="brand-divider">/</span><span class="brand-english">AQUA INSIGHT</span></span>
      </a>
      <span class="welcome-edition">WATER QUALITY INTELLIGENCE</span>
    </header>

    <section class="welcome-copy">
      <p class="welcome-eyebrow"><span></span>让每一滴水，有迹可循</p>
      <h1 id="welcome-title" aria-label="水质溯源与检测助手">
        <span class="title-line" aria-hidden="true"><TechTextLine text="水质溯源" /></span>
        <span class="title-line" aria-hidden="true"><TechTextLine text="与检测助手" /><span class="title-dot">.</span></span>
      </h1>
      <p class="welcome-description">读懂水质变化，追寻污染线索。<br>连接监测数据、可视分析与智能研判。</p>
      <div class="welcome-entries">
      <RouterLink class="welcome-enter" to="/simple">
        <span>进入简易版</span>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12h15M13 5l7 7-7 7" stroke="currentColor" stroke-width="1.5"/></svg>
      </RouterLink>
      <RouterLink class="welcome-enter" to="/chat">
        <span>进入专业版</span>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12h15M13 5l7 7-7 7" stroke="currentColor" stroke-width="1.5"/></svg>
      </RouterLink>
      </div>
      <p class="welcome-caption">从观测出发 · 以证据为依据</p>
    </section>

    <footer class="welcome-footer">
      <span class="welcome-fields">水质检测<span>/</span>时空分析<span>/</span>辅助溯源</span>
    </footer>
  </main>
</template>

<style scoped>
.welcome{position:relative;isolation:isolate;min-height:max(720px,100svh);overflow:hidden;background:#233643;color:#eef6f6;display:flex;flex-direction:column;font-family:'Microsoft YaHei','PingFang SC',sans-serif}
.welcome-image,.welcome-ripple,.welcome-shade{position:absolute;inset:0;z-index:-3}
.welcome-image{background-position:center;background-size:cover}
.welcome-ripple{opacity:0;transition:opacity .7s;pointer-events:none}
.welcome-ripple.ready{opacity:1}.welcome-ripple :deep(canvas){display:block;width:100%;height:100%}
.welcome-shade{z-index:-2;background:linear-gradient(90deg,rgba(15,31,42,.38),rgba(15,31,42,.06) 55%,transparent 75%)}
.welcome-header{display:flex;align-items:center;justify-content:space-between;margin:0 5.2%;padding:33px 0;border-bottom:1px solid #d2eef21c}
.welcome-brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:#d9ecee;font-size:17px;letter-spacing:.15em}
.welcome-brand svg{width:27px;height:34px;color:#a4d8d6}.brand-divider{margin:0 15px;color:#b5d7de5c;font-weight:300}.brand-english{font-size:10px;letter-spacing:.2em}
.welcome-edition{font-size:9px;letter-spacing:.25em;color:#d1e3e487}
.welcome-copy{margin:auto 0 auto 10%;padding:78px 0 72px;width:55%}
.welcome-eyebrow{display:flex;align-items:center;gap:12px;color:#b3d0d4;font-size:12px;letter-spacing:.24em;margin:0 0 29px}
.welcome-eyebrow>span{width:25px;height:1px;background:#a1d8d2}
h1{font-size:clamp(42px,4.2vw,78px);line-height:1.4;font-weight:400;letter-spacing:.08em;margin:0 0 25px;text-shadow:0 2px 24px #14263122}
.title-line{display:block}
.title-dot{color:#a6d8cf;margin-left:.04em}
.welcome-eyebrow,.welcome-description,.welcome-enter,.welcome-caption{animation:welcome-arrive .9s ease backwards}
.welcome-description{animation-delay:.3s}.welcome-enter{animation-delay:.45s}.welcome-caption{animation-delay:.55s}
.welcome-entries{display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.welcome-description{font-size:14px;line-height:2.1;letter-spacing:.12em;color:#bdd0d7;margin:0 0 37px}
.welcome-enter{display:inline-flex;align-items:center;justify-content:space-between;gap:55px;min-height:54px;padding:0 24px;background:#c2e5df;color:#18343c;border:1px solid #d4eee8;border-radius:3px;text-decoration:none;font-size:13px;letter-spacing:.15em;transition:background .25s,transform .25s,box-shadow .25s}
.welcome-enter svg{width:22px;height:22px;transition:transform .25s}.welcome-enter:hover{background:#ddf3ee;box-shadow:0 6px 32px #bce4dc20;transform:translateY(-2px)}.welcome-enter:hover svg{transform:translateX(4px)}.welcome-enter:focus-visible{outline:2px solid #f0ffff;outline-offset:6px}
.welcome-caption{font-size:10px;letter-spacing:.17em;color:#a9c3cb99;margin:17px 0 0}
.welcome-footer{display:flex;align-items:center;justify-content:space-between;margin:0 5.2%;padding:25px 0 28px;border-top:1px solid #d2eef21c;color:#afc9ce9c;font-size:10px;letter-spacing:.15em}
.welcome-fields{display:flex;gap:20px;align-items:center}.welcome-fields>span{color:#b2d8dd4d}
@keyframes welcome-arrive{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:translateY(0)}}
@media(min-width:1800px){.welcome-copy{margin-left:12%}}
@media(max-width:900px){.welcome-copy{margin-left:7%;width:65%}h1{font-size:clamp(40px,6vw,60px)}.welcome-edition{display:none}.welcome-shade{background:linear-gradient(90deg,#142a3bd9,#142a3b33 85%)}.welcome-description{font-size:13px}}
@media(max-width:600px){.welcome{min-height:100svh}.welcome-header{margin:0 7%;padding:24px 0}.welcome-brand{font-size:15px}.brand-english{font-size:8px}.brand-divider{margin:0 10px}.welcome-image{background-position:72% center}.welcome-ripple{display:none}.welcome-shade{background:linear-gradient(0deg,#132633fa 0%,#172c3cce 48%,#142a3b22 100%)}.welcome-copy{margin:auto 7% 0;width:86%;padding:170px 0 46px}.welcome-eyebrow{font-size:10px;margin-bottom:20px}h1{font-size:clamp(35px,9vw,52px);margin-bottom:20px}.welcome-description{font-size:12px;margin-bottom:29px}.welcome-enter{min-height:50px;gap:50px}.welcome-footer{margin:0 7%;padding:20px 0;font-size:9px}.welcome-fields{gap:13px}.welcome-interaction{display:none}}
@media(prefers-reduced-motion:reduce){.welcome-eyebrow,.welcome-description,.welcome-enter,.welcome-caption{animation:none!important;opacity:1;transform:none}}
</style>
