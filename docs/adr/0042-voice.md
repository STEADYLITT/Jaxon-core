# ADR-042: AI voice (ElevenLabs) for course narration and ad voiceovers

**Status:** accepted, 2026-11-27

## Decision
- **Calls:**
  - Generation: `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128` with `xi-api-key`, returning the mp3 bytes.
  - Test: `GET /v1/user/subscription`, which is free and shows the plan and characters left.
  - Voices: `GET /v2/voices`, which lists Steady's own library only.
- **Task `voice_generate`** (the "Voice Narrator" on the Courses building's Lessons & Media floor) runs these steps in order:
  1. Loads the script, either from a course lesson or as given text. A course drafted by the MOCK model is refused.
  2. Runs the claims check. Any problem stops the task before any cost.
  3. Shows the estimate as a SPEND (`always_ask`): owner approval, or an active Launch's budget.
  4. Reserves the budget, generates, and settles the spend.
  5. Stores the mp3 as an `audio/mpeg` artifact with `meta.ai_generated = true`, labeled **AI voice**.
- **Estimate:**
  - Multilingual v2 is 8 cents per 1,000 characters, and Flash v2.5 is 4. `ELEVENLABS_CENTS_PER_1K_CHARS` overrides both.
  - Scripts longer than a model's per-request limit (10k or 40k characters) are split on paragraph and sentence boundaries. The mp3 parts are joined.
  - The cap is 30,000 characters per task.
- **Consent:** Jackson never clones a voice. It only uses voice ids from the account's own library.

## Without keys
No `ELEVENLABS_API_KEY`: the task still asks for approval of the estimate, then produces no audio, costs $0, and is labeled `[MOCK]`.

## API
- `GET /tenants/{t}/voice/voices`
- `POST /tenants/{t}/voice` with `{voice_id, purpose: narration|ad, text | course_id+module+lesson}`. It returns the run, the character count and the estimate.
