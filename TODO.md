# Synapse Voice — TODO / Roadmap

Stand 2026-05-08, abgeleitet aus TJs Voicely-Walkthrough + heutigen Tests.

## ✅ Heute geshipped (8. Mai 2026)
- [x] **v0.3.2** — Local-Toggle + Local-Modell-Picker auf Hauptseite, Cloud-Dropdown nur Cloud, Account-UX-Klarheit, .ico-Build-Fix
- [x] **v0.3.3** — Auto-paste deep fix (AttachThreadInput offen halten, SendMessageTimeout statt PostMessage, Child-Walk fuer RichEditD2DPT) ✅ **TJ confirmed "funktioniert"**
- [x] **v0.3.4** — Auto-Update laedt + installiert selbst (Download → NSIS spawn → App quit → Replace → Restart)
- [x] **v0.3.5** — Orb-Overlay-Preview (Verlet-Spheren, 3-Punkte-Picker, Hover-Satellites, hinter Settings-Toggle)
- [x] **v0.3.6** — Orb ist Default + Right-Click-Drag-to-Move + Settings-Tab "Overlay" (Theme/Position/Pulse)
- [x] **v0.3.7** — Searchable Language-Picker mit 99 Sprachen (statt 5-Cycle)
- [x] **v0.3.8** — Mic-Device-Picker + gentler Idle-Physik + Pulse-Toggle wirkt
- [x] **AI-Cleanup** ist bereits drin (Claude Haiku via OpenRouter, "tidy"/"formal"-Stil) — Settings-Toggle aktivieren

## 🔨 v0.4 — Floating-Overlay (Killer-Feature)
- [x] **Orb-Overlay** statt simple Bubble — schwebende Glaspheren mit Verlet-Physik, reagieren auf Mic-Pegel
- [x] **3-Punkte-Picker** an der Orb (Hover): links Sprache, rechts Tonalitaet, oben Local/Cloud-Toggle
- [x] **Idle-Pulse** — atmender Glow (subtle, abschaltbar via Settings)
- [x] **Sprach-Picker** im Overlay mit Suche + 99 Sprachen
- [x] **Personalisierung**: Position (4 Ecken / Custom-Drag) + Farbe (Cyan / Violet / Mint)
- [x] **Settings-Tab "Overlay"**
- [x] Klassische Bubble bleibt als Fallback-Option (mutually exclusive)
- [ ] **Glas-Effekt verfeinern** — TJ-Feedback ausstehend
- [ ] **Click-through wenn nicht hovered** — Orb soll nicht Klicks nach hinten blockieren
- [ ] **Groesse** (S/M/L) in Settings (heute nur Default)

## 🔨 v0.5 — Mic-Polish
- [x] **Mic-Device-Picker** in Settings ✅ v0.3.8
- [ ] **Live-Level-Meter** zum Test direkt darunter
- [ ] **Audio-Vis-Styles**: Wellen / Faeden / Klassisch (im Overlay umschaltbar)
- [ ] **Subtle-Sound** beim Hotkey-Press (custom WAV, hochwertig, nicht penetrant)

## 🔨 v0.6 — Onboarding + Lexikon
- [ ] **Onboarding-Tutorial** beim ersten Start (4 Steps: Hotkey → Local/Cloud → Account → Test-Aufnahme)
- [ ] **Lexikon** — Custom-Vocab "klingt wie X → schreibe Y", als Whisper-Prompt-Hint + Post-Process

## 🔨 Marketing-Site — voice.subunit.ai
- [ ] Three.js animated EU-Globe mit Pulsen
- [ ] Normal/Privacy-Mode Toggle-Demo (animiert)
- [ ] DSGVO-Hero im Subunit-Cyan
- [ ] EU-Server-Hamburg-Hervorhebung
- [ ] Cloudflare-Pages Deploy
- [ ] Eigenes Repo `marketing/` im Synapse-Voice-Projekt

## ❌ Aus dem Scope (TJ explizit)
- ~~Snippets~~ — "brauchen wir aber das mal nicht" (durch Lexikon abgedeckt)
- ~~macOS-Build~~ — kommt spaeter

## 🐛 Offene Bugs / zu verifizieren
- [ ] **Auto-paste auf Win11 nach v0.3.3** — TJ-Re-Test ausstehend; Logs zeigen jetzt Klassen-Namen jeder Strategie
- [ ] **Auto-Update v0.3.4** — Erst-Test ausstehend; ab dann sollten alle zukuenftigen Updates seamless sein

## 📐 Technische Skizze v0.4 Orb
```
synapse_voice/ui/orb_overlay.py
  ├── OrbWindow(QWidget) — frameless, translucent, top-most, click-through-when-idle
  ├── OrbPhysics — Verlet-Solver fuer 8-12 Spheres
  ├── OrbAudioReactor — RMS-Sample alle 30ms, mappt auf Sphere-Velocity
  ├── OrbDotPicker(QWidget) — appears on hover, 3 satellite buttons
  └── OrbConfig (in Config) — position, size, color, idle_pulse_enabled
```

Bubble-Code bleibt unter `bubble.py`, neuer Default-Renderer = OrbWindow,
Settings → Overlay → "Klassisches Design verwenden" als Opt-out.
