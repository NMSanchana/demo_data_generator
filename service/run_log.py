"""
Run log: the readable story of one /generate call, kept per screen.

Each entry is {t, level, screen, message}:
  t       seconds since the run started
  level   "info" | "warning" | "error"
  screen  the screen the line is about, or None for run-wide lines

The log is returned with the /generate response and saved with the run (dd_runs.params.logs), so it can be
shown after the run and read back later from the downloaded bundle.
"""

import time

MAX_MESSAGE_CHARS = 600


def plain_error(raw: object) -> str:
    """Turn a raw exception / error string into a sentence a person can act on. The original text is kept at the end."""
    text = str(raw or "").strip() or "Unknown error."
    low = text.lower()
    if "agent returned no rows" in low:
        hint = "The AI answered but returned no rows for this screen. Try generating it again."
    elif "no fields available" in low or "no form fields found" in low:
        hint = "This screen has no usable fields, so there is nothing to generate."
    elif "api key" in low or "incorrect api key" in low or "401" in low and "openai" in low:
        hint = "The AI service rejected the key. Check OPENAI_API_KEY in the backend .env."
    elif "rate limit" in low or "429" in low:
        hint = "The AI service is rate limiting requests. Wait a moment and generate again, or lower the rows per screen."
    elif "timeout" in low or "timed out" in low:
        hint = "The AI service took too long to answer. Try again, or lower the rows per screen."
    elif "json" in low and ("decode" in low or "expecting" in low or "unterminated" in low):
        hint = "The AI answer was cut off or malformed. Lower the rows per screen (or ROWS_PER_LLM_CALL) and try again."
    elif "connect" in low or "unreachable" in low:
        hint = "A service could not be reached. Check that it is running and its address in .env is right."
    elif "not a screen in the kms" in low:
        hint = "KMS does not know this screen. Pick it from the Features list so its screen id is used."
    elif "report screen" in low:
        hint = "This is a report screen. Reports hold no data to generate."
    else:
        hint = ""
    return f"{hint} ({text})" if hint else text


class RunLog:
    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.entries: list[dict] = []

    def add(self, level: str, message: str, screen: str | None = None) -> None:
        msg = " ".join(str(message).split())
        if len(msg) > MAX_MESSAGE_CHARS:
            msg = msg[: MAX_MESSAGE_CHARS - 1] + "…"
        self.entries.append({
            "t": round(time.monotonic() - self._t0, 1),
            "level": level,
            "screen": screen,
            "message": msg,
        })

    def info(self, message: str, screen: str | None = None) -> None:
        self.add("info", message, screen)

    def warning(self, message: str, screen: str | None = None) -> None:
        self.add("warning", message, screen)

    def error(self, message: str, screen: str | None = None) -> None:
        self.add("error", message, screen)

    def counts(self) -> dict[str, int]:
        out = {"info": 0, "warning": 0, "error": 0}
        for e in self.entries:
            out[e["level"]] = out.get(e["level"], 0) + 1
        return out