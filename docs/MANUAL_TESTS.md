# QuickPrompt AI — Manual Windows Test Checklist

Run this checklist on a real desktop session after automated tests pass.
Each item states the steps, the expected result, and where it applies.

## A. Startup & tray

| # | Steps | Expected |
|---|---|---|
| A1 | `python main.py` (dev) | Tray icon appears; settings window opens on first run. No terminal window when using the EXE. |
| A2 | `dist\QuickPromptAI.exe` | Same behavior without Python installed. |
| A3 | `dist\QuickPromptAI.exe --smoke-test` | Prints `SMOKE OK`, exit code 0. |
| A4 | Launch a second instance | Second instance exits immediately (single-instance guard). |
| A5 | Close the main window | App keeps running in tray; reopen via tray menu. |
| A6 | Tray → Quit | Process exits fully (check Task Manager); hotkey stops working. |

## B. Global shortcut & capture

| # | Steps | Expected |
|---|---|---|
| B1 | Notepad → type text → select → Ctrl+Shift+E | Popup opens near cursor with the captured text inside, then shows "Enhancing…", then result. |
| B2 | Same in VS Code, Chrome (ChatGPT page), Word | Works in all standard editors. |
| B3 | Select nothing, press shortcut | Popup shows "No text selection…" with paste hint; no crash. |
| B4 | Copy something first, then capture text | After capture, pasting elsewhere gives your *original* clipboard content back (restore works). |
| B5 | Select text identical to current clipboard, press shortcut | Capture still detected (sequence-number check). |
| B6 | Shortcut tab → Record → Ctrl+Alt+T → Apply | Old shortcut stops, new one works; persisted after restart. |
| B7 | Apply a reserved combo (e.g. Win+E) | Clear error; previous shortcut still active. |
| B8 | Press shortcut while a password field has focus | Nothing sensitive captured; empty-selection message. |
| B9 | Press shortcut in a browser (Chrome/Brave/Edge) with text selected | Captures correctly even though your fingers are still on Ctrl+Shift when it fires (a held Shift used to turn the copy into Ctrl+Shift+C = "Inspect Element"). |
| B10 | Select text in a window running as administrator, press shortcut | Message explicitly explains the admin-app restriction (not the generic one). |

## B2. Dictation (speech-to-text)

| # | Steps | Expected |
|---|---|---|
| S1 | Press Ctrl+Shift+Q, speak a sentence, press again | Recording starts instantly; transcript is inserted at the cursor in the Enhance tab typing area. |
| S2 | Dictate with the popup open | Transcript lands in the popup's input box at the caret. |
| S3 | Dictate with existing text, caret mid-sentence | Text inserted exactly at the caret; surrounding text intact; a space separates words. |
| S4 | Press shortcut, stop immediately without speaking | Friendly "no audio captured" message; nothing inserted. |
| S5 | Deny microphone access in Windows privacy settings | Clear "Windows blocked microphone access" message with the Settings path; the dictation pill flashes its red error state and hides itself after ~2 s. |
| S6 | Unplug/disable the mic, then dictate | "No microphone found" message; recording state resets; pill shows the red error state briefly, then hides. |
| S7 | Disconnect network, then dictate | Friendly network error; app stays responsive; next dictation works. |
| S8 | Use a provider without a Whisper endpoint (e.g. Anthropic) | Clear message that the provider does not support speech-to-text. |
| S9 | Shortcut tab → Dictation row → record Ctrl+Alt+D → Apply | New dictation shortcut works; persisted after restart; enhance shortcut unaffected. |
| S10 | Set the dictation shortcut equal to the enhance shortcut | Rejected with a clear message; old dictation shortcut stays active. |
| S11 | Speak for 2+ minutes | Recording/transcription completes or stops gracefully; no crash, mic released. |
| S12 | Quit the app while recording | Process exits; microphone is released (check Sound settings); the pill is gone. |

### B3. Dictation overlay pill (Wispr Flow-style)

| # | Steps | Expected |
|---|---|---|
| O1 | Idle (app in tray, nothing running) | No pill visible; Task Manager shows no idle CPU from QuickPrompt AI. |
| O2 | Notepad focused → Ctrl+Shift+Q first press | Icon-only dark frosted-glass pill appears bottom-centre of the monitor under the mouse, just above the taskbar, on top of Notepad, with a centred soft white waveform. **No text on the pill. Notepad keeps focus** (keep typing while it shows). |
| O3 | Speak louder / quieter | Bars grow taller / shrink (6–26 px range); rim glow brightens with volume. No bars wiggle from a timer when the mic is closed. |
| O4 | Caret near the bottom-centre of the screen, then dictate | Pill shifts above (or below) the caret instead of covering the text cursor. |
| O5 | Ctrl+Shift+Q second press | Pill shrinks slightly, border tinted blue, 3 centred pulsing dots (no text) while the provider runs. |
| O6 | Transcript typed successfully | Green border + centred green checkmark only; fades away after ~1.2 s; nothing stays on screen afterwards. |
| O7 | Start recording in Notepad, switch to another window mid-recording, let it stop | Typing is still **refused** exactly as before ("You switched to another window…"); pill shows a centred red "!" badge only (no text — the full reason appears in the app window), shakes, hides after ~2 s; the transcript waits in the Enhance tab. Nothing is ever typed into the wrong app. |
| O8 | Network cable pulled → dictate | Red "!" badge pill (icon only); hides itself; the full error appears in the app window; app recovers. |
| O9 | Click on the pill while recording | Nothing happens, focus stays in Notepad (the pill is click-dead and unactivated). |
| O10 | Ctrl+Shift+E (enhance popup) normally | Prompt-enhance popup behaves exactly as before — the dictation pill must not appear for enhancement. |
| O11 | Hide/deactivate a monitor while dictating, or run on two monitors | Pill stays on the monitor where the mouse is; never off-screen; no crash. |
| O12 | Packaged exe: `dist\QuickPromptAI.exe --smoke-test` then dictate | `SMOKE OK`; pill behaves as O2–O6. |


## C. Enhancement

| # | Steps | Expected |
|---|---|---|
| C1 | Each mode (Quick Fix / Prompt Enhance / Advanced Optimize) on a rough sentence | Mode-appropriate output; intent preserved; editable in popup. |
| C2 | Enhance tab manual workflow | Same results without the global shortcut. |
| C3 | Try Again after editing original text | New result reflects the edit. |
| C4 | Enter / Ctrl+Enter in popup | Re-runs enhancement. Esc closes popup. |
| C5 | Extremely long input (> max input chars) | Clear "too long" error, no request sent. |

## D. Provider configuration

| # | Steps | Expected |
|---|---|---|
| D1 | Save API key for each provider | "✓ Key stored" label; Credential Manager contains a `QuickPromptAI` entry. |
| D2 | Test Connection (valid key) | Success message with model. |
| D3 | Test Connection (invalid key) | Friendly auth error; key never shown in the message. |
| D4 | OpenAI-compatible with base URL `https://api.deepseek.com/v1` (or your gateway) | Enhancements work through the gateway. |
| D5 | Restart app | Settings, key, shortcut all restored. |
| D6 | Remove stored key, then Enhance | "No API key is configured…" guidance. |

## E. Replace workflow

| # | Steps | Expected |
|---|---|---|
| E1 | Notepad: select text → enhance → Replace Selected Text | Selection replaced with the result; old clipboard restored. |
| E2 | After popup opens, click into a *different* app, then Replace | Refused with "original window is no longer active" + copy guidance; other app untouched. |
| E3 | Replace with an emptied result box | Error "result is empty", nothing pasted. |
| E4 | Replace in Word / browser textarea | Pasted correctly (verify in target app). |

## F. Privacy & history

| # | Steps | Expected |
|---|---|---|
| F1 | Enhance something → History tab | Entry appears with time/mode/provider/tokens. |
| F2 | Double-click entry | Details shown; "Copy Enhanced Text" works. |
| F3 | Disable history → enhance | No new entry. |
| F4 | Clear History | List empties; `history.json` removed. |
| F5 | Inspect `%APPDATA%\QuickPromptAI\settings.json` | No API key anywhere in the file. |

## G. Robustness

| # | Steps | Expected |
|---|---|---|
| G1 | Disconnect network → Enhance | Friendly network error + retry available. |
| G2 | Airplane mode + Test Connection | Timeout/connection message, app stays responsive. |
| G3 | Kill Wi-Fi mid-request | Timeout error after configured timeout; popup recovers. |
| G4 | Minimize all windows, press shortcut on desktop | No crash; empty-selection or capture of desktop selection. |
| G5 | Two monitors: cursor on second monitor | Popup opens near cursor, fully visible, on the correct monitor. |

## H. Packaging

| # | Steps | Expected |
|---|---|---|
| H1 | Copy EXE to a machine without Python | Runs normally. |
| H2 | Run from a path with spaces | Runs normally. |
| H3 | First cold start timing | ~1–3 s (one-file unpack), then responsive. |
| H4 | SmartScreen/AV scan of the EXE | Document any false positive in the release notes. |
