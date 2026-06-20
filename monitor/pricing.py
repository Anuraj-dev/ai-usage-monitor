"""USD pricing per 1,000,000 tokens, matched by model-name substring.

Tuple order: (input, output, cache_write, cache_read).
Longest matching key wins. Unknown / free models cost $0.
Edit these to correct any rate; new events recompute on the next poll.
"""

# per 1M tokens, USD
RATES = {
    # --- Anthropic / Claude Code ---
    "claude-opus":     (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet":   (3.00,  15.00, 3.75,  0.30),
    "claude-haiku":    (0.80,  4.00,  1.00,  0.08),
    "claude-fable":    (3.00,  15.00, 3.75,  0.30),
    # --- OpenAI / Codex ---
    "gpt-5-codex":     (1.25,  10.00, 1.25,  0.125),
    "gpt-5-mini":      (0.25,  2.00,  0.25,  0.025),
    "gpt-5":           (1.25,  10.00, 1.25,  0.125),
    "gpt-4.1-mini":    (0.40,  1.60,  0.40,  0.10),
    "gpt-4.1":         (2.00,  8.00,  2.00,  0.50),
    "o4-mini":         (1.10,  4.40,  1.10,  0.275),
    "o3":              (2.00,  8.00,  2.00,  0.50),
    # --- Google / Gemini ---
    "gemini-2.5-pro":  (1.25,  10.00, 1.25,  0.31),
    "gemini-2.5-flash":(0.30,  2.50,  0.30,  0.075),
    "gemini":          (0.30,  2.50,  0.30,  0.075),
}

_DEFAULT = (0.0, 0.0, 0.0, 0.0)


def _rate(model: str):
    m = (model or "").lower()
    best = None
    for key, rate in RATES.items():
        if key in m and (best is None or len(key) > len(best[0])):
            best = (key, rate)
    return best[1] if best else _DEFAULT


def cost_for(model, new_input=0, output=0, cache_write=0, cache_read=0):
    """USD cost of one call. `new_input` = uncached prompt tokens."""
    pin, pout, pcw, pcr = _rate(model)
    return (
        new_input * pin
        + output * pout
        + cache_write * pcw
        + cache_read * pcr
    ) / 1_000_000.0
