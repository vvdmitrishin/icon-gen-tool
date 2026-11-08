import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings

FAKE_KEY = "AIzaSy-FAKE-TEST-KEY-1234567890"


def png_bytes(size=(64, 48), color=(200, 30, 30), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        gemini_api_key=FAKE_KEY,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "data" / "artifacts",
        job_budget_usd=0.75,
        total_budget_usd=2.00,
    )


def gemini_ok_response(image=None, text="Here is your image.", usage=None, fmt="PNG"):
    image = image or png_bytes(fmt=fmt)
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": text},
                        {"inlineData": {"mimeType": "image/png",
                                        "data": base64.b64encode(image).decode()}},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": usage or {"promptTokenCount": 120, "candidatesTokenCount": 1290},
    }


class Recorder:
    """httpx MockTransport that records requests and replays a scripted response."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else gemini_ok_response()
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def last_json(self):
        return json.loads(self.requests[-1].content)


def art_png(size: int = 512, seed: int = 0) -> bytes:
    """A non-blank, seed-distinct test image (gradient + ellipse) so QA and duplicate checks are realistic."""
    from PIL import ImageDraw

    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    c1 = ((seed * 53) % 200 + 30, (seed * 97) % 200 + 30, (seed * 31) % 200 + 30)
    c2 = (255 - c1[0], 255 - c1[1], 255 - c1[2])
    for y in range(size):
        t = y / size
        d.line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    d.ellipse([size // 4, size // 4, 3 * size // 4, 3 * size // 4], fill=(250, 250, 250), outline=(20, 20, 20), width=5)
    rng = __import__("random").Random(seed)  # speckle so the PNG is realistically large (>5 KB)
    for _ in range(size * 6):
        x, y = rng.randrange(size), rng.randrange(size)
        d.point((x, y), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ArtRecorder(Recorder):
    """Like Recorder but every call returns a different valid image; `fail` switches to a 500."""

    def __init__(self):
        super().__init__()
        self.fail = False

    def _handle(self, request):
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "backend overloaded"}})
        return httpx.Response(200, json=gemini_ok_response(image=art_png(seed=len(self.requests))))


# ---------------------------------------------------------------------------------------------------------------------
# Deterministic planner fixture. The production app has ONE planner (the AI text planner); there are no curated pools.
# Tests that are not about planning still need plans, so every test gets a deterministic stand-in for the LLM call unless
# its Gemini transport explicitly serves text plans (`transport.serves_text_plans = True`, see TextRecorder).
# ---------------------------------------------------------------------------------------------------------------------
_ADJ = ("bright", "quiet", "tiny", "grand", "golden", "frosty", "rusty", "velvet", "wild", "polished", "ancient", "cheerful")
_NOUN = (
    "lantern anchor compass kettle violin telescope saddle thimble bucket feather mirror harp pendulum satchel trowel "
    "lighthouse acorn barrel canoe drum easel flute goblet hammock ivy jar kite ladder mitten nest oar parasol quill "
    "radio scarf teapot umbrella vase whistle yarn zipper abacus beacon clock dagger engine fiddle globe helmet inkwell "
    "jewel key locket magnet needle orb pillow quiver ribbon spyglass tambourine urn vessel wagon xylophone yoyo zither "
    "balloon cannon dice envelope fan gramophone hourglass iron jug kayak lamp mask net oven pitcher quilt rake sled "
    "tent unicycle vest windmill crate brush cart dish flag gate hook"
).split()


def deterministic_plan(prompt: str) -> dict:
    """Build a valid structured plan from the planner prompt (variant count, category slots, history) without any LLM."""
    import re
    import zlib

    variants = int(re.search(r"Create (\d+) DISTINCT", prompt).group(1))
    slots = [int(x) for x in re.search(r"category slots: \[([0-9, ]+)\]", prompt).group(1).split(",")]
    set_name = (re.search(r"semantic boundary\): '(.*?)'\n", prompt) or re.search(r"SET .*?: '(.*?)'", prompt)).group(1)
    history = prompt.split("ALREADY GENERATED", 1)[1] if "ALREADY GENERATED" in prompt else ""
    k = zlib.crc32(set_name.encode()) % len(_NOUN)
    out = []
    for vi in range(variants):
        cards = []
        for pos, cat in enumerate(slots, 1):
            while True:
                name = f"{_ADJ[k % len(_ADJ)].capitalize()} {_NOUN[k % len(_NOUN)].capitalize()}"
                k += 1
                if name not in history:
                    break
            card = dict(position=pos, category=cat, object_name=name,
                        object_description=f"a vivid {name.lower()} for the {set_name} set",
                        rationale=f"clearly part of {set_name}")
            if cat == 5:
                card["characters"] = 1 + pos % 2
            cards.append(card)
        out.append(dict(title=f"{set_name} angle {vi + 1}", concept=f"A distinct visual angle {vi + 1} on {set_name}", cards=cards))
    return {"variants": out}


@pytest.fixture(autouse=True)
def _deterministic_text_planner(monkeypatch):
    from app.services.text_planner import TextPlanner

    original = TextPlanner._call

    def fake_call(self, s, model, prompt, seed):
        if getattr(getattr(self.registry, "_gemini_transport", None), "serves_text_plans", False):
            return original(self, s, model, prompt, seed)  # a test that scripts the real text transport
        return deterministic_plan(prompt)

    monkeypatch.setattr(TextPlanner, "_call", fake_call)


def make_plan(variants: int, counts: dict, set_name: str = "Detective", collection: str = "Movies"):
    """A validated SetPlan from the deterministic fixture (what the AI planner would return), for planner/validator unit tests."""
    from app.services.text_planner import TextPlanner

    prompt = TextPlanner.build_prompt(collection, set_name, variants, counts, "")
    return TextPlanner._build(deterministic_plan(prompt), set_name, variants, counts, None, "fixture-model")


def plan_cost(variants: int, cards: int) -> float:
    """Expected local estimate of one AI planning call with the default text model (gemini-3.5-flash-lite pricing)."""
    from app.services.text_planner import estimate_plan_cost_usd
    return estimate_plan_cost_usd("gemini-3.5-flash-lite", variants, cards)
