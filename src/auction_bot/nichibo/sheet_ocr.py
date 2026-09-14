"""Read the fixed fields of a Japanese auction sheet with a local OCR engine (no API key).

Uses RapidOCR (ONNX runtime, CPU). If the engine cannot load, every field is None and the
reviewer reads the sheet as before. Only printed fields are read: seating capacity
(乗車定員 "10 人"), model code (型式 "QDF-GDH206K"), odometer, grade and the 4WD mark.
Handwritten inspector notes are not attempted.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_engine = None
_engine_error: str | None = None


@dataclass
class SheetRead:
    available: bool
    seats: int | None = None
    model_code: str | None = None
    km: int | None = None
    grade: str | None = None
    is_4wd: bool | None = None
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    engine: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def engine_status() -> tuple[bool, str]:
    """(available, detail). Loads the OCR engine once."""
    global _engine, _engine_error
    if _engine is not None:
        return True, "rapidocr"
    if _engine_error is not None:
        return False, _engine_error
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _engine = RapidOCR()
        return True, "rapidocr"
    except Exception as exc:  # ImportError or DLL load failure
        msg = str(exc).splitlines()[0][:160]
        if "specified module could not be found" in msg or "DLL" in msg:
            msg += " (install the Microsoft Visual C++ 2015-2022 x64 Redistributable)"
        _engine_error = msg
        return False, msg


def _run_engine(path: Path) -> list[tuple[list, str, float]]:
    ok, _ = engine_status()
    if not ok:
        raise RuntimeError(_engine_error or "OCR engine unavailable")
    result, _elapse = _engine(str(path))
    return [(box, str(text), float(score)) for box, text, score in (result or [])]


# ---- parsing (pure, unit-tested with stubbed engine output) -----------------

_FULLWIDTH = str.maketrans("０１２３４５６７８９，．－", "0123456789,.-")


def _norm(text: str) -> str:
    return text.translate(_FULLWIDTH).replace(" ", "").replace("　", "")


def parse_boxes(boxes: list[tuple[list, str, float]]) -> SheetRead:
    """Extract the fields from OCR boxes [(polygon, text, score), ...]."""
    read = SheetRead(available=True)
    items = []
    for box, text, score in boxes:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({"text": text, "norm": _norm(text), "x": min(xs), "y": (min(ys) + max(ys)) / 2,
                      "cx": (min(xs) + max(xs)) / 2, "w": max(xs) - min(xs),
                      "h": max(ys) - min(ys), "score": score})
    read.lines = [i["text"] for i in items]
    blob = "|".join(i["norm"] for i in items)

    # Seats. The label 乗車定員 is often misread (秉車定負, 乗車定负 ...); the value sits in the
    # cell right below it ("10人", or "10" and "人" as two boxes), sometimes on the same row.
    m = re.search(r"[乗秉][車車]?定[員負负][^\d|]{0,4}(\d{1,2})人?", blob)
    if m:
        read.seats = int(m.group(1))
    else:
        label = next((i for i in items if re.search(r"[乗秉][車車]?定[員負负]|定員", i["norm"])), None)
        numeric = [i for i in items if re.fullmatch(r"(\d{1,2})人?", i["norm"])]
        pick = _value_near_label(label, numeric) if label is not None else None
        if pick is None:
            lone = [i for i in items if re.fullmatch(r"(\d{1,2})人", i["norm"])]
            pick = lone[0] if len(lone) == 1 else None
        if pick is not None:
            read.seats = int(re.match(r"(\d{1,2})", pick["norm"]).group(1))
    if read.seats is not None and not (1 <= read.seats <= 30):
        read.seats = None

    # Model code: "QDF-GDH206K", "3BA-AGH30W", "DBA-ANH20W" (a katakana 改 suffix is dropped).
    codes = re.findall(r"(?:[A-Z0-9]{2,4}-)?([A-Z]{2,4}\d{2,3}[A-Z]{0,2})(?=[^A-Z0-9]|$)", blob)
    if codes:
        read.model_code = codes[0]

    # Odometer: "42,192km" / "42.192km" (OCR reads the comma as a dot) / "42192 km".
    m = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d{4,6})(?:km|㎞)", blob, re.I)
    if m:
        read.km = int(re.sub(r"[.,]", "", m.group(1)))

    # Grade: the value cell right below the 評価点 label (often misread as 评洒点 / 評洒点).
    glabel = next((i for i in items if re.search(r"[評评][価洒价]点", i["norm"])), None)
    gvals = [i for i in items if re.fullmatch(r"(R|RA|[0-6](?:\.5)?)", i["norm"])]
    gpick = _value_near_label(glabel, gvals) if glabel is not None else None
    if gpick is not None:
        read.grade = gpick["norm"]
    read.is_4wd = True if re.search(r"4WD|4\s*WD|四駆", blob, re.I) else None
    return read


def _value_near_label(label: dict, cands: list[dict]) -> dict | None:
    """The candidate box in the cell below the label (or right of it on the same row)."""
    if not cands:
        return None
    lw = max(label.get("w", 40), 40)
    lh = max(label["h"], 12)
    below = [c for c in cands
             if abs(c["cx"] - label["cx"]) <= lw * 0.9 and -lh * 0.5 <= (c["y"] - label["y"]) <= lh * 3.0]
    if below:
        return min(below, key=lambda c: abs(c["y"] - label["y"]) + abs(c["cx"] - label["cx"]) / 10)
    same_row = [c for c in cands if abs(c["y"] - label["y"]) <= lh * 1.2 and 0 <= c["x"] - label["x"] <= lw * 3]
    if same_row:
        return min(same_row, key=lambda c: c["x"] - label["x"])
    return None


def read_sheet(path: Path) -> SheetRead:
    ok, detail = engine_status()
    if not ok:
        return SheetRead(available=False, error=detail)
    try:
        boxes = _run_engine(path)
    except Exception as exc:
        return SheetRead(available=False, error=str(exc)[:160])
    read = parse_boxes(boxes)
    read.engine = detail
    return read
