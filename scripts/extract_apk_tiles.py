"""Extract daily challenge sprites from a Cocos Creator APK without executing it."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
SPRITE_PATH = re.compile(r"^block/daily/(block_\d+|block_bg(?:_change|_gold|_top)?|blackMask)$")


def decode_uuid(value: str) -> str:
    """Expand Cocos Creator's 22-character UUID representation."""
    if len(value) == 36:
        return value
    if len(value) != 22:
        raise ValueError(f"Unsupported Cocos UUID: {value}")
    digits = value[:2] + "".join(
        f"{ALPHABET.index(value[i]) * 64 + ALPHABET.index(value[i + 1]):03x}"
        for i in range(2, 22, 2)
    )
    return f"{digits[:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:]}"


def archive_path(kind: str, uuid: str, suffix: str) -> str:
    return f"assets/assets/resources/{kind}/{uuid[:2]}/{uuid}{suffix}"


def extract(apk: Path, output: Path, *, max_type_id: int = 16) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    skipped: list[dict] = []
    with zipfile.ZipFile(apk) as zf:
        config = json.loads(zf.read("assets/assets/resources/config.json"))
        entries = [
            (key, entry) for key, entry in config["paths"].items()
            if SPRITE_PATH.fullmatch(entry[0]) and config["types"][entry[1]] == "cc.SpriteFrame"
            and (not re.search(r"block_(\d+)$", entry[0])
                 or int(re.search(r"block_(\d+)$", entry[0]).group(1)) <= max_type_id)
        ]
        for key, entry in sorted(entries, key=lambda pair: pair[1][0]):
            logical = entry[0]
            sprite_uuid = decode_uuid(config["uuids"][int(key)])
            sprite_path = archive_path("import", sprite_uuid, ".json")
            try:
                sprite = json.loads(zf.read(sprite_path))
                texture_uuid = decode_uuid(sprite[1][0])
                texture_path = archive_path("native", texture_uuid, ".png")
                texture = Image.open(io.BytesIO(zf.read(texture_path))).convert("RGBA")
                frame = sprite[5][0]
                left, top, width, height = frame["rect"]
                if not (width > 0 and height > 0 and left >= 0 and top >= 0 and
                        left + width <= texture.width and top + height <= texture.height):
                    raise ValueError("Sprite rect is outside texture")
                crop = texture.crop((left, top, left + width, top + height))
                filename = logical.rsplit("/", 1)[-1] + ".png"
                crop.save(output / filename)
                records.append({
                    "logical_path": logical,
                    "type_id": logical.rsplit("_", 1)[-1] if re.search(r"block_\d+$", logical) else None,
                    "file": filename,
                    "sprite_uuid": sprite_uuid,
                    "sprite_record": sprite_path,
                    "texture_uuid": texture_uuid,
                    "texture_record": texture_path,
                    "rect": [left, top, width, height],
                    "original_size": frame.get("originalSize"),
                    "offset": frame.get("offset", [0, 0]),
                })
            except (KeyError, IndexError, ValueError, OSError, zipfile.BadZipFile) as exc:
                skipped.append({"logical_path": logical, "reason": str(exc)})
    with apk.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {"apk": str(apk), "apk_sha256": digest, "max_type_id": max_type_id,
                "records": records, "skipped": skipped}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("--output", type=Path, default=Path("assets/tiles/classic"))
    parser.add_argument("--max-type-id", type=int, default=16)
    args = parser.parse_args()
    result = extract(args.apk, args.output, max_type_id=args.max_type_id)
    print(f"extracted={len(result['records'])} skipped={len(result['skipped'])} output={args.output}")


if __name__ == "__main__":
    main()
