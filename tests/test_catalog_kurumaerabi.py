from pathlib import Path
from auction_bot.catalog.kurumaerabi import Grade, bare_code, dd, mark_state, parse_grade_page, parse_type_page, parse_type_sitemap, ym

FIX = Path(__file__).parent / "fixtures" / "catalog"
BASE = "https://www.kurumaerabi.com"

def test_sitemap_maps_bare_codes_to_pages():
    m = parse_type_sitemap((FIX / "type_sitemap_slice.xml").read_text(encoding="utf-8"))
    assert sorted(m["AGH30W"]) == [f"{BASE}/catalog/toyota/21130/type/3BA-AGH30W/", f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/", f"{BASE}/catalog/toyota/3491/type/DBA-AGH30W/"]
    assert m["GDH206V"] == [f"{BASE}/catalog/toyota/2522/type/3DF-GDH206V/"] and m["GDH206M"][0].startswith(f"{BASE}/catalog/mazda/")
    assert m["U69V"] == [f"{BASE}/catalog/mitsubishi/23423/type/ZAB-U69V/"] and f"{BASE}/catalog/toyota/3491/type/DBA-ANH10W-PRASK/" in m["ANH10W"]
    assert "DFM5P4" in m and not any(u.endswith("/type/") for urls in m.values() for u in urls)
    assert bare_code("3BA-AGH30W") == "AGH30W" and bare_code("DBA-ANH10W-PRASK") == "ANH10W" and bare_code("-DFM5P4-") == "DFM5P4" and bare_code("KR-KDY280") == "KDY280"

def test_type_page_grades_sections_and_seat_systems():
    grades = parse_type_page((FIX / "type_3BA-AGH30W.html").read_text(encoding="utf-8"), BASE)
    assert len(grades) == 5 and all(isinstance(g, Grade) and g.model_code == "3BA-AGH30W" for g in grades)
    first = grades[0]
    assert first.name == "2.5 S タイプゴールドII" and first.seats == 7 and first.section_ym == 202105 and first.url == f"{BASE}/catalog/toyota/3491/fmc003/116883/"
    lift = next(g for g in grades if "サイドリフトアップ" in g.name)
    assert lift.seat_system == "second_row_lift_up" and lift.url.endswith("/67465/")
    x = next(g for g in grades if g.name == "2.5 X")
    assert x.seats == 8 and x.seat_system == "not_welcab" and x.url.endswith("/62479/")
    assert grades[-1].section_ym == 202001
    assert ym("2021年5月") == 202105 and ym("2026年2月モデル") == 202602 and ym("none") is None

def test_grade_pages_marks_welfare_and_periods():
    x = parse_grade_page((FIX / "grade_alphard_25x.html").read_text(encoding="utf-8"))
    assert x["name"] == "2.5 X" and x["model_code"] == "3BA-AGH30W" and x["welfare"] is False
    assert (x["cruise_mark"], x["cruise"], x["adaptive_mark"], x["adaptive"]) == ("標準装備", "standard", "◯", "standard")
    assert (x["sale_from_ym"], x["sale_to_ym"]) == (202105, 202305)
    lift = parse_grade_page((FIX / "grade_alphard_25s_sidelift.html").read_text(encoding="utf-8"))
    assert lift["welfare"] is True and lift["name"] == "2.5 S サイドリフトアップチルトシート" and lift["cruise"] == "standard"
    dx = parse_grade_page((FIX / "grade_hiace_dx_2026.html").read_text(encoding="utf-8"))
    assert dx["name"] == "2.8 DX ロング GLパッケージ ディーゼル 4WD" and dx["model_code"] == "3DF-GDH206V"
    assert (dx["cruise_mark"], dx["cruise"], dx["adaptive_mark"], dx["adaptive"]) == ("◯", "standard", "", "unknown")
    assert (dx["sale_from_ym"], dx["sale_to_ym"]) == (202602, None) and dx["welfare"] is False

def test_mark_state_vocabulary():
    assert [mark_state(m) for m in ("標準装備", "◯", "○", "オプション", "メーカーオプション", "ディーラーオプション", "なし", "ー", "-", "")] == \
           ["standard", "standard", "standard", "optional", "optional", "optional", "none", "none", "none", "unknown"]
    assert dd("<dl><dt>型式</dt><dd>3BA-AGH30W</dd><dt>駆動方式</dt><dd>エンジン位置：-\n駆動輪：フロント</dd></dl>", "駆動方式") == "エンジン位置：- 駆動輪：フロント"
    assert dd("<dl></dl>", "型式") is None
