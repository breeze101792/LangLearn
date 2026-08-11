-- 007_tts_provider.sql — persist the user's TTS provider choice.
--
-- The TTS module is swappable; we store the selected provider name in
-- settings so the user can switch from the Settings page. The column
-- defaults to "google" because that's the only provider shipped today.
-- The value is validated against the live tts registry by the settings
-- service, so a renamed or removed provider is rejected on save.

ALTER TABLE settings ADD COLUMN tts_provider TEXT NOT NULL DEFAULT 'google';
