"""Parsing of OCR boxes into sheet fields (engine stubbed, plus real OCR output fixtures)."""
import json
from pathlib import Path

import pytest

from auction_bot.nichibo.sheet_ocr import parse_boxes

FIX = Path(__file__).parent / "fixtures"


def box(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_parse_boxes_caa_layout():
    boxes = [
        (box(10, 10, 60, 20), "90026", 0.99),
        (box(200, 10, 120, 20), "ハイエースバン", 0.9),
        (box(400, 10, 200, 20), "クルマイスシヨウシヤBタイプ 4WD", 0.8),
        (box(380, 40, 120, 20), "QDF-GDH206Kｶｲ", 0.85),
        (box(10, 60, 90, 20), "42,192 km", 0.95),
        (box(300, 100, 60, 18), "乗車定員", 0.9),
        (box(305, 120, 40, 18), "10 人", 0.95),
        (box(700, 10, 45, 20), "評価点", 0.9),
        (box(710, 32, 20, 20), "4", 0.9),
    ]
    r = parse_boxes(boxes)
    assert r.seats == 10
    assert r.model_code == "GDH206K"
    assert r.km == 42_192
    assert r.grade == "4"
    assert r.is_4wd is True


def test_parse_boxes_seats_variants():
    r = parse_boxes([(box(0, 0, 10, 10), "乗車定員１０人", 0.9)])
    assert r.seats == 10
    # Label with the value right of it on the same row, and a decoy far away.
    r = parse_boxes([(box(0, 0, 60, 18), "定員", 0.9), (box(70, 0, 30, 18), "8人", 0.9), (box(0, 300, 30, 18), "5人", 0.9)])
    assert r.seats == 8
    # Split boxes: digits directly below the (misread) label, "人" as its own box.
    r = parse_boxes([(box(383, 159, 58, 18), "秉車定負", 0.7), (box(401, 178, 28, 21), "10", 1.0), (box(421, 179, 29, 20), "人", 0.98)])
    assert r.seats == 10
    r = parse_boxes([(box(0, 0, 10, 10), "6人", 0.9)])
    assert r.seats == 6  # a lone "N人" box
    r = parse_boxes([(box(0, 0, 10, 10), "ハイエース", 0.9)])
    assert r.seats is None and r.model_code is None and r.km is None and r.grade is None


@pytest.mark.parametrize("name,seats,model,km", [
    ("ocr_boxes_d2xLUU5Idz0.json", 10, "GDH206K", 123_708),   # 2019 Welcab, CAA Tokyo
    ("ocr_boxes_Vk1Nbmd1ND0.json", 10, "GDH206K", 42_192),    # 2019 Van xWD, CAA Tokyo ("10" and "人" split)
    ("ocr_boxes_WFpsRXUxcz0.json", 9, "GDH206V", 122_482),    # 2020 DX GL Package: the 9-seater from the walkthrough
])
def test_parse_real_sheets(name, seats, model, km):
    boxes = json.loads((FIX / name).read_text(encoding="utf-8"))
    r = parse_boxes([(b, t, s) for b, t, s in boxes])
    assert r.seats == seats
    assert r.model_code == model
    assert r.km == km
    assert r.grade == "4"
    assert r.is_4wd is True
