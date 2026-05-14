# Sonar — TODO / Roadmap

Stand 2026-05-14, aktualisiert nach 7-Releases-Tag v0.5.7→v0.6.1.
**Aktuelle Version: v0.6.1**

## ✅ Komplett (v0.3.x → v0.6.1)
- Local-Toggle, Local-Model-Picker, Cloud-Provider-UI
- Auto-paste (durch v0.5.7→v0.5.10 stabilisiert, inkl. Win-ARM)
- Auto-Update (arch-aware, IsWow64Process2, filename-fix)
- Orb-Overlay (Default-Renderer, Verlet-Physik, Idle-Pulse, 3-Punkte-Picker)
- Right-Click-Drag to move + v0.5.11 left-click-drag with threshold
- Searchable Language-Picker (99 Sprachen)
- Mic-Device-Picker + Live-Level-Meter
- Audio-Cleanup mit 5 Styles: tidy / formal / summary / action_items / minutes / decisions / raw
- Long-Form Auto-Switch (>=240s → style "raw", v0.6.1)
- Auto-Language-Detect (faster-whisper language=None)
- Cleanup Server-Hardening (refusal+halluzination prompt-fix)
- Onboarding-Tutorial beim Erst-Start
- Subtle Sounds beim Hotkey-Press (sounds.py)
- Lexikon (Custom-Vocab via auto_mode_overrides)
- Click-through wenn nicht hovered (v0.3.24)
- Email-Signup mit Resend 6-Code (eigenes System, bald durch Auth.subunit.ai abgeloest)
- Orb-UX v0.5.11: Satellites 9→14 + NoDropShadowWindowHint auf Win11

## 🔨 Wirklich offen

### Orb-Polish
- [ ] **Glas-Effekt verfeinern** — TJ-Feedback ausstehend (Geschmacksfrage, nicht selbst entscheidbar)
- [ ] **Groesse-Setting** S/M/L (heute fix Default)
- [ ] **Audio-Vis-Styles** umschaltbar: Wellen / Faeden / Klassisch (heute nur Verlet-Spheren)

### Killer-Feature (gross)
- [ ] **Meetings-Tab** im Hauptfenster (TJ-Idee 2865)
  - Liste aller Long-Form-Aufnahmen + Transcripts
  - Deep-Dive: einzelnes Meeting öffnen → Transcript, Cleanup-Versionen (summary/action_items/minutes/decisions/raw), Speaker-Marker
  - "Tasks rausziehen"-Button → POST zu api.subunit.ai/tasks via lokaler Bridge → erscheint in Subunit-App
  - Search ueber alle Transcripts
  - Verzahnt direkt mit Phase-1-Foundation

### Marketing
- [ ] **voice.subunit.ai** Marketing-Site
  - Three.js EU-Globe mit Pulsen
  - Normal/Privacy-Mode Toggle-Demo animiert
  - DSGVO-Hero Subunit-Cyan
  - EU-Server-Hamburg hervorheben
  - Cloudflare-Pages Deploy

### Integration (mit Phase-1-Foundation)
- [ ] **Auth-Migration** — Sonar nutzt heute eigenes Sign-up-System, migrieren zu auth.subunit.ai (OAuth2 Authorization Code + PKCE). Bestandsuser per Password-Reset-Flow uebernehmen.
- [ ] **Bridge-Daemon bundled** im Sonar-Installer — Sonar-Installer installiert subunit-bridge mit, Sonar redet via localhost:7842 mit Bridge fuer Decisions/Tasks/Memory.

## 🐛 Verify (Erik)
- [ ] v0.6.1 auf Win-ARM Surface Pro testen (Long-Form raw + 240s threshold + autopaste + orb-drag)

## ❌ Aus dem Scope (TJ explizit)
- ~~Snippets~~ — durch Lexikon abgedeckt
- ~~macOS-Build~~ — kommt spaeter

## 📐 Hinweise
- Sonar = Brand (Repo + CONFIG_DIR weiterhin `synapse-voice` aus Kompat-Gruenden)
- Server-Cleanup ist gehaertet — kein medizin-disclaimer-Halluzinationen mehr
- 5+ verschiedene Cleanup-Styles auswählbar
