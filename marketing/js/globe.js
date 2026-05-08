/**
 * EU-highlighted dotted globe — pure canvas 2D, no Three.js dependency
 * (lighter, no module-loader gymnastics, runs offline). Voicely-inspired:
 * thousands of small dots forming a continent map, with the EU region
 * pulsing in cyan and a slow rotation.
 *
 * Coordinates: dot positions are projected from a sphere via lat/lon.
 * Continents are sketched by a hand-tuned bitmap; for v0.4 simplicity
 * we generate dots from a procedural mask that's "good enough" — Africa,
 * Europe and Eurasia outlines come from a tiny vector polygon set.
 */
(() => {
  const canvas = document.getElementById('globe-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  const cx = W / 2;
  const cy = H / 2;
  const R = Math.min(W, H) * 0.42;

  // Generate a dense grid of (lat, lon) points and keep the ones that
  // fall on land. We use an extremely simplified land-mask sampled from
  // the embedded continent polygons below; close enough for the Voicely
  // "many small dots" aesthetic.
  const dots = [];
  for (let lat = -85; lat <= 85; lat += 3.2) {
    const stepLon = 3.2 / Math.cos(lat * Math.PI / 180);
    for (let lon = -180; lon <= 180; lon += stepLon) {
      if (isLand(lat, lon)) {
        dots.push({ lat, lon, eu: isEU(lat, lon) });
      }
    }
  }

  let rotation = 14; // start so Europe faces forward
  let target = rotation;
  let mouseX = cx, mouseY = cy;

  // Reduce rotation while user hovers the canvas — feels intentional
  canvas.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    mouseX = e.clientX - rect.left;
    mouseY = e.clientY - rect.top;
    target = 14 + ((mouseX - cx) / cx) * 30;
  });
  canvas.addEventListener('mouseleave', () => { target = 14; });

  let last = performance.now();
  function tick(now) {
    const dt = (now - last) / 1000;
    last = now;
    rotation += (target - rotation) * Math.min(1, dt * 2.4);
    rotation += dt * 4; // slow autorotate
    draw();
    requestAnimationFrame(tick);
  }

  function project(lat, lon) {
    const phi = lat * Math.PI / 180;
    const theta = (lon + rotation) * Math.PI / 180;
    const x = Math.cos(phi) * Math.sin(theta);
    const y = -Math.sin(phi);
    const z = Math.cos(phi) * Math.cos(theta);
    return { x, y, z };
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    // Backdrop ring
    const grad = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 1.4);
    grad.addColorStop(0, 'rgba(20, 50, 70, 0.4)');
    grad.addColorStop(1, 'rgba(20, 50, 70, 0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.4, 0, Math.PI * 2);
    ctx.fill();

    // EU pulse aura (concentric soft rings)
    const t = performance.now() / 1000;
    for (let i = 0; i < 3; i++) {
      const phase = (t * 0.4 + i / 3) % 1;
      const r = R * 0.05 + phase * R * 0.45;
      const alpha = (1 - phase) * 0.12;
      ctx.strokeStyle = `rgba(64, 214, 255, ${alpha})`;
      ctx.lineWidth = 1.5;
      const eu = project(50, 10);  // ~Frankfurt
      if (eu.z > 0) {
        ctx.beginPath();
        ctx.arc(cx + eu.x * R, cy + eu.y * R, r, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    // Dots
    for (const d of dots) {
      const p = project(d.lat, d.lon);
      if (p.z < -0.05) continue; // back of globe
      const size = 1.2 + p.z * 1.0;
      const alpha = Math.max(0.1, p.z * 0.85);
      if (d.eu) {
        ctx.fillStyle = `rgba(64, 214, 255, ${alpha + 0.15})`;
        ctx.shadowColor = 'rgba(64, 214, 255, 0.7)';
        ctx.shadowBlur = 6;
      } else {
        ctx.fillStyle = `rgba(180, 220, 240, ${alpha * 0.6})`;
        ctx.shadowBlur = 0;
      }
      ctx.beginPath();
      ctx.arc(cx + p.x * R, cy + p.y * R, size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;

    // Frankfurt marker
    const fr = project(50.11, 8.68);
    if (fr.z > 0) {
      const fx = cx + fr.x * R;
      const fy = cy + fr.y * R;
      const pulse = 0.5 + 0.5 * Math.sin(t * 3);
      ctx.fillStyle = `rgba(64, 214, 255, ${0.6 + pulse * 0.4})`;
      ctx.shadowColor = 'rgba(64, 214, 255, 1)';
      ctx.shadowBlur = 14;
      ctx.beginPath();
      ctx.arc(fx, fy, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  // Approximate land mask via simple bbox unions. Good enough for the
  // visual — we're not making a geo-app, we're making a vibe.
  function isLand(lat, lon) {
    return (
      // Europe + Russia core
      (lat >= 36 && lat <= 70 && lon >= -10 && lon <= 60) ||
      (lat >= 36 && lat <= 75 && lon >= 60 && lon <= 180) ||
      // North Africa
      (lat >= 12 && lat < 36 && lon >= -17 && lon <= 50) ||
      // Sub-Saharan Africa
      (lat >= -34 && lat < 12 && lon >= -18 && lon <= 50) ||
      // Asia
      (lat >= 10 && lat < 50 && lon >= 60 && lon <= 145) ||
      (lat >= -10 && lat < 10 && lon >= 95 && lon <= 142) ||
      // North America
      (lat >= 25 && lat <= 75 && lon >= -170 && lon <= -50) ||
      (lat >= 8 && lat < 25 && lon >= -118 && lon <= -75) ||
      // South America
      (lat >= -55 && lat < 12 && lon >= -82 && lon <= -34) ||
      // Australia
      (lat >= -38 && lat < -10 && lon >= 113 && lon <= 154)
    );
  }

  function isEU(lat, lon) {
    // EU + EFTA bounding box (approximate, includes UK and Ukraine region)
    return lat >= 36 && lat <= 70 && lon >= -10 && lon <= 35;
  }

  requestAnimationFrame(tick);
})();
