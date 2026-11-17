"""Screenshots in the GM chat: an owner attaches a photo (a trade offer, a roster, another app's league) and asks.

The GM model sees the images themselves on the turn they're sent. Saved conversations hold text only, so a small,
cheap model (Haiku) also writes down what each screenshot shows; that reading is saved with the question and sent
again on follow-ups ("what if I add a 2nd?") instead of the image. It also lets the trade router see "should I
accept?" is about a trade when the trade is only in the picture.

Cost: a phone screenshot is about 1,500 image tokens after the app shrinks it, a fraction of a cent on either model
(ten screenshots on Sonnet is about 3 cents).
Images are never stored: only the text reading is.
"""
from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

MODEL = "claude-haiku-5-5"
MAX_IMAGES = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # the API's limit per image
MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_READING_CHARS = 8000
DEFAULT_QUESTION = "What do you make of this?"

READ_SYSTEM = """You transcribe fantasy football screenshots for a fantasy football assistant who will answer the owner's question.

Write down everything in the screenshot(s) that could matter, as compact plain text:
- the app or site if you can tell (Sleeper, ESPN, Yahoo, Fleaflicker, MFL, Underdog...), the league or team names shown
- any league settings shown (PPR, superflex, TE premium, roster slots, team count, dynasty/keeper/redraft, salary or contracts)
- a trade: exactly what each side gives (players with position and NFL team, picks with year and round, FAAB/cap), who proposed it, any status
- rosters or lineups: each player with position, NFL team, slot (starter, bench, taxi, IR) and any salary, contract or points shown
- standings, records, points, projections, rankings or values shown, with their numbers
Copy names and numbers exactly. Don't give opinions, advice or a summary of what to do. If a screenshot isn't about
fantasy football or the NFL, say so in one line. If text is too small or cut off, say which part."""


class ScreenshotError(ValueError):
    """A bad upload, with a message the owner can read."""


@dataclass(frozen=True)
class Shot:
    media_type: str
    data: str  # base64, no data: prefix

    def block(self) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": self.media_type, "data": self.data}}


@dataclass
class Reading:
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None


def clean(images: Any) -> list[Shot]:
    """Validate uploads: at most MAX_IMAGES, real base64, a supported type, under the size limit each.
    Accepts {"media_type", "data"} dicts, with or without a data: URL prefix. Raises ScreenshotError."""
    if not images:
        return []
    if not isinstance(images, (list, tuple)):
        raise ScreenshotError("Couldn't read that photo. Try attaching it again.")
    if len(images) > MAX_IMAGES:
        raise ScreenshotError(f"Attach up to {MAX_IMAGES} screenshots per question.")
    out = []
    for item in images:
        get = item.get if isinstance(item, Mapping) else (lambda k, d=None, _i=item: getattr(_i, k, d))
        data = str(get("data", "") or "").strip()
        media = str(get("media_type", "") or "").strip().lower()
        if data.startswith("data:"):
            head, _, data = data.partition(",")
            media = media or head[5:].split(";")[0].lower()
        media = "image/jpeg" if media == "image/jpg" else media
        if media not in MEDIA_TYPES:
            raise ScreenshotError("That photo type isn't supported. Use a JPEG or PNG screenshot.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ScreenshotError("Couldn't read that photo. Try attaching it again.") from None
        if not raw:
            raise ScreenshotError("That photo came through empty. Try attaching it again.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise ScreenshotError("That photo is too big. Try a regular screenshot instead of a full-size photo.")
        out.append(Shot(media, data))
    return out


LONG_EDGE = 1568  # the model downsizes anything bigger, so bigger only costs upload time


def shrink(raw: bytes, media_type: str = "image/jpeg") -> Shot:
    """An uploaded file as a Shot, scaled to LONG_EDGE and re-saved as JPEG when Pillow can read it (the website's
    uploads can be full-size phone photos). Without Pillow, or for a file it can't open, the bytes go as they are."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.thumbnail((LONG_EDGE, LONG_EDGE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        raw, media_type = out.getvalue(), "image/jpeg"
    except Exception:
        pass
    shots = clean([{"media_type": media_type, "data": base64.b64encode(raw).decode()}])
    return shots[0]


def read(shots: list[Shot], question: str = "", *, sdk_client: Any = None) -> Reading:
    """What the screenshots show, as text. Fails open: any error returns an empty reading (the GM still sees the images)."""
    if not shots:
        return Reading()
    start = time.perf_counter()
    try:
        client = sdk_client
        if client is None:
            import anthropic

            from league_ai.config import api_key

            client = anthropic.Anthropic(api_key=api_key(), timeout=30.0, max_retries=1)
        content = [s.block() for s in shots] + [{"type": "text", "text": f"The owner's question: {question.strip() or DEFAULT_QUESTION}"}]
        resp = client.messages.create(model=MODEL, max_tokens=600 + 300 * len(shots), system=READ_SYSTEM,
                                      messages=[{"role": "user", "content": content}])
        text = "".join(getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text").strip()
        u = getattr(resp, "usage", None)
        get = (lambda k: int(getattr(u, k, 0) or 0)) if u is not None else (lambda k: 0)
        usage = {"input_tokens": get("input_tokens"), "output_tokens": get("output_tokens"),
                 "cache_read_tokens": get("cache_read_input_tokens"), "cache_write_tokens": get("cache_creation_input_tokens")}
        return Reading(text[:MAX_READING_CHARS], usage, int((time.perf_counter() - start) * 1000))
    except Exception as exc:
        return Reading(error=type(exc).__name__, latency_ms=int((time.perf_counter() - start) * 1000))


def label(count: int) -> str:
    return "a screenshot" if count == 1 else f"{count} screenshots"


def question_text(question: str, count: int) -> str:
    """The question as the GM gets it on the turn the images are attached."""
    q = question.strip() or DEFAULT_QUESTION
    return f"(I attached {label(count)} above.)\n\n{q}"


def history_note(reading: str, count: int) -> str:
    """How an earlier screenshot comes back on a follow-up, where only its text reading is kept."""
    body = reading.strip() or "(it couldn't be read back; ask the owner to send it again if you need the details)"
    return f"(Earlier in this chat I sent {label(count)}. What it showed:\n{body})"


def routing_text(question: str, reading: str) -> str:
    """Question plus reading, for the trade router and effort pick (a bare 'should I accept?' is a trade question)."""
    return f"{question}\n{reading}" if reading else question
