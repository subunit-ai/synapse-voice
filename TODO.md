# Sonar — TODO / Roadmap

Stand 2026-05-14 06:00, aktualisiert nach Diarize-Ship + QR-Konzept.
**Aktuelle Version: v0.8.0**

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
- [x] **Meetings-Tab** im Hauptfenster (TJ-Idee 2865) — v0.7.0 LIVE
- [x] **Speaker-Erkennung** (Codex Top 1) — v0.8.0 LIVE (server-side via /v1/diarize)
- [ ] 🔥 **QR-Meeting-Check-In** (TJ-Idee 2026-05-14, msg 2937 + 2939 "DAS IST GENIAL")
  - **v0.9.0-Kandidat — alleine marktfähig (TJ-O-Ton)**
  - Host startet Meeting → QR-Code + 6-stelliger Zahlencode + meet.subunit.ai-URL erscheint
  - Teilnehmer-Flows:
    - Phone: QR scannen → Browser-PWA → Name → Join
    - PC/ohne Cam: meet.subunit.ai aufrufen → 6-Stellen-Code tippen → Name → Join
  - Web-PWA fordert Mic-Permission, streamt WebRTC zum Sonar-Server (Hamburg)
  - Host-View: Live-Liste der eingecheckten Teilnehmer mit Avatar + Name + Timestamp
  - Host kann manuell starten ODER warten auf alle vorher geplanten Teilnehmer
  - Meeting im Voraus planbar (scheduled_at + expected_participants, pre-issued Codes)
  - Pro Teilnehmer eigene Audio-Spur → Whisper parallel → Speaker = QR-Name (echte Namen)
  - Pro Teilnehmer eigener Magic-Link mit Protokoll per Mail
  - Host steuert was Teilnehmer X aus dem Master sieht
  - **DSGVO-Killer**: Check-In = expliziter Aufnahme-Consent (Audit-Trail)
  - Konkurrenz hat das nicht — Granola/Read.ai/Otter nehmen System-Audio aus 1 Mic auf
  - Komponenten: Meeting-Session-API, meet.subunit.ai PWA (neue Surface), SFU (pion-go/mediasoup), Per-Stream-Recording, Post-Meeting-Pipeline, Per-Teilnehmer-Mail-Versand, Sonar-Desktop QR-Modal + Live-Liste + Planung
  - Konzept-Doc: `~/.claude/projects/-home-subunit-subunit-unitone/memory/project_sonar-qr-meeting-checkin.md`
  - Pricing-Implikation: Agency-Tier (€19-29/Monat), Cloud-Infra-Kosten je Meeting-Minute

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
