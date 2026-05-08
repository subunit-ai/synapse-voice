# Synapse Voice — Marketing Site

Static landing page at **voice.subunit.ai** (target). Vanilla HTML/CSS/JS,
no build step. Voicely-inspired layout with subunit-cyan branding,
animated EU-globe, privacy/cloud-mode toggle demo.

## Structure

```
marketing/
├── index.html         Landing page
├── css/style.css      All styling
├── js/
│   ├── main.js        Mode toggle + latest-release fetch
│   └── globe.js       EU-highlighted dotted globe (canvas 2D)
└── assets/
    └── logo.png       Subunit brand
```

## Local preview

```bash
cd marketing && python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

Cloudflare Pages — point a project at `marketing/` directory. Domain
binding: `voice.subunit.ai` via the existing `subunit-server` tunnel.

```
Build command:        (empty)
Build output:         marketing
Root directory:       /
Environment vars:     none
```

## Latest-release fetch

`js/main.js` calls `api.github.com/repos/subunit-ai/synapse-voice/releases/latest`
on page load and rewrites the Download buttons to point at the current
.exe / AppImage. Falls back to the release page on any error.
