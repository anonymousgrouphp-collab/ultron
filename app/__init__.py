"""app/ — the application layer extracted from the main.py god module (Phase W0).

Composition plan (ROADMAP §9.3 W0 — main.py must SHRINK as the product
grows; extraction is incremental and every step keeps the boot smoke test
green):

- audio.py      mic capture / Gemini response pump / speaker playback
- handlers.py    the 20 legacy `_handle_*` bridges (action functions →
                 kernel-executed tools)
- briefing.py    the startup greeting + proactive check-in prompts
- monitors.py    the emergency system monitor + dashboard command pump
"""
