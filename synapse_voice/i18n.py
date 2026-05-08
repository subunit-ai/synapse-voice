"""Lightweight translation system.

Dict-based instead of Qt's QTranslator/.ts/.qm pipeline — keeps the
build simple, no msgfmt step, edits land in this file directly. Two
languages: de (default) and en. Strings are looked up by stable key
("settings.tab.general"); missing keys fall back to the key itself
so the UI never breaks on an untranslated string.
"""
from __future__ import annotations

from typing import Optional


_LANG_DE: dict[str, str] = {
    # General
    "app.name": "Synapse Voice",
    "app.tagline": "Hotkey-Diktat fuer alle deine Apps",

    # Onboarding wizard
    "onb.welcome.title": "Willkommen.",
    "onb.welcome.sub": "Lass uns dich in 60 Sekunden zum Diktieren bringen.",
    "onb.feature.local.title": "Lokal-first als Standard",
    "onb.feature.local.body": "Audio verlaesst dein Geraet nicht — ausser du aktivierst Cloud.",
    "onb.feature.quality.title": "Whisper-Qualitaet, ohne Reibung",
    "onb.feature.quality.body": "Hotkey druecken, sprechen, paste. Kein Window-Switch, kein Copy-Paste.",
    "onb.feature.dsgvo.title": "DSGVO-konforme Cloud-Option",
    "onb.feature.dsgvo.body": "Wenn du Cloud waehlst, laeuft der Subunit-Server in Hamburg.",
    "onb.feature.daily.title": "Fuer taegliches Diktat gebaut",
    "onb.feature.daily.body": "Lexikon fuer Eigennamen. AI-Cleanup. 99 Sprachen. Auto-Update.",

    "onb.hotkey.title": "Waehl deinen Hotkey",
    "onb.hotkey.sub": "Die Tastenkombination zum Diktieren. Standard: Strg + Leertaste — gut mit einer Hand erreichbar. Halten zum Aufnehmen, loslassen zum Transkribieren.",
    "onb.hotkey.hint": "Klicke den Button → druecke deine gewuenschte Kombination",
    "onb.hotkey.live_hint": "Druecke jetzt deine Tasten — die Anzeige reagiert live",

    "onb.mode.title": "Lokal oder Cloud?",
    "onb.mode.local": "Lokal",
    "onb.mode.local.subtitle": "100% privat",
    "onb.mode.local.body": "Whisper laeuft auf deiner Maschine. Audio verlaesst das Geraet nicht. Optimal fuer sensible Inhalte.",
    "onb.mode.local.badge": "Standard",
    "onb.mode.cloud": "Cloud",
    "onb.mode.cloud.subtitle": "Schneller, genauer",
    "onb.mode.cloud.body": "Routet ueber Subunit-Server in Hamburg. DSGVO-konform. Etwas schneller als Local auf einem typischen Laptop.",
    "onb.mode.cloud.badge": "Nur EU",

    "onb.test.title": "Probier's aus",
    "onb.test.sub": "Sprich ins Mikro. Die Leiste unten zeigt das Live-Signal. Wenn du bereit bist, klicke Fertig — danach kannst du den Hotkey aus jeder App heraus druecken.",
    "onb.test.mic_label": "Mikrofon-Test",
    "onb.test.try_label": "Hier reinpasten — druecke deinen Hotkey, sprich, und schau ob's landet:",
    "onb.test.try_placeholder": "Cursor hier reinklicken, dann Hotkey halten, sprechen, loslassen…",
    "onb.test.ready": "✓ Bereit. Klicke Fertig um Synapse Voice zu starten.",
    "onb.test.your_hotkey": "Dein Hotkey:",

    "onb.btn.back": "Zurueck",
    "onb.btn.skip": "Ueberspringen",
    "onb.btn.next": "Weiter",
    "onb.btn.finish": "Fertig",

    # Account step (v0.3.21)
    "onb.account.title": "Konto erstellen",
    "onb.account.sub": "Mit deiner E-Mail anmelden — du bekommst 7 Tage kostenlosen Pro-Trial. Kein Passwort, kein Kreditkarten-Zwang, jederzeit kuendbar.",
    "onb.account.email_label": "E-Mail",
    "onb.account.email_placeholder": "du@firma.de",
    "onb.account.btn.signup": "Konto erstellen + Trial starten",
    "onb.account.btn.skip": "Spaeter — erstmal nur lokal nutzen",
    "onb.account.signing_up": "Erstelle Konto…",
    "onb.account.success": "✓ Konto erstellt. 7 Tage Pro-Trial laeuft.",
    "onb.account.exists": "Diese E-Mail ist schon registriert. Bei Schluesselverlust: support@subunit.ai",
    "onb.account.invalid": "Bitte eine gueltige E-Mail eingeben.",
    "onb.account.network_error": "Verbindungsfehler. Bist du online?",
    "onb.account.benefit.privacy": "Privacy-by-design — wir loggen keine Inhalte",
    "onb.account.benefit.eu": "Server in Hamburg, DSGVO-konform",
    "onb.account.benefit.cancel": "Jederzeit kuendbar, kein Risiko",

    # Theme step (v0.3.21)
    "onb.theme.title": "Dark oder Light?",
    "onb.theme.sub": "Wie soll die App aussehen? Du kannst das jederzeit in den Einstellungen aendern.",
    "onb.theme.dark": "Dark",
    "onb.theme.dark.body": "Augenfreundlich bei Nacht — Subunit-Standard",
    "onb.theme.light": "Light",
    "onb.theme.light.body": "Hell, bei Tageslicht angenehm",

    # Main window
    "mw.privacy_label": "100% PRIVAT",
    "mw.privacy_sub": "Audio verlaesst dieses Geraet nicht",
    "mw.cloud_label": "CLOUD",
    "mw.cloud_sub": "DSGVO · EU-Server · Subunit",
    "mw.local_model": "Lokales Modell",
    "mw.cloud_provider": "Cloud-Provider",
    "mw.recent": "Letzte Transkriptionen",
    "mw.recent_empty": "Noch keine Transkriptionen — druecke deinen Hotkey um zu starten",
    "mw.btn.full_history": "Verlauf…",
    "mw.btn.settings": "Einstellungen…",
    "mw.btn.hide": "In Tray verstecken",
    "mw.btn.quit": "Beenden",
    "mw.stat.transcribed": "TRANSKRIBIERT",
    "mw.stat.audio": "AUDIO VERARBEITET",
    "mw.stat.saved": "ZEIT GESPART",
    "mw.hotkey": "Hotkey:",

    # Settings
    "set.title": "Einstellungen",
    "set.tab.general": "Allgemein",
    "set.tab.transcription": "Transkription",
    "set.tab.vocabulary": "Lexikon",
    "set.tab.overlay": "Overlay",
    "set.tab.account": "Konto",
    "set.tab.about": "Ueber",
    "set.section.hotkey": "Hotkey",
    "set.section.language": "Sprache",
    "set.section.behavior": "Verhalten",
    "set.section.recording_mode": "Aufnahme-Modus",
    "set.section.cleanup": "AI-Cleanup",
    "set.section.updates": "Updates",
    "set.section.microphone": "Mikrofon",
    "set.section.ui_language": "App-Sprache",

    # Buttons / generic
    "btn.ok": "OK",
    "btn.cancel": "Abbrechen",
    "btn.save": "Speichern",
    "btn.close": "Schliessen",

    # Orb satellite popups
    "orb.mode.title": "Modus",
    "orb.mode.local": "Lokal",
    "orb.mode.cloud": "Cloud",
    "orb.cleanup.title": "Cleanup",
    "orb.cleanup.off": "Aus",
    "orb.cleanup.tidy": "Bereinigen",
    "orb.cleanup.prompt": "Prompt",
    "orb.cleanup.email": "E-Mail",
    "orb.cleanup.slack": "Slack",
    "orb.cleanup.formal": "Formal",
}

# English: all keys mapped 1:1; missing entries fall back to the key.
_LANG_EN: dict[str, str] = {
    "app.name": "Synapse Voice",
    "app.tagline": "Hotkey dictation for every app",

    "onb.welcome.title": "Welcome.",
    "onb.welcome.sub": "Let's get you dictating in 60 seconds.",
    "onb.feature.local.title": "Local-first by default",
    "onb.feature.local.body": "Audio never leaves your machine — unless you opt in to cloud.",
    "onb.feature.quality.title": "Whisper-quality, zero friction",
    "onb.feature.quality.body": "Press a hotkey, speak, paste. No window-switching, no copy-paste.",
    "onb.feature.dsgvo.title": "DSGVO-compliant cloud option",
    "onb.feature.dsgvo.body": "If you switch to cloud, the Subunit-Server runs in Hamburg.",
    "onb.feature.daily.title": "Built for daily dictation",
    "onb.feature.daily.body": "Lexikon for proper nouns. AI cleanup. 99 languages. Auto-update.",

    "onb.hotkey.title": "Pick your hotkey",
    "onb.hotkey.sub": "This is the key combo you press to dictate. The default is Ctrl + Space — easy to reach with one hand. Hold to record, release to transcribe.",
    "onb.hotkey.hint": "Click the button → press your preferred combo",
    "onb.hotkey.live_hint": "Press your keys now — the display reacts live",

    "onb.mode.title": "Local or cloud?",
    "onb.mode.local": "Local",
    "onb.mode.local.subtitle": "100% private",
    "onb.mode.local.body": "Whisper runs on your machine. Audio never leaves the device. Best for sensitive content.",
    "onb.mode.local.badge": "Default",
    "onb.mode.cloud": "Cloud",
    "onb.mode.cloud.subtitle": "Faster, more accurate",
    "onb.mode.cloud.body": "Routed through Subunit-Server in Hamburg. DSGVO-compliant. Slightly faster than Local on a typical laptop.",
    "onb.mode.cloud.badge": "EU only",

    "onb.test.title": "Try it out",
    "onb.test.sub": "Speak into your mic. The bar below shows the live signal. When you're ready, finish setup and press your hotkey from any app to start dictating.",
    "onb.test.mic_label": "Microphone test",
    "onb.test.try_label": "Type or dictate here — press your hotkey, speak, see it land:",
    "onb.test.try_placeholder": "Click here, then hold your hotkey, speak, release…",
    "onb.test.ready": "✓ Ready to go. Press Finish to start using Synapse Voice.",
    "onb.test.your_hotkey": "Your hotkey:",

    "onb.btn.back": "Back",
    "onb.btn.skip": "Skip",
    "onb.btn.next": "Next",
    "onb.btn.finish": "Finish",

    "onb.account.title": "Create your account",
    "onb.account.sub": "Sign up with your email — you'll get a free 7-day Pro trial. No password, no credit card required, cancel any time.",
    "onb.account.email_label": "Email",
    "onb.account.email_placeholder": "you@company.com",
    "onb.account.btn.signup": "Create account + start trial",
    "onb.account.btn.skip": "Later — just use it locally for now",
    "onb.account.signing_up": "Creating account…",
    "onb.account.success": "✓ Account created. 7-day Pro trial active.",
    "onb.account.exists": "This email is already registered. If you lost your key: support@subunit.ai",
    "onb.account.invalid": "Please enter a valid email.",
    "onb.account.network_error": "Connection error. Are you online?",
    "onb.account.benefit.privacy": "Privacy-by-design — we never log content",
    "onb.account.benefit.eu": "Servers in Hamburg, DSGVO-compliant",
    "onb.account.benefit.cancel": "Cancel any time, no risk",

    "onb.theme.title": "Dark or light?",
    "onb.theme.sub": "How should the app look? You can change this any time in settings.",
    "onb.theme.dark": "Dark",
    "onb.theme.dark.body": "Easy on the eyes at night — Subunit default",
    "onb.theme.light": "Light",
    "onb.theme.light.body": "Bright, comfortable in daylight",

    "mw.privacy_label": "100% PRIVATE",
    "mw.privacy_sub": "Audio never leaves this device",
    "mw.cloud_label": "CLOUD",
    "mw.cloud_sub": "DSGVO · EU-Server · Subunit",
    "mw.local_model": "Local model",
    "mw.cloud_provider": "Cloud provider",
    "mw.recent": "Recent transcriptions",
    "mw.recent_empty": "No transcriptions yet — press your hotkey to begin",
    "mw.btn.full_history": "Full history…",
    "mw.btn.settings": "Settings…",
    "mw.btn.hide": "Hide to tray",
    "mw.btn.quit": "Quit",
    "mw.stat.transcribed": "TRANSCRIBED",
    "mw.stat.audio": "AUDIO PROCESSED",
    "mw.stat.saved": "TIME SAVED",
    "mw.hotkey": "Hotkey:",

    "set.title": "Settings",
    "set.tab.general": "General",
    "set.tab.transcription": "Transcription",
    "set.tab.vocabulary": "Vocabulary",
    "set.tab.overlay": "Overlay",
    "set.tab.account": "Account",
    "set.tab.about": "About",
    "set.section.hotkey": "Hotkey",
    "set.section.language": "Language",
    "set.section.behavior": "Behaviour",
    "set.section.recording_mode": "Recording mode",
    "set.section.cleanup": "AI cleanup",
    "set.section.updates": "Updates",
    "set.section.microphone": "Microphone",
    "set.section.ui_language": "App language",

    "btn.ok": "OK",
    "btn.cancel": "Cancel",
    "btn.save": "Save",
    "btn.close": "Close",

    "orb.mode.title": "Mode",
    "orb.mode.local": "Local",
    "orb.mode.cloud": "Cloud",
    "orb.cleanup.title": "Cleanup",
    "orb.cleanup.off": "Off",
    "orb.cleanup.tidy": "Tidy",
    "orb.cleanup.prompt": "Prompt",
    "orb.cleanup.email": "Email",
    "orb.cleanup.slack": "Slack",
    "orb.cleanup.formal": "Formal",
}


_BUNDLES = {"de": _LANG_DE, "en": _LANG_EN}
_current = "de"


def set_language(lang: str) -> None:
    """Switch the active language. `lang` is "de" or "en". Other values
    fall back to "en" silently — the runtime never raises on i18n input
    so a corrupted config can't brick the UI."""
    global _current
    _current = lang if lang in _BUNDLES else "en"


def current_language() -> str:
    return _current


def tr(key: str, default: Optional[str] = None) -> str:
    """Lookup a translation by key. Falls back to the en bundle, then
    to the supplied default, then to the key itself — UI never sees a
    KeyError. The key's dotted format is convention only, not enforced."""
    bundle = _BUNDLES.get(_current, _LANG_EN)
    if key in bundle:
        return bundle[key]
    if key in _LANG_EN:
        return _LANG_EN[key]
    return default if default is not None else key
