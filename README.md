# QuickPrompt AI

**System-wide AI prompt enhancer and voice dictation for Windows.** Select text
in *any* application and press **Ctrl+Shift+E** to get a corrected, structured,
high-quality prompt back. Or press **Ctrl+Shift+S**, speak, and your words are
typed straight into the caret you left behind.

```
Select text anywhere → Ctrl+Shift+E → AI enhances it → popup → Copy / Replace
```

```
Put your caret in any app → Ctrl+Shift+S → speak → press again → text is typed
```

- Works with **OpenAI**, **Anthropic Claude**, **Google Gemini**, **Groq**, and any
  **OpenAI-compatible** endpoint (DeepSeek, LM Studio, Ollama, OpenRouter, …)
- Three enhancement modes: **Quick Fix**, **Prompt Enhance**, **Advanced Optimize**
- **Dictation** that types into the app you're working in — not into a separate box
- Global, configurable keyboard shortcuts for both features
- Safe text replacement with window-identity verification and clipboard restore
- API keys stored in **Windows Credential Manager** (never on disk in plaintext)
- One-file, windowed `QuickPromptAI.exe` — no terminal, no admin rights needed

---

## Table of contents

1. [Install & Run](#install--run)
2. [Configure an AI provider](#configure-an-ai-provider)
3. [Use it](#use-it)
4. [How text capture & replacement work (and their limits)](#how-text-capture--replacement-work-and-their-limits)
5. [Privacy & security](#privacy--security)
6. [History](#history)
7. [Testing](#testing)
8. [Building the Windows executable](#building-the-windows-executable)
9. [Troubleshooting](#troubleshooting)
10. [Project structure](#project-structure)

---

## Install & Run

**Requirements:** Windows 10/11, Python 3.10+ (3.13 tested).

```powershell
# from the project folder
python -m pip install -r requirements.txt
python main.py
```

The app starts in the system tray. First launch opens the settings window.

**Use the packaged executable instead** (no Python needed):
grab or build `dist\QuickPromptAI.exe` — see
[Building the Windows executable](#building-the-windows-executable).

---

## Configure an AI provider

Open the tray menu → **Open QuickPrompt AI** → **Provider** tab.

| Provider | API base URL (default) | Model ID examples |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini`, `o4-mini` |
| Anthropic | `https://api.anthropic.com` | `claude-sonnet-4-20250514`, `claude-3-7-sonnet-latest`, `claude-3-5-haiku-latest` |
| Google Gemini | `https://generativelanguage.googleapis.com` | `gemini-2.0-flash`, `gemini-2.5-flash`, `gemini-2.5-pro` |
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` (default), `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, `allam-2-7b` |
| OpenAI-compatible | *(you provide it)* | whatever your gateway serves |

> **Using Groq?** Get a free key at [console.groq.com/keys](https://console.groq.com/keys),
> pick Groq in the Provider tab, paste the key, **Save Key**, then **Test Connection**.
> The base URL is prefilled. Groq's free developer tier has per-model rate limits
> (e.g. requests-per-minute caps) — if you hit a 429, wait a moment and retry.
> Note: `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` are Enterprise-tier on many
> accounts and return 404 on free developer keys — use the gpt-oss or qwen models instead.
> Model list verified 2026-10 against [Groq's docs](https://console.groq.com/docs/models).

Steps:

1. Pick the provider, paste your API key, click **Save Key**.
   The key goes straight into **Windows Credential Manager**
   (or a DPAPI-encrypted file if Credential Manager is unavailable).
2. Pick/enter the **Model ID**. For OpenAI-compatible endpoints also set the
   **API base URL** (e.g. `https://api.deepseek.com/v1`).
3. Optionally adjust timeout, maximum output tokens, temperature
   (only applied when enabled), prompt style, and max input length.
4. Click **Test Connection**, then **Save Settings**.

> **API access & billing are between you and your provider.** QuickPrompt AI
> ships with no API key and makes no purchases. Charges depend on your
> provider account and usage. Cost estimates are shown only for models with
> known list prices (editable via `price_overrides` in `settings.json`).

### API key storage details

- Primary backend: `keyring` → **Windows Credential Manager**
  (service name `QuickPromptAI`).
- Fallback: a **DPAPI**-encrypted file in `%APPDATA%\QuickPromptAI\`.
  DPAPI binds it to your Windows user account.
- Keys are **never** written to `settings.json`, logs, history, or the EXE.
  A test suite guard enforces this.

---

## Use it

### Global workflow (any application)

1. Select text in ChatGPT, Notepad, VS Code, a browser, …
2. Press **Ctrl+Shift+E** (configurable).
3. A popup opens near your cursor showing a loading state, then the captured
   text and the enhanced prompt.
4. Edit the result if you like, then:
   - **Copy** — put it on the clipboard.
   - **Replace Selected Text** — paste it over the original selection
     (only if the same window is still focused; see
     [safety notes](#how-text-capture--replacement-work-and-their-limits)).
   - **Try Again** — re-run with your edits or a different mode.
   - **Cancel** / **Esc** — dismiss (the original text is never touched).

### Alternative workflow (main window)

Tray → **Open QuickPrompt AI** → **Enhance** tab: paste/type text, choose a
mode, click **Enhance**, then **Copy Result**. This needs no global shortcut
and works even when capture is impossible in the source app.

### Enhancement modes

| Mode | What it does |
|---|---|
| **Quick Fix** | Grammar/spelling/clarity only, minimal changes, structure preserved. |
| **Prompt Enhance** | Turns rough text into a structured prompt with goal, context, output format, constraints. |
| **Advanced Optimize** | Objective / Context / Requirements / Constraints / Output Format / Success Criteria (+ verification), with coding & research task adjustments. |

**Prompt style** (Provider tab) optionally adapts formatting guidance for
ChatGPT / Claude / Gemini, or leaves it universal. This is best-effort
formatting advice — no provider is guaranteed to benefit.

### Customizing the shortcut

**Shortcut** tab → **Record** → press the new combination → **Apply Shortcut**.
Windows-reserved combinations (Win+letter, Ctrl+Alt+Del…) cannot be
registered; the app tells you and keeps the old shortcut.

### Dictation (speech-to-text)

Press **Ctrl+Shift+S** anywhere to dictate instead of type:

1. Put your caret wherever you want the words to land — Notepad, Word, VS Code,
   a browser, anywhere.
2. Press the shortcut once — recording starts **immediately** (16 kHz mono).
3. Speak your sentence.
4. Press the shortcut again — the audio is transcribed and the text is typed
   **at your caret, in that same window.**

QuickPrompt deliberately raises **no window** while dictating. Any popup would
steal the caret you are dictating into, which is the whole point of the
feature. Progress is reported through the tray icon tooltip
(`🎤 Listening…` → `Transcribing…`) and the main window's status bar, neither of
which takes focus.

If the transcript cannot be typed safely — the window closed, or you switched to
a different app while talking — QuickPrompt **refuses** rather than dumping your
words into an unrelated window, and puts the text in its own editor instead so
nothing is lost.

There is also a **🎤 Dictate** button in the **Enhance** tab that toggles the
same flow with the mouse.

- **Configurable**: **Shortcut** tab → *Dictation* row → record/enter a new
  combination → **Apply Dictation Shortcut**. It must differ from the enhance
  shortcut; the default is `Ctrl+Shift+S`.
- **Endpoint & key**: dictation is sent to your **active provider's**
  `/audio/transcriptions` route using that provider's stored API key — there is
  no separate endpoint or credential to configure. Use **OpenAI**, **Groq**, or
  an **OpenAI-compatible** endpoint that hosts Whisper (Anthropic/Gemini do not
  expose a Whisper-compatible route, and the app says so).
- **Model**: set in **Provider** tab → *Dictation (Whisper) model*. Leave it
  **empty for auto** — the provider's own Whisper model is used
  (`whisper-1` on OpenAI, `whisper-large-v3-turbo` on Groq; model IDs are not
  shared between providers). Only set this if you know your endpoint's
  transcription model ID.
- **Errors are handled**: microphone denied/unavailable, no API key, network
  failure, and provider API errors each show a specific, actionable message.
- Recording stops automatically if something goes wrong, and quitting the app
  always releases the microphone.

---

## How text capture & replacement work (and their limits)

QuickPrompt uses the classic, dependency-free Windows strategy:

1. **Capture** — snapshot the clipboard → send **Ctrl+C** to the focused
   window → poll the clipboard *sequence number* until it changes (robust
   even when the copied text is identical to the clipboard) → read the text →
   **restore your previous clipboard**.
2. **Replace** — verify the foreground window still belongs to the **same
   process** that was captured → snapshot the clipboard → put the result on
   the clipboard → send **Ctrl+V** → restore the clipboard (skipped if you
   copied something else in the meantime).

### Known limitations (by design, not bugs)

- **Elevated apps:** Windows blocks keystrokes from a non-elevated process
  into *Run as administrator* windows (UIPI). Run QuickPrompt AI as admin
  only if you must capture from elevated apps.
- **Secure input fields** (password boxes, some UAC prompts) never send
  Ctrl+C — QuickPrompt cannot and will not try to read them.
- **Remote Desktop / Citrix** sessions may not deliver synthetic keystrokes.
- **Custom-drawn editors** (terminals with clipboard overrides, some Electron
  apps in exotic states) may ignore Ctrl+C; if no selection is detected you
  get a clear "No text selection" message — use the Enhance tab instead.
- **Non-text clipboard contents** (images, file lists) cannot be preserved
  across a capture — only the clipboard *text* format is backed up.
- **Replace is best-effort and never automatic**: the target app decides what
  actually happens, so verify the result. If the original window lost focus
  or its process changed, replacement is refused and you're told to copy.
- QuickPrompt does **not** claim universal compatibility. Test your critical
  apps with the [manual checklist](docs/MANUAL_TESTS.md).

---

## Privacy & security

- Text is sent to your provider **only** when *you* press the shortcut or an
  Enhance button. There is no background monitoring.
- **No keylogging**: QuickPrompt never records keystrokes. The global hotkey
  only listens for the registered combination.
- **Microphone only while dictating**: audio is captured strictly between your
  two presses of the dictation shortcut (or the Dictate button). It is sent to
  your provider's Whisper endpoint and is never written to disk, logged, or
  added to history. The recording is held in memory only.
- **No clipboard watching**: the clipboard is read only during the
  seconds-long capture you triggered, then restored.
- Password fields/secure inputs are not readable — see limitations above.
- API keys live in Windows Credential Manager / DPAPI — never in plaintext
  config, logs, or history.
- Provider errors are scrubbed of known secrets before display.
- **History is local, optional, and clearable** (History tab). It stores the
  original and enhanced text only — no keys.

---

## History

**History** tab shows the last enhancements (time, mode, provider, model,
tokens). Double-click to inspect. **Clear History** wipes it; uncheck
*Keep local history* to disable it entirely. Stored in
`%APPDATA%\QuickPromptAI\history.json`.

---

## Testing

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

The suite covers: enhancement instructions, provider adapters & request
schemas (OpenAI/Anthropic/Gemini/compatible) with mocked HTTP, API error
handling (auth, quota, rate limit, 5xx, timeout, non-JSON), configuration
validation & persistence, shortcut parsing/registration/cleanup, clipboard
backup/restore, capture & replacement safeguards, credential storage
(Credential Manager + DPAPI round-trips), controller flows
(capture→enhance→popup→replace, cancel/stale-result discard), dictation
(microphone WAV capture, Whisper multipart request & error mapping,
record→transcribe→type pipeline, caret pasting with refusal when the user
switches windows), and offscreen UI startup. Six real-clipboard tests self-skip
in sessions where Windows denies clipboard access (they run on a normal
interactive desktop).

Also verify a packaged build at any time:

```powershell
dist\QuickPromptAI.exe --smoke-test
```

(prints `SMOKE OK` and exits 0). For **live integration checks** on your real
desktop (each opens/closes what it needs automatically):

```powershell
python scripts/live_check_notepad.py          # capture → refocus → replace round-trip
python scripts/live_check_dictation.py       # transcript typed at the caret; refusal when switched
python scripts/live_check_modifier_gating.py  # capture waits for hotkey modifiers to lift
python scripts/live_check_hotkey.py           # real Ctrl+Shift+E end-to-end (quit running instances first)
```

Manual, real-world checks: [docs/MANUAL_TESTS.md](docs/MANUAL_TESTS.md).

---

## Building the Windows executable

```powershell
build.bat
```

The script installs dev dependencies, regenerates the icon, runs the test
suite, and builds `dist\QuickPromptAI.exe` (one-file, windowed, embedded
icon). Equivalent manual command:

```powershell
python -m PyInstaller quickprompt.spec --noconfirm --clean
```

Notes:

- **No admin rights** are needed to build or run.
- `build.bat` stops any running `QuickPromptAI.exe` first — a live instance keeps
  `dist\QuickPromptAI.exe` open, which makes PyInstaller fail while rewriting it
  and silently wastes the whole build.
- One-file PyInstaller apps unpack to a temp folder at startup (≈1–2 s cold
  start). If that bothers you, switch the spec to `COLLECT` (one-folder) for
  faster starts.
- **Antivirus false positives**: heuristic engines (especially Defender on
  unsigned one-file executables that unpack themselves) sometimes flag
  PyInstaller output. This build keeps `upx=False` to reduce this. If your AV
  complains: sign the EXE (e.g. with a code-signing certificate), distribute
  one-folder builds, or add an exclusion for `dist\`. No virus total
  submission was performed for this project.
- Runtime dependencies bundled: PySide6, pynput, requests, keyring, sounddevice
  (plus their transitive deps). Python itself is embedded — end users do not
  need it.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "No text selection was found" | Nothing was selected; the app ignored every copy method (legacy consoles treat Ctrl+C as "interrupt" — Ctrl+Insert is retried automatically); the target window runs as administrator (the message then says so — run QuickPrompt elevated too, or paste into the popup); or focus changed mid-capture. QuickPrompt waits for you to release the hotkey keys before copying, so a still-held Shift can no longer turn Ctrl+C into Ctrl+Shift+C (Inspect Element in browsers). |
| "The shortcut could not be registered" | Another app or Windows owns that combo — pick a different one in the Shortcut tab. |
| Capture fails only in one app | Likely elevated (run QuickPrompt as admin), secure input, or custom clipboard handling. |
| "The provider rejected the API key" | Wrong/expired key, or the key belongs to another provider. Re-save it in Provider settings. |
| "insufficient credits / quota" | Your provider account needs credit/quota. |
| Rate limit (429) | Wait and retry; consider a higher-tier model/plan. |
| Timeout errors | Increase the timeout (Provider tab) or pick a faster model. |
| "could not find the model or endpoint" (404) | Model ID typo, wrong base URL (OpenAI-compatible), an unsupported model for your key (e.g. Groq's `llama-*` models are Enterprise-only — use `openai/gpt-oss-120b`), or a deprecated model. |
| Popup shows no result but no error | The provider returned empty content (safety block on Gemini). Try other wording. |
| Replace pasted into nothing | The selection was lost or the app uses non-standard paste. Copy + paste manually. |
| Old clipboard not restored | It held non-text content (image/files), or you copied something else mid-operation. |
| EXE blocked by antivirus | See [Building the Windows executable](#building-the-windows-executable). |
| App won't start in a restricted desktop | PyInstaller one-file needs a writable `%TEMP%`. |
| Shortcut works, but only some keys | Win-key combos are often reserved by Windows/Explorer. |
| Dictation says "no microphone found" | Connect or enable an input device in Windows sound settings. |
| Dictation says "Windows blocked microphone access" | Allow desktop apps to use the mic: Settings → Privacy → Microphone. |
| Dictation: "does not support speech-to-text" | Your active provider has no Whisper endpoint — switch to OpenAI, Groq or an OpenAI-compatible endpoint. |
| Dictation types nothing / "no speech detected" | You stopped the recording before speaking, or the audio was silent — hold the recording while you speak. |
| "You switched to another window while dictating" | Working as intended: the text is *not* typed into an unrelated window. Finish dictating without alt-tabbing, or pick up the transcript from the Enhance tab editor where it was placed instead. |
| Dictation text appeared in QuickPrompt instead of your app | The paste was refused for safety (window changed or closed). Copy it from the Enhance tab and paste manually. |
| Dictation shortcut won't register | It collides with the enhance shortcut or another app — pick a different one in the Shortcut tab. |

Log files are not written by default (privacy). Run
`dist\QuickPromptAI.exe --smoke-test` from a terminal to check startup errors.

---

## Project structure

```
quickprompt/
├── main.py                     # entry point (dev + packaged), wiring, --smoke-test
├── core/                       # Qt-free logic
│   ├── errors.py               # error taxonomy + secret scrubbing
│   ├── models.py               # shared dataclasses
│   ├── enhancement.py          # system instructions, modes, strategies
│   ├── config.py               # settings model/validation, data dir
│   └── pricing.py              # best-effort cost estimation
├── providers/                  # one adapter per API schema
│   ├── base.py                 # ProviderAdapter ABC + shared HTTP/errors
│   ├── openai_provider.py      # OpenAI + OpenAI-compatible endpoints
│   ├── anthropic_provider.py   # Claude Messages API
│   ├── gemini_provider.py      # Gemini generateContent API
│   └── registry.py
├── windows_integration/        # ctypes-only Windows plumbing
│   ├── clipboard.py            # backup/restore with sequence checks
│   ├── window_info.py          # foreground window identity (PID)
│   ├── capture.py              # Ctrl+C selection capture
│   ├── caret.py                # type dictated text at the caret (verified)
│   └── replace.py              # verified Ctrl+V replacement
├── speech/                     # dictation (speech-to-text)
│   ├── recorder.py             # microphone capture → 16 kHz WAV
│   └── dictation.py            # record → transcribe → hand text to the UI
├── shortcuts/global_hotkey.py  # parse/validate/register global hotkeys
├── security/credstore.py       # Credential Manager (keyring) + DPAPI fallback
├── storage/                    # settings.json & history.json persistence
├── ui/                         # PySide6: main window, popup, controller, tray
├── tests/                      # automated tests (pytest)
├── assets/                     # icon generator + icon.ico/icon.png
├── docs/MANUAL_TESTS.md        # manual Windows test checklist
├── requirements.txt / requirements-dev.txt
├── quickprompt.spec / build.bat
└── config.example.json         # example configuration (placeholders only)
```

## License

[MIT](LICENSE) © 2026 Abhijeet Dhotre — use, modify and redistribute freely,
including commercially. No warranty is provided.
