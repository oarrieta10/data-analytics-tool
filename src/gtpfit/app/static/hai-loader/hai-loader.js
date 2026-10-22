/**
 * <hai-loader> â€” HAI Robotics 3D loading screen (web component).
 *
 * Dependency: three  (npm i three)   â€” tested with three@0.184
 *
 * Usage:
 *   import './hai-loader.js';
 *   <hai-loader></hai-loader>
 *
 * Attributes (all optional):
 *   background  CSS color behind the loader (default: transparent)
 *   footer      footer text (default "HAI Robotics Â· Intelligent Automation"); footer="" hides it
 *   no-status   hide the text + progress bar, show the robot only
 *   speed       animation speed multiplier (default 1)
 *
 * Properties:
 *   el.messages = [['Title', 'Subtitle'], ...]   replace the cycling copy
 *
 * The component sizes to its box (default 100% x 100%, min-height 420px).
 * Rendering stops automatically when it's removed from the DOM.
 */
import * as THREE from '../three/three.module.min.js';  // vendored three@0.184.0 (was 'three')

const DEFAULT_MESSAGES = [
  ['Lifting things up', 'Literally. It\u2019s what we do'],
  ['Picking the right tote', 'Out of several thousand identical ones'],
  ['Climbing the shelves', 'No ladder required'],
  ['Counting boxes', 'Still six. Recounting to be sure'],
  ['Optimizing the route', 'Shortest path, zero small talk'],
  ['Stretching the scissor lift', 'Never skip warm-up'],
  ['Consulting the fleet', 'Robot 001 has opinions'],
  ['Almost there', 'Gently lowering expectations'],
];

const CSS = `
:host{display:block;position:relative;width:100%;height:100%;min-height:420px;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;color:#1d2433;background:var(--hai-bg,transparent)}
.wrap{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:24px;box-sizing:border-box}
.view{position:relative;width:min(460px,92%);height:min(400px,62%);flex:none}
canvas{display:block;width:100%!important;height:100%!important}
.status{display:flex;flex-direction:column;align-items:center;gap:8px;width:min(360px,86%)}
.title{display:flex;align-items:center;gap:9px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;transition:opacity .35s;text-align:center}
.dot{width:7px;height:7px;border-radius:50%;background:#24418A;box-shadow:0 0 0 4px rgba(36,65,138,.12);flex:none}
.sub{font-size:12px;color:#6b7280;transition:opacity .35s;min-height:15px;text-align:center}
.track{position:relative;width:100%;height:2px;background:#e6e8ec;border-radius:2px;overflow:hidden;margin-top:10px}
.bar{position:absolute;top:0;bottom:0;width:32%;background:#24418A;border-radius:2px}
.foot{margin-top:8px;font-size:10px;letter-spacing:.2em;color:#9aa1ad;text-transform:uppercase}
:host([no-status]) .status{display:none}
@media (prefers-reduced-motion: reduce){.bar{left:0!important;width:100%}}
`;

function buildRobot() {
    const M = (name, color, o = {}) => new THREE.MeshStandardMaterial({ name, color, roughness: 0.5, metalness: 0, ...o });
    const mat = {
      white: M('shell_white', 0xe9ebee, { roughness: 0.42 }),
      grey: M('shell_grey', 0xc9cdd2, { roughness: 0.5 }),
      black: M('frame_black', 0x1d1f23, { roughness: 0.62 }),
      steel: M('steel', 0xc4c8cd, { roughness: 0.3, metalness: 0.35 }),
      navy: M('hai_navy', 0x2c4884, { roughness: 0.55 }),
      red: M('estop_red', 0xd3271c, { roughness: 0.35 }),
      tire: M('tire', 0x232427, { roughness: 0.85 }),
      led: M('led', 0xffffff, { emissive: 0xffffff, emissiveIntensity: 0.6 }),
      crate: M('crate_blue', 0x2b3cc0, { roughness: 0.48 }),
    };
    
    function box(name, m, w, h, d, x, y, z, parent) {
      const g = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
      g.name = name; g.position.set(x, y, z); parent.add(g); return g;
    }
    function cyl(name, m, r, len, x, y, z, parent, axis = 'z', seg = 40) {
      const g = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, seg), m);
      g.name = name; if (axis === 'z') g.rotation.x = Math.PI / 2; if (axis === 'x') g.rotation.z = Math.PI / 2;
      g.position.set(x, y, z); parent.add(g); return g;
    }
    // rounded-in-plan slab: w along x, d along z, extruded up by h
    function slab(name, m, w, h, d, r, x, y, z, parent) {
      const s = new THREE.Shape(), a = w / 2 - 0.01, b = d / 2 - 0.01;
      s.moveTo(-a + r, -b); s.lineTo(a - r, -b); s.quadraticCurveTo(a, -b, a, -b + r); s.lineTo(a, b - r); s.quadraticCurveTo(a, b, a - r, b);
      s.lineTo(-a + r, b); s.quadraticCurveTo(-a, b, -a, b - r); s.lineTo(-a, -b + r); s.quadraticCurveTo(-a, -b, -a + r, -b);
      const geo = new THREE.ExtrudeGeometry(s, { depth: h - 0.02, bevelEnabled: true, bevelThickness: 0.01, bevelSize: 0.01, bevelSegments: 4, curveSegments: 10 });
      geo.rotateX(-Math.PI / 2); geo.translate(0, 0.01, 0);
      const g = new THREE.Mesh(geo, m); g.name = name; g.position.set(x, y, z); parent.add(g); return g;
    }
    // side profile extruded along z
    function profile(name, m, pts, depth, z, parent) {
      const s = new THREE.Shape(); pts.forEach(([x, y], i) => (i ? s.lineTo(x, y) : s.moveTo(x, y)));
      const geo = new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: false });
      const g = new THREE.Mesh(geo, m); g.name = name; g.position.z = z; parent.add(g); return g;
    }
    function label(name, w, h, draw, x, y, z, ry, parent, rz = 0) {
      const c = document.createElement('canvas'); c.width = Math.round(w * 1200); c.height = Math.round(h * 1200);
      draw(c.getContext('2d'), c.width, c.height);
      const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
      const g = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ name: name + '_mat', map: t, transparent: true, roughness: 0.5 }));
      g.name = name; g.position.set(x, y, z); g.rotation.set(0, ry, rz); parent.add(g); return g;
    }
    
    const robot = new THREE.Group(); robot.name = 'hai_lift_robot';
    const base = new THREE.Group(); base.name = 'chassis'; robot.add(base);
    const deck = new THREE.Group(); deck.name = 'lift_deck'; robot.add(deck);
    const lift = new THREE.Group(); lift.name = 'scissor_lift'; robot.add(lift);
    
    // rounded side-profile solid, extruded across z (centered)
    function rshape(pts, rad) {
      const sh = new THREE.Shape(), n = pts.length;
      for (let i = 0; i < n; i++) {
        const p = pts[i], pv = pts[(i + n - 1) % n], nx = pts[(i + 1) % n];
        const r = Array.isArray(rad) ? rad[i] : rad;
        const v1 = [pv[0] - p[0], pv[1] - p[1]], v2 = [nx[0] - p[0], nx[1] - p[1]];
        const l1 = Math.hypot(...v1), l2 = Math.hypot(...v2), k1 = Math.min(r, l1 / 2) / l1, k2 = Math.min(r, l2 / 2) / l2;
        const A = [p[0] + v1[0] * k1, p[1] + v1[1] * k1], B = [p[0] + v2[0] * k2, p[1] + v2[1] * k2];
        i ? sh.lineTo(A[0], A[1]) : sh.moveTo(A[0], A[1]);
        if (r > 0) sh.quadraticCurveTo(p[0], p[1], B[0], B[1]); else sh.lineTo(B[0], B[1]);
      }
      return sh;
    }
    function solid(name, m, pts, rad, depth, bev, parent, z = 0) {
      const geo = new THREE.ExtrudeGeometry(rshape(pts, rad), { depth: depth - 2 * bev, bevelEnabled: bev > 0, bevelThickness: bev, bevelSize: bev, bevelSegments: 3, curveSegments: 10 });
      geo.translate(0, 0, -(depth - 2 * bev) / 2 + z);
      const g = new THREE.Mesh(geo, m); g.name = name; parent.add(g); return g;
    }
    
    // ---------- chassis (profile traced from side render) ----------
    const BW = 1.17, BD = 0.72, BY = 0.035, TOP = 0.274, LOW = 0.15, RX = 0.588, FX = -0.581;
    const AR = 0.13, WY = 0.122, BEV = 0.006;
    {
      const pts = [[RX, BY], [AR, BY], [AR, WY]], rad = [0.02, 0, 0];
      for (let i = 1; i < 16; i++) { const t = (i / 16) * Math.PI; pts.push([AR * Math.cos(t), WY + AR * Math.sin(t)]); rad.push(0); }
      pts.push([-AR, WY], [-AR, BY], [FX, BY], [FX, LOW], [-0.215, LOW], [-0.06, TOP], [RX, TOP]);
      rad.push(0, 0, 0.02, 0.02, 0.07, 0.07, 0.03);
      solid('chassis_shell', mat.white, pts, rad, BD, BEV, base);
    }
    box('wheel_well', mat.black, AR * 2 - 0.004, WY + AR - BY - 0.01, BD - 0.03, 0, (BY + WY + AR) / 2 - 0.004, 0, base);
    box('arch_seam', mat.black, 0.022, TOP - (WY + AR) + 0.012, BD + 0.004, 0, (TOP + WY + AR) / 2 + 0.002, 0, base);
    box('front_bumper', mat.black, 0.012, 0.03, 0.5, FX - 0.008, BY + 0.05, 0, base);
    box('rear_sensor', mat.black, 0.008, 0.07, 0.2, RX + 0.008, 0.16, 0, base);
    for (const sz of [-1, 1]) {
      for (const sx of [-1, 1]) box('caster', mat.tire, 0.05, 0.035, 0.05, sx * 0.5, BY / 2, sz * 0.27, base);
      const fz = sz * (BD / 2 + 0.0012), ry = sz > 0 ? 0 : Math.PI;
      box('led_rear', mat.led, 0.1, 0.016, 0.003, RX - 0.05, 0.064, fz, base);
      box('led_front', mat.led, 0.1, 0.016, 0.003, FX + 0.05, 0.106, fz, base);
      const wz = sz * (BD / 2 - 0.045);
      cyl('drive_tire', mat.tire, 0.118, 0.07, 0, WY, wz, base, 'z', 48);
      cyl('drive_hub', mat.steel, 0.082, 0.074, 0, WY, wz, base, 'z', 48);
      cyl('hub_cap', mat.steel, 0.03, 0.08, 0, WY, wz, base, 'z', 32);
      for (let i = 0; i < 5; i++) {
        const t = (i / 5) * Math.PI * 2 + 0.3;
        cyl('hub_hole', mat.tire, 0.016, 0.076, Math.cos(t) * 0.055, WY + Math.sin(t) * 0.055, wz, base, 'z', 20);
      }
      label('hai_badge', 0.15, 0.125, (g, w, h) => {
        g.fillStyle = '#2c4884'; g.beginPath(); g.moveTo(w * 0.2, 0); g.lineTo(w, 0); g.lineTo(w * 0.8, h); g.lineTo(0, h); g.fill();
        g.save(); g.translate(w / 2, h / 2); g.rotate(-Math.PI / 2 + 0.2); g.fillStyle = '#fff'; g.font = `800 ${h * 0.42}px Helvetica, Arial`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('HAI', 0, 0); g.restore();
      }, 0.187, 0.142, fz, ry, base);
      label('id_plate', 0.11, 0.076, (g, w, h) => {
        g.fillStyle = '#3a3a3c'; g.beginPath(); g.roundRect(0, 0, w, h, h * 0.14); g.fill();
        g.fillStyle = '#fff'; g.font = `800 ${h * 0.72}px Helvetica, Arial`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('001', w / 2, h * 0.55);
      }, -0.266 * (sz > 0 ? 1 : 1), 0.088, fz, ry, base);
      label('console', 0.16, 0.08, (g, w, h) => {
        g.fillStyle = '#1e1f22'; g.beginPath(); g.roundRect(0, 0, w, h * 0.66, h * 0.08); g.fill(); g.fillRect(0, h * 0.74, w, h * 0.22);
        g.fillStyle = '#f2f2f2'; g.beginPath(); g.arc(w * 0.5, h * 0.33, h * 0.22, 0, 7); g.fill();
        g.fillStyle = '#444'; g.beginPath(); g.arc(w * 0.18, h * 0.33, h * 0.17, 0, 7); g.fill(); g.beginPath(); g.arc(w * 0.82, h * 0.33, h * 0.17, 0, 7); g.fill();
      }, 0.418, 0.15, fz, ry, base);
    }
    
    // ---------- lift deck (body + masts + crate) ----------
    const DX0 = -0.392, DX1 = 0.588, DY0 = 0.30, DY1 = 0.707, DZ = 0.355;
    const DL = DX1 - DX0, DCX = (DX0 + DX1) / 2, SLOPE = (0.305 - 0.182) / (0.207 - 0.023);
    solid('body_shell', mat.white, [[DX1, 0.305], [DX1, DY1], [DX0, DY1], [DX0, 0.182], [-0.207, 0.182], [-0.023, 0.305]], [0.02, 0.03, 0.02, 0.02, 0.07, 0.07], DZ * 2, BEV, deck);
    solid('body_frame', mat.black, [[DX1 - 0.02, TOP + 0.004], [DX1 - 0.02, 0.31], [-0.023, 0.31], [-0.207, 0.187], [DX0 + 0.01, 0.187], [DX0 + 0.01, LOW + 0.004], [-0.215, LOW + 0.004], [-0.06, TOP + 0.004]], 0, BD - 0.07, 0, deck);
    for (const sz of [-1, 1]) {
      const zf = (d) => (sz > 0 ? DZ + 0.0005 : -DZ - 0.0005 - d);
      const gy = 0.49, gx = 0.0, gf = gy - (gx - DX0) * SLOPE;
      profile('panel_groove', mat.grey, [[DX1 - 0.01, gy], [gx, gy], [DX0 + 0.005, gf], [DX0 + 0.005, gf - 0.035], [gx - 0.035 / SLOPE * 0.4, gy - 0.035], [DX1 - 0.01, gy - 0.035]], 0.003, zf(0.003), deck);
      profile('navy_stripe', mat.navy, [[0.217, 0.31], [0.095, 0.31], [0.074, 0.43], [0.196, 0.43]], 0.002, zf(0.004) + sz * 0.0015, deck);
      cyl('estop_ring', mat.steel, 0.032, 0.008, -0.245, 0.52, sz * (DZ + 0.004), deck, 'z', 40);
      cyl('estop', mat.red, 0.021, 0.024, -0.245, 0.52, sz * (DZ + 0.01), deck, 'z', 32);
      box('fork_rail', mat.black, DL - 0.05, 0.022, 0.05, DCX, DY1 + 0.014, sz * 0.2, deck);
      box('chain', mat.steel, DL - 0.12, 0.008, 0.014, DCX, DY1 + 0.029, sz * 0.2, deck);
      box('top_rail', mat.black, DL - 0.02, 0.02, 0.03, DCX, DY1 + 0.01, sz * (DZ - 0.03), deck);
    }
    box('top_plate', mat.black, DL - 0.04, 0.008, DZ * 2 - 0.06, DCX, DY1 + 0.004, 0, deck);
    box('front_panel', mat.grey, 0.01, 0.3, 0.46, DX0 - 0.008, 0.36, 0, deck);
    label('front_logo', 0.36, 0.06, (g, w, h) => {
      g.fillStyle = '#24418A'; g.font = `800 ${h * 0.7}px Helvetica, Arial`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('HAI ROBOTICS', w / 2, h / 2);
    }, DX0 - 0.0135, 0.38, 0, -Math.PI / 2, deck);
    box('tray_wall', mat.steel, DL - 0.06, 0.06, 0.006, DCX, DY1 + 0.04, -DZ + 0.02, deck);
    // ---------- HAI crate ----------
    function buildCrate() {
      const C = new THREE.Group(); C.name = 'hai_crate';
      const L = 0.96, D = 0.69, H = 0.37, t = 0.012, m = mat.crate, kx = L / 0.8, kz = D / 0.56, ky = H / 0.31;
      const slot = (sh, x, y, w, h) => {
        const p = new THREE.Path(), r = w / 2;
        p.moveTo(x + w, y + r); p.lineTo(x + w, y + h - r); p.absarc(x + r, y + h - r, r, 0, Math.PI, false);
        p.lineTo(x, y + r); p.absarc(x + r, y + r, r, Math.PI, Math.PI * 2, false); sh.holes.push(p);
      };
      // panels: [x0, x1, hasSlots]
      const wall = (name, W, panels, edgeCols) => {
        const sh = new THREE.Shape(); sh.moveTo(-W / 2, 0); sh.lineTo(W / 2, 0); sh.lineTo(W / 2, H); sh.lineTo(-W / 2, H); sh.lineTo(-W / 2, 0);
        const rows = [[0.06 * ky, 0.08 * ky], [0.155 * ky, 0.09 * ky]];
        for (const [a, b, s] of panels) {
          if (!s) continue;
          const cols = Math.max(2, Math.round((b - a) / 0.03)), sw = 0.011, gap = (b - a - cols * sw) / (cols + 1);
          for (let c = 0; c < cols; c++) for (const [ry, rh] of rows) slot(sh, a + gap + c * (sw + gap), ry, sw, rh);
        }
        const g = new THREE.Mesh(new THREE.ExtrudeGeometry(sh, { depth: t, bevelEnabled: false, curveSegments: 6 }), m); g.name = name; return g;
      };
      const lp = [[-0.37, -0.205, 1], [-0.185, 0.0, 0], [0.02, 0.185, 1], [0.205, 0.37, 1]].map(([a, b, c]) => [a * kx, b * kx, c]);
      const sp = [[-0.25, -0.2, 1], [-0.17, 0.17, 0], [0.2, 0.25, 1]].map(([a, b, c]) => [a * kz, b * kz, c]);
      for (const sz of [-1, 1]) {
        const w = wall('crate_long_wall', L, lp); w.position.z = sz > 0 ? D / 2 - t : -D / 2; C.add(w);
        const s = wall('crate_short_wall', D, sp); s.rotation.y = Math.PI / 2; s.position.x = sz > 0 ? L / 2 - t : -L / 2 + 0; if (sz < 0) s.position.x = -L / 2; C.add(s);
        // bands
        box('crate_rim', m, L + 0.016, 0.028, 0.018, 0, H - 0.014, sz * (D / 2 + 0.002), C);
        box('crate_rim', m, 0.018, 0.028, D + 0.016, sz * (L / 2 + 0.002), H - 0.014, 0, C);
        box('crate_band', m, L + 0.004, 0.014, 0.01, 0, 0.27 * ky, sz * (D / 2 + 0.003), C);
        box('crate_band', m, 0.01, 0.014, D + 0.004, sz * (L / 2 + 0.003), 0.27 * ky, 0, C);
        box('crate_base', m, L + 0.01, 0.045, 0.016, 0, 0.0225, sz * (D / 2 + 0.002), C);
        box('crate_base', m, 0.016, 0.045, D + 0.01, sz * (L / 2 + 0.002), 0.0225, 0, C);
        for (const [a, b] of lp) for (const x of [a - 0.01, b + 0.01]) box('crate_rib', m, 0.014, H - 0.02, 0.012, x, H / 2, sz * (D / 2 + 0.004), C);
        for (const zz of [-0.18 * kz, 0.18 * kz]) box('crate_rib', m, 0.012, H - 0.02, 0.014, sz * (L / 2 + 0.004), H / 2, zz, C);
        for (const sx of [-1, 1]) box('crate_corner', m, 0.03, H, 0.03, sx * (L / 2 - 0.006), H / 2, sz * (D / 2 - 0.006), C);
      }
      box('crate_floor', m, L - 0.01, 0.012, D - 0.01, 0, 0.02, 0, C);
      const logo = (g, w, h, big) => {
        g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'alphabetic';
        g.font = `800 ${h * 0.5}px "Helvetica Neue", Helvetica, Arial`; g.fillText('HAI', w / 2, h * 0.56);
        g.font = `700 ${h * 0.2}px "Helvetica Neue", Helvetica, Arial`; g.fillText('ROBOTICS', w / 2, h * 0.86);
      };
      for (const sz of [-1, 1]) {
        label('crate_logo_long', 0.16, 0.115, logo, -0.09 * kx, 0.15 * ky, sz * (D / 2 + 0.0015), sz > 0 ? 0 : Math.PI, C);
        label('crate_logo_short', 0.19, 0.135, logo, sz * (L / 2 + 0.0015), 0.17 * ky, 0, sz * Math.PI / 2, C);
        label('crate_barcode', 0.13, 0.03, (g, w, h) => {
          g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.fillStyle = '#111';
          let x = w * 0.42; let s = 7; while (x < w * 0.95) { const bw = 1 + ((s = (s * 13 + 5) % 17) % 3) * 1.5; g.fillRect(x, h * 0.18, bw, h * 0.64); x += bw + 2; }
          g.font = `${h * 0.3}px monospace`; g.fillText('HAI0001', w * 0.05, h * 0.62);
        }, sz * (L / 2 + 0.0015), 0.085, 0, sz * Math.PI / 2, C);
      }
      return C;
    }
    const crate = buildCrate();
    crate.position.set(DCX + 0.005, DY1 + 0.033, 0);
    deck.add(crate);
    // climbing masts
    for (const sz of [-1, 1]) {
      const mz = sz * 0.29, mx = -0.47;
      box('mast_column', mat.white, 0.085, 0.94, 0.11, mx + 0.02, 0.17 + 0.47, mz, deck);
      box('mast_rail', mat.black, 0.045, 0.9, 0.06, mx - 0.045, 0.17 + 0.45, mz, deck);
      box('mast_head', mat.steel, 0.12, 0.07, 0.115, mx - 0.01, 1.14, mz, deck);
      box('hook', mat.black, 0.05, 0.09, 0.03, mx - 0.075, 1.07, mz, deck);
      for (const hy of [0.22, 0.62, 1.03]) {
        box('climb_bracket', mat.black, 0.09, 0.06, 0.13, mx - 0.04, hy, mz, deck);
        cyl('climb_roller', mat.tire, 0.028, 0.03, mx - 0.085, hy, mz + sz * 0.07, deck, 'z', 28);
      }
    }
    
    // ---------- scissor ----------
    const BAR = 0.62, PZ = 0.24, SX0 = -0.04;
    const bars = [];
    for (const sz of [-1, 1]) {
      for (const k of [0, 1]) {
        const b = new THREE.Mesh(new THREE.BoxGeometry(BAR, 0.03, 0.025), mat.steel);
        b.name = `scissor_bar_${k ? 'b' : 'a'}`; b.position.z = sz * (PZ + k * 0.03); lift.add(b); bars.push({ b, k });
      }
    }
    const pivot = cyl('scissor_pivot', mat.black, 0.014, PZ * 2 + 0.1, 0, 0, 0, lift, 'z', 20);
    const footA = box('scissor_foot', mat.black, 0.08, 0.03, PZ * 2 + 0.1, SX0, TOP + 0.015, 0, lift);
    const footB = box('scissor_slider', mat.black, 0.08, 0.03, PZ * 2 + 0.1, 0, TOP + 0.015, 0, lift);
    const headA = box('scissor_head', mat.black, 0.08, 0.03, PZ * 2 + 0.1, SX0, 0, 0, lift);
    const headB = box('scissor_head_slider', mat.black, 0.08, 0.03, PZ * 2 + 0.1, 0, 0, 0, lift);
    
    const MAXL = 0.4;
    function pose(L) {
      deck.position.y = L;
      const y0 = TOP + 0.03, y1 = DY0 + L - 0.0;
      const h = Math.max(0.01, y1 - y0), span = Math.sqrt(Math.max(0, BAR * BAR - h * h)), ang = Math.atan2(h, span);
      lift.visible = L > 0.004;
      for (const { b, k } of bars) { b.position.set(SX0 + span / 2, y0 + h / 2, b.position.z); b.rotation.z = k ? -ang : ang; }
      pivot.position.set(SX0 + span / 2, y0 + h / 2, 0);
      footB.position.x = SX0 + span; headA.position.y = y1 - 0.0; headB.position.set(SX0 + span, y1, 0);
    }
    return { robot, pose, MAXL };
}

class HaiLoader extends HTMLElement {
  static get observedAttributes() { return ['background', 'footer']; }
  constructor() {
    super();
    this._messages = DEFAULT_MESSAGES;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>${CSS}</style>
      <div class="wrap" role="status" aria-live="polite">
        <div class="view"></div>
        <div class="status">
          <div class="title"><span class="dot"></span><span class="t"></span></div>
          <div class="sub"></div>
          <div class="track"><div class="bar"></div></div>
          <div class="foot"></div>
        </div>
      </div>`;
    this._view = root.querySelector('.view');
    this._title = root.querySelector('.title');
    this._t = root.querySelector('.t');
    this._sub = root.querySelector('.sub');
    this._bar = root.querySelector('.bar');
    this._foot = root.querySelector('.foot');
  }
  set messages(v) { if (Array.isArray(v) && v.length) { this._messages = v; this._msgI = 0; this._showMsg(); } }
  get messages() { return this._messages; }

  attributeChangedCallback(n, _o, v) {
    if (n === 'background') this.style.setProperty('--hai-bg', v || 'transparent');
    if (n === 'footer') this._applyFooter();
  }
  _applyFooter() {
    const f = this.getAttribute('footer');
    this._foot.textContent = f == null ? 'HAI Robotics \u00b7 Intelligent Automation' : f;
    this._foot.style.display = f === '' ? 'none' : '';
  }
  _showMsg() {
    const m = this._messages[this._msgI % this._messages.length];
    this._t.textContent = m[0]; this._sub.textContent = m[1] || '';
  }

  connectedCallback() {
    this._applyFooter();
    this._msgI = 0; this._showMsg();
    if (!this._renderer) this._init();
    this._t0 = performance.now(); this._lastCycle = 0;
    this._renderer.setAnimationLoop(() => this._frame());
    this._ro.observe(this._view);
  }
  disconnectedCallback() {
    this._renderer && this._renderer.setAnimationLoop(null);
    this._ro && this._ro.disconnect();
  }
  /** Free GPU resources (call if you remove the loader permanently). */
  dispose() {
    this.disconnectedCallback();
    this._scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); const ms = o.material ? [].concat(o.material) : []; ms.forEach((m) => { m.map && m.map.dispose(); m.dispose(); }); });
    this._renderer.dispose();
  }

  _init() {
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x000000, 0);
    this._view.appendChild(renderer.domElement);
    this._renderer = renderer;

    const scene = new THREE.Scene(); this._scene = scene;
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 500); this._camera = camera;
    scene.add(new THREE.HemisphereLight(0xffffff, 0xd8d2c4, 1.0));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(0.8, 9, 2.2); key.castShadow = true; key.shadow.mapSize.set(2048, 2048); key.shadow.bias = -0.0002;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xfff4e6, 0.5); fill.position.set(-5, 3, -4); scene.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.ShadowMaterial({ opacity: 0.14 }));
    ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);

    const { robot, pose, MAXL } = buildRobot();
    this._pose = pose; this._MAXL = MAXL;
    robot.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    pose(MAXL);
    const box = new THREE.Box3().setFromObject(robot);
    ground.position.y = box.min.y;
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const span = sphere.radius * 3;
    Object.assign(key.shadow.camera, { left: -span, right: span, top: span, bottom: -span });
    key.shadow.camera.updateProjectionMatrix();
    const tgt = sphere.center.clone(); tgt.y = 0.86;
    const dist = (sphere.radius / Math.tan((camera.fov * Math.PI) / 360)) * 1.35 * 0.84;
    camera.position.copy(tgt).add(new THREE.Vector3(-0.85, 0.38, 1.0).normalize().multiplyScalar(dist));
    camera.near = Math.max(dist / 100, 0.01); camera.far = dist * 100;
    camera.lookAt(tgt);
    scene.add(robot);

    this._ro = new ResizeObserver(() => {
      const w = this._view.clientWidth || 1, h = this._view.clientHeight || 1;
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    });
  }

  _frame() {
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const spring = (k, a) => (k < 0 || k > 1 ? 0 : Math.sin(k * Math.PI * 2) * Math.exp(-k * 4) * a);
    const S = { rest: 0.35, up: 1.1, hold: 0.5, down: 1.1, settle: 0.35 };
    const P = S.rest + S.up + S.hold + S.down + S.settle;
    const speed = parseFloat(this.getAttribute('speed')) || 1;
    const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    const tw = ((performance.now() - this._t0) / 1000) * speed;
    const t = tw % P, MAXL = this._MAXL;
    const a = S.rest, b = a + S.up, c = b + S.hold, d = c + S.down;
    let L;
    if (reduce) L = MAXL * 0.5;
    else if (t < a) L = 0;
    else if (t < b) L = MAXL * ease((t - a) / S.up);
    else if (t < c) L = MAXL + spring((t - b) / S.hold, 0.008);
    else if (t < d) L = MAXL * (1 - ease((t - c) / S.down));
    else L = Math.max(0, -spring((t - d) / S.settle, 0.004));
    this._pose(L);
    if (!reduce) this._bar.style.left = (-32 + 132 * ease((tw % 1.6) / 1.6)) + '%';
    const cycle = Math.floor(tw / P);
    if (cycle !== this._lastCycle) {
      this._lastCycle = cycle;
      this._title.style.opacity = 0; this._sub.style.opacity = 0;
      clearTimeout(this._swapT);
      this._swapT = setTimeout(() => { this._msgI++; this._showMsg(); this._title.style.opacity = 1; this._sub.style.opacity = 1; }, 350);
    }
    this._renderer.render(this._scene, this._camera);
  }
}

if (!customElements.get('hai-loader')) customElements.define('hai-loader', HaiLoader);
export { HaiLoader };
