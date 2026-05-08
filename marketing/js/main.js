/**
 * Marketing-site interactivity:
 *  - Privacy/Cloud-mode toggle in the demo card
 *  - Latest-release pull from GitHub Releases API for the download cards
 */

// ─── Mode toggle ────────────────────────────────────────────────────────────
(() => {
  const sw = document.querySelector('.toggle-switch');
  if (!sw) return;
  const flowLocal = document.getElementById('flow-local');
  const flowCloud = document.getElementById('flow-cloud');
  const title = document.getElementById('toggle-title');
  const sub = document.getElementById('toggle-sub');
  const note = document.getElementById('toggle-footnote');

  const labels = {
    local: {
      title: 'Local Mode',
      sub: 'Whisper laeuft komplett auf deiner Maschine',
      note: '🔒 Audio verlaesst dein Geraet nicht — perfekt fuer sensible Inhalte',
    },
    cloud: {
      title: 'Cloud Mode',
      sub: 'Schnellere Verarbeitung ueber Subunit EU-Server',
      note: '🇪🇺 Frankfurt · DSGVO-konform · End-to-End verschluesselt',
    },
  };

  function set(mode) {
    sw.setAttribute('aria-checked', mode === 'local' ? 'true' : 'false');
    flowLocal.classList.toggle('flow-hidden', mode !== 'local');
    flowCloud.classList.toggle('flow-hidden', mode !== 'cloud');
    title.textContent = labels[mode].title;
    sub.textContent = labels[mode].sub;
    note.innerHTML = labels[mode].note;
  }

  sw.addEventListener('click', () => {
    const next = sw.getAttribute('aria-checked') === 'true' ? 'cloud' : 'local';
    set(next);
  });
  sw.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      sw.click();
    }
  });
})();

// ─── Latest release auto-fill ───────────────────────────────────────────────
(() => {
  const winCard = document.getElementById('dl-windows');
  const linuxCard = document.getElementById('dl-linux');
  const winVer = document.getElementById('dl-version-win');
  const linuxVer = document.getElementById('dl-version-linux');

  fetch('https://api.github.com/repos/subunit-ai/synapse-voice/releases/latest', {
    headers: { Accept: 'application/vnd.github+json' },
  })
    .then((r) => r.json())
    .then((release) => {
      if (!release || !release.assets) return;
      const tag = release.tag_name || '';
      const assets = release.assets;

      const setupExe = assets.find(
        (a) => a.name && a.name.toLowerCase().endsWith('.exe') &&
               a.name.toLowerCase().includes('setup')
      );
      const appImage = assets.find(
        (a) => a.name && a.name.toLowerCase().endsWith('.appimage')
      );

      if (setupExe && winCard) {
        winCard.href = setupExe.browser_download_url;
        if (winVer) winVer.textContent = `${tag} · neueste Version`;
      }
      if (appImage && linuxCard) {
        linuxCard.href = appImage.browser_download_url;
        if (linuxVer) linuxVer.textContent = `${tag} · neueste Version`;
      }
    })
    .catch((e) => {
      // Network blocked / rate-limited / offline preview — fall back to
      // the release page so the user still gets to download something.
      console.warn('Latest-release fetch failed:', e);
      const fallback = 'https://github.com/subunit-ai/synapse-voice/releases/latest';
      if (winCard) winCard.href = fallback;
      if (linuxCard) linuxCard.href = fallback;
      if (winVer) winVer.textContent = 'Zur Release-Seite →';
      if (linuxVer) linuxVer.textContent = 'Zur Release-Seite →';
    });
})();
