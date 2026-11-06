// The Meadow scene: sky, sun or moon, clouds, hills, a tree and a cottage, painted from the theme's scene tokens so it
// follows Day and Night. `p` prefixes the SVG's ids, one per copy on the page. It is seeded, so a (p, seed) pair always
// draws the same picture. The markup is all our own, built from numbers: nothing of a message's reaches it.
const painted = new Map<string, string>();

function rnd(seed: number) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }
const PUFFS: [number, number, number][] = [[-74, 2, 30], [-40, -8, 38], [0, -14, 46], [44, -6, 36], [80, 4, 26], [-30, -46, 32], [10, -60, 40], [48, -40, 28], [-4, -96, 30], [24, -88, 25], [-22, -80, 22]];
function cloud(p: string, x: number, y: number, s: number, o = 1, extra = '') {
  return `<g transform="translate(${x} ${y}) scale(${s})" opacity="${o}" ${extra}><g clip-path="url(#${p}cb)">${PUFFS.map(([dx, dy, r]) => `<circle cx="${dx}" cy="${dy}" r="${r}" fill="url(#${p}cl)"/>`).join('')}</g></g>`;
}
function markup(p: string, seedOf: number) {
  const R = rnd(seedOf);
  let stars = ''; for (let i = 0; i < 70; i++) stars += `<circle cx="${(R() * 1200).toFixed(0)}" cy="${(R() * 170).toFixed(0)}" r="${(R() * 1.3 + .3).toFixed(2)}" fill="#fff" opacity="${(R() * .6 + .3).toFixed(2)}"/>`;
  let grass = ''; const groups = ['', ' b', ' c'];
  for (let gI = 0; gI < 3; gI++) {
    let blades = '';
    for (let i = 0; i < 46; i++) {
      const x = R() * 1200, h = 10 + R() * 22, lean = (R() - .4) * 10, base = 322 - R() * 8;
      blades += `<path d="M${x.toFixed(1)} ${base.toFixed(1)}q${(lean * .4).toFixed(1)} ${(-h * .55).toFixed(1)} ${lean.toFixed(1)} ${(-h).toFixed(1)}" stroke="var(--${R() > .5 ? 'hill-lit' : 'meadow'})" stroke-width="${(1.2 + R()).toFixed(1)}" fill="none" stroke-linecap="round"/>`;
    }
    grass += `<g class="sway${groups[gI]}">${blades}</g>`;
  }
  let ff = ''; for (let i = 0; i < 12; i++) ff += `<circle class="ff" style="animation-delay:${(-R() * 4).toFixed(1)}s" cx="${(560 + R() * 620).toFixed(0)}" cy="${(250 + R() * 60).toFixed(0)}" r="1.8" fill="#FFE59A" filter="url(#${p}glow)"/>`;
  const canopy: [number, number, number][] = [[-44, 10, 22], [-26, -10, 28], [2, -22, 33], [30, -8, 27], [48, 10, 21], [-8, 6, 31], [22, 14, 24], [-30, 16, 19], [8, -40, 22]];
  const house = `<g transform="translate(842 249)">
      <ellipse cx="2" cy="1" rx="26" ry="3" fill="var(--leaf-3)" opacity=".25"/>
      <rect x="-14" y="-15" width="30" height="15" fill="var(--wall)"/>
      <rect x="6" y="-27" width="4" height="9" fill="var(--trunk)"/>
      <path d="M-18 -14 L1 -28 L20 -14z" fill="var(--roof)"/>
      <rect x="-8" y="-11" width="6" height="6" fill="var(--window)"/>
      <circle class="winglow" cx="-5" cy="-8" r="16" fill="url(#${p}wg)"/>
    </g>
    <path d="M848 252 C 820 268 760 282 702 298 C 668 308 646 316 630 322" stroke="var(--path)" stroke-width="3.2" fill="none" opacity=".55" stroke-linecap="round"/>`;
  return `
  <defs>
    <linearGradient id="${p}sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--sky-1)"/><stop offset=".55" style="stop-color:var(--sky-2)"/><stop offset="1" style="stop-color:var(--sky-3)"/></linearGradient>
    <radialGradient id="${p}sun" cx="1080" cy="70" r="380" gradientUnits="userSpaceOnUse"><stop offset="0" style="stop-color:var(--disc-glow)"/><stop offset="1" style="stop-color:var(--disc-glow);stop-opacity:0"/></radialGradient>
    <linearGradient id="${p}cl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--cloud-1)"/><stop offset=".5" style="stop-color:var(--cloud-2)"/><stop offset="1" style="stop-color:var(--cloud-3)"/></linearGradient>
    <clipPath id="${p}cb" clipPathUnits="userSpaceOnUse"><rect x="-200" y="-220" width="400" height="244"/></clipPath>
    <linearGradient id="${p}mid" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--hill-mid)"/><stop offset="1" style="stop-color:var(--hill-near)"/></linearGradient>
    <linearGradient id="${p}near" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--meadow)"/><stop offset="1" style="stop-color:var(--hill-near)"/></linearGradient>
    <radialGradient id="${p}leaf" cx=".34" cy=".28" r=".85"><stop offset="0" style="stop-color:var(--leaf-1)"/><stop offset=".55" style="stop-color:var(--leaf-2)"/><stop offset="1" style="stop-color:var(--leaf-3)"/></radialGradient>
    <radialGradient id="${p}wg"><stop offset="0" stop-color="#FFC768" stop-opacity=".7"/><stop offset="1" stop-color="#FFC768" stop-opacity="0"/></radialGradient>
    <filter id="${p}paint" x="-5%" y="-10%" width="110%" height="120%"><feTurbulence type="fractalNoise" baseFrequency=".018 .04" numOctaves="2" seed="4" result="t"/><feDisplacementMap in="SourceGraphic" in2="t" scale="7" xChannelSelector="R" yChannelSelector="G"/></filter>
    <filter id="${p}tex" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="9"/><feColorMatrix values="0 0 0 0 .3 0 0 0 0 .3 0 0 0 0 .25 0 0 0 .22 0"/><feComposite in2="SourceGraphic" operator="in"/></filter>
    <radialGradient id="${p}halo"><stop offset=".25" style="stop-color:var(--disc-glow)"/><stop offset="1" style="stop-color:var(--disc-glow);stop-opacity:0"/></radialGradient>
    <mask id="${p}moon"><rect x="1040" y="30" width="80" height="80" fill="#fff"/><circle cx="1091" cy="63" r="19" fill="#000"/></mask>
    <filter id="${p}glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="2.2"/></filter>
  </defs>
  <rect width="1200" height="320" fill="url(#${p}sky)"/>
  <rect width="1200" height="320" fill="url(#${p}sun)"/>
  <g class="stars">${stars}</g>
  <circle class="sunbody" cx="1080" cy="70" r="30" fill="var(--disc)"/>
  <g class="moonbody"><circle cx="1080" cy="70" r="70" fill="url(#${p}halo)"/><circle cx="1080" cy="70" r="22" fill="var(--disc)" mask="url(#${p}moon)"/></g>
  <g filter="url(#${p}paint)">
    <g class="cdrift">${cloud(p, 930, 178, 1.3)}${cloud(p, 1130, 196, .8, .95)}${cloud(p, 700, 66, .34, .9)}${cloud(p, 760, 150, .22, .8)}</g>
    <path d="M0 205 C 90 186 150 170 230 178 C 320 186 360 160 450 150 C 540 140 600 172 690 176 C 780 180 830 158 920 168 C 1010 178 1080 192 1200 184 L1200 320 L0 320Z" fill="var(--mtn)" opacity=".85"/>
    <path d="M0 230 C 140 212 260 208 380 216 C 500 224 600 236 720 230 C 840 224 940 206 1060 212 C 1120 215 1170 222 1200 224 L1200 320 L0 320Z" fill="var(--hill-far)"/>
    <path d="M0 260 C 120 242 240 238 360 246 C 480 254 560 262 680 258 C 800 254 860 244 960 242 C 1060 240 1140 252 1200 250 L1200 320 L0 320Z" fill="url(#${p}mid)"/>
  </g>
  ${house}
  <g transform="translate(1012 242)">
    <ellipse cx="6" cy="2" rx="52" ry="5" fill="var(--leaf-3)" opacity=".3"/>
    <path d="M-5 1 C -4 -16 -7 -28 -11 -38 L -4 -39 C 0 -28 3 -16 6 1Z" fill="var(--trunk)"/>
    <path d="M-6 -24 C -16 -30 -22 -36 -26 -44 M0 -28 C 8 -36 16 -40 26 -44" stroke="var(--trunk)" stroke-width="3" fill="none" stroke-linecap="round"/>
    <g transform="translate(0 -62)" filter="url(#${p}paint)">${canopy.map(([dx, dy, r]) => `<circle cx="${dx}" cy="${dy}" r="${r}" fill="url(#${p}leaf)"/>`).join('')}</g>
  </g>
  <path d="M0 302 C 160 286 320 278 480 284 C 640 290 760 302 900 294 C 1020 287 1120 278 1200 282 L1200 320 L0 320Z" fill="url(#${p}near)"/>
  <rect width="1200" height="320" filter="url(#${p}tex)" opacity=".5" style="mix-blend-mode:multiply"/>
  ${grass}
  <g class="bird" stroke="var(--soft)" stroke-width="1.6" fill="none" stroke-linecap="round" opacity=".55"><path d="M820 92q5-5 10 0q5-5 10 0"/><path d="M846 104q4-4 8 0q4-4 8 0"/></g>
  ${ff}
`;
}

export function Scene({ p, seed = 11, par = "xMidYMax slice" }: { p: string; seed?: number; par?: string }) {
  const key = `${p}:${seed}`;
  let inner = painted.get(key);
  if (!inner) painted.set(key, inner = markup(p, seed));
  // eslint-disable-next-line no-restricted-syntax -- this markup is built above from numbers and the literal `p` its two callers pass: nothing of a message's reaches it
  return <svg className="scene" viewBox="0 0 1200 320" preserveAspectRatio={par} aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: inner }} />;
}
