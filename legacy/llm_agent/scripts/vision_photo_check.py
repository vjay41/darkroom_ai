"""Vision check on a test photo: the local model's brightness statement vs measured numbers.

Lightroom: looks the photo up and exports a 1024 px preview (no edits, no new virtual copy).
The preview goes ONLY to the local model (loopback). No file name, metadata or measurement is
sent. Prints the model's five answer fields and the numbers; no image is displayed.

Usage:  poetry run python scripts/vision_photo_check.py --photo PATH [--profile NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.llm.tool_modes import find_json_object
from darkroom_ai.llm.vision import prepare_image
from darkroom_ai.net.loopback import LocalHttp
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

FIELDS = (
    "brightness",
    "mean_brightness_0_to_255",
    "highlights_clipped",
    "shadows_clipped",
    "color_cast",
)
PROMPT = (
    "Look at the photo. Answer with ONE JSON object and nothing else: "
    '{"brightness": "very dark | dark | balanced | bright | very bright", '
    '"mean_brightness_0_to_255": 0, "highlights_clipped": false, "shadows_clipped": false, '
    '"color_cast": "none | warm | cool | green | magenta"}. Do not describe what the photo shows.'
)
CLIPPED_PCT = 1.0  # same threshold the playbooks use


def brightness_word(mean: float) -> str:
    for limit, word in ((50, "very dark"), (95, "dark"), (160, "balanced"), (205, "bright")):
        if mean < limit:
            return word
    return "very bright"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    settings = load_config(None)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    profile = settings.llm.profiles[args.profile or settings.llm.active_profile]

    try:
        context = build_tool_context(settings)
        photo = context.lightroom.find_photo(str(args.photo))
        if photo is None:
            raise SystemExit(f"{args.photo} is not in the open catalog.")
        result = ToolRegistry(context).call("analyze_photo", {"photo_id": photo.id})
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        return 2

    analysis: dict[str, Any] = result["analysis"]
    tones = {k: v for k, v in analysis["tones"].items() if k != "histogram"}
    mean = float(tones["mean"])
    mean_255 = mean * 255 if mean <= 1.0 else mean
    print("== MEASURED (from the same preview) ==")
    print("analysed photo id is the master:", result["photo_id"] == photo.id)
    print("size:", analysis["width"], "x", analysis["height"])
    print("tones:", json.dumps(tones))
    print("clipping:", json.dumps(analysis["clipping"]))
    print("color cast:", json.dumps(analysis["color_cast"]))

    image = prepare_image(Path(result["preview_path"]), min(1024, profile.max_image_px))
    payload = {
        "model": profile.model,
        "messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT}, image.part()]}],
        "temperature": profile.temperature,
        "stream": False,
        "max_tokens": 1500,
    }
    print("== WILL SEND ==")
    print("endpoint:", profile.base_url.rstrip("/") + "/chat/completions")
    print("model:", profile.model, "| temperature:", profile.temperature)
    print("image:", image.describe(), "| request bytes:", len(json.dumps(payload)))
    print("prompt:", PROMPT)

    http = LocalHttp(profile.base_url, timeout_s=profile.timeout_s)
    started = time.monotonic()
    try:
        status, data = http.request_json("POST", "chat/completions", payload)
    except OSError as exc:
        print(f"MODEL ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        http.close()
    seconds = time.monotonic() - started
    if status != 200 or not isinstance(data, dict):
        print(f"MODEL ERROR: HTTP {status}: {str(data)[:300]}", file=sys.stderr)
        return 2
    message = data["choices"][0]["message"]
    usage = data.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    try:
        answer = find_json_object(str(message.get("content") or ""))
    except DarkroomError:
        answer = {}
    shown = {key: answer.get(key) for key in FIELDS}  # only these fields are printed
    print("== MODEL ==")
    print("seconds:", round(seconds, 2), "| finish:", data["choices"][0].get("finish_reason"))
    print(
        "tokens: prompt",
        usage.get("prompt_tokens"),
        "| completion",
        usage.get("completion_tokens"),
        "| of which thinking",
        reasoning,
    )
    print("answer:", json.dumps(shown))
    print("extra fields in the answer (names only):", sorted(set(answer) - set(FIELDS)))

    clipping = analysis["clipping"]
    print("== COMPARISON ==")
    print(
        f"brightness: model {shown['brightness']!r} vs measured {brightness_word(mean_255)!r} "
        f"(mean {mean_255:.1f})"
    )
    print(f"mean: model {shown['mean_brightness_0_to_255']} vs measured {mean_255:.1f}")
    print(
        f"highlights clipped: model {shown['highlights_clipped']} vs measured "
        f"{clipping['highlights_pct'] >= CLIPPED_PCT} ({clipping['highlights_pct']} %)"
    )
    print(
        f"shadows clipped: model {shown['shadows_clipped']} vs measured "
        f"{clipping['shadows_pct'] >= CLIPPED_PCT} ({clipping['shadows_pct']} %)"
    )
    print(
        f"color cast: model {shown['color_cast']!r} vs measured "
        f"{analysis['color_cast']['description']!r}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
