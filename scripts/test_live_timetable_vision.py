"""Live evaluation of the current local vision model timetable extraction against synthetic oracle images.

Implements S03 verification from docs/project/TIMETABLE_VISION_AND_DATA_2026-10-05.md.
Uses purely synthetic schedules (no personal data), evaluates course/day/start/end/room accuracy.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Ensure app is importable
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import pymupdf
from app import timetable_vision, timetable_store


def draw_table_grid(page, x0, y0, col_widths, row_heights, headers, rows):
    """Draw a clean grid table with borders and centered/left-aligned text."""
    # Draw header background
    total_w = sum(col_widths)
    h_h = row_heights[0]
    page.draw_rect(pymupdf.Rect(x0, y0, x0 + total_w, y0 + h_h), color=(0.2, 0.4, 0.7), fill=(0.9, 0.93, 0.98))

    # Draw header text
    curr_x = x0
    for w, h_text in zip(col_widths, headers):
        page.insert_text((curr_x + 8, y0 + h_h - 8), h_text, fontsize=12, fontname="helv", color=(0.1, 0.2, 0.4))
        curr_x += w

    # Draw rows
    curr_y = y0 + h_h
    for row_idx, row in enumerate(rows):
        r_h = row_heights[row_idx + 1]
        # Alternate row fill
        fill_c = (0.98, 0.98, 0.99) if row_idx % 2 == 1 else (1.0, 1.0, 1.0)
        page.draw_rect(pymupdf.Rect(x0, curr_y, x0 + total_w, curr_y + r_h), color=(0.7, 0.75, 0.8), fill=fill_c)

        curr_x = x0
        for w, cell_text in zip(col_widths, row):
            page.insert_text((curr_x + 8, curr_y + r_h - 8), str(cell_text), fontsize=11, fontname="helv", color=(0.1, 0.1, 0.1))
            curr_x += w
        curr_y += r_h

    # Draw outer border
    total_h = sum(row_heights)
    page.draw_rect(pymupdf.Rect(x0, y0, x0 + total_w, y0 + total_h), color=(0.3, 0.4, 0.6), width=1.5)


def create_synthetic_image_1() -> bytes:
    """Image 1: Standard weekly schedule with explicit clock times."""
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=500)

    # Title
    page.insert_text((50, 45), "THOI KHOA BIEU HOC KY I - 2026", fontsize=18, fontname="helv", color=(0.1, 0.2, 0.5))
    page.insert_text((50, 68), "Chuong trinh Dao tao Dai hoc Chinh quy", fontsize=11, fontname="helv", color=(0.4, 0.4, 0.4))

    headers = ["Thu", "Ten mon hoc", "Gio hoc", "Phong"]
    col_widths = [100, 280, 180, 140]
    row_heights = [32, 30, 30, 30, 30]

    rows = [
        ["Thu 2", "Dai so tuyen tinh", "07:30 - 09:30", "A101"],
        ["Thu 2", "Ky thuat lap trinh C++", "09:45 - 11:45", "B204"],
        ["Thu 4", "Tam ly hoc dai cuong", "13:00 - 15:00", "C302"],
        ["Thu 6", "Vat ly dai cuong 1", "08:00 - 10:00", "D105"],
    ]

    draw_table_grid(page, 50, 100, col_widths, row_heights, headers, rows)

    pix = page.get_pixmap(dpi=150)
    return pix.tobytes("png")


def create_synthetic_image_2() -> bytes:
    """Image 2: Schedule with lesson periods (Tiet) and mixed explicit hours."""
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=500)

    # Title
    page.insert_text((50, 45), "LICH HOC THEO TIET - HOC KY II", fontsize=18, fontname="helv", color=(0.1, 0.2, 0.5))
    page.insert_text((50, 68), "Luu y: Cac lop tiet 1-3 hoc buoi sang, tiet 7-9 buoi chieu", fontsize=11, fontname="helv", color=(0.4, 0.4, 0.4))

    headers = ["Thu", "Mon hoc", "Tiet hoc / Gio", "Phong hoc"]
    col_widths = [100, 260, 200, 140]
    row_heights = [32, 30, 30, 30]

    rows = [
        ["Thu 3", "Giai tich 1", "Tiet 1-3", "Giang duong 1"],
        ["Thu 3", "Cau truc du lieu", "13:30 - 15:30", "Phong may 2"],
        ["Thu 5", "Toan roi rac", "Tiet 7-9", "Hoi truong B"],
    ]

    draw_table_grid(page, 50, 100, col_widths, row_heights, headers, rows)

    pix = page.get_pixmap(dpi=150)
    return pix.tobytes("png")


def evaluate_extraction(predicted_entries, oracle_entries):
    """Compare extracted entries against oracle ground truth."""
    metrics = {
        "oracle_count": len(oracle_entries),
        "predicted_count": len(predicted_entries),
        "matches": [],
        "course_exact_match": 0,
        "weekday_match": 0,
        "time_match": 0,
        "room_match": 0,
    }

    # Match each oracle entry with best predicted entry
    used_pred = set()
    for o_idx, o in enumerate(oracle_entries):
        best_p_idx = None
        best_p = None
        best_score = -1

        for p_idx, p in enumerate(predicted_entries):
            if p_idx in used_pred:
                continue
            # Simple scoring: matching weekday gives 2 pts, matching course substring gives 3 pts
            score = 0
            if p.get("weekday") == o.get("weekday"):
                score += 2
            o_course = o.get("course", "").lower()
            p_course = str(p.get("course", "")).lower()
            if o_course in p_course or p_course in o_course:
                score += 3
            if score > best_score:
                best_score = score
                best_p_idx = p_idx
                best_p = p

        match_detail = {
            "oracle": o,
            "predicted": best_p,
            "weekday_ok": False,
            "course_ok": False,
            "time_ok": False,
            "room_ok": False,
        }

        if best_p:
            used_pred.add(best_p_idx)
            # Check weekday
            if best_p.get("weekday") == o.get("weekday"):
                match_detail["weekday_ok"] = True
                metrics["weekday_match"] += 1

            # Check course (normalized substring)
            o_c = o.get("course", "").lower().replace(" ", "")
            p_c = str(best_p.get("course", "")).lower().replace(" ", "")
            if o_c in p_c or p_c in o_c:
                match_detail["course_ok"] = True
                metrics["course_exact_match"] += 1

            # Check time
            o_s, o_e = o.get("start_time"), o.get("end_time")
            p_s, p_e = best_p.get("start_time"), best_p.get("end_time")
            if (o_s == p_s or (not o_s and not p_s)) and (o_e == p_e or (not o_e and not p_e)):
                match_detail["time_ok"] = True
                metrics["time_match"] += 1

            # Check room
            o_r = (o.get("room") or "").lower().replace(" ", "")
            p_r = (best_p.get("room") or "").lower().replace(" ", "")
            if o_r and (o_r in p_r or p_r in o_r):
                match_detail["room_ok"] = True
                metrics["room_match"] += 1
            elif not o_r and not p_r:
                match_detail["room_ok"] = True
                metrics["room_match"] += 1

        metrics["matches"].append(match_detail)

    n = max(1, len(oracle_entries))
    metrics["course_accuracy"] = round(metrics["course_exact_match"] / n, 3)
    metrics["weekday_accuracy"] = round(metrics["weekday_match"] / n, 3)
    metrics["time_accuracy"] = round(metrics["time_match"] / n, 3)
    metrics["room_accuracy"] = round(metrics["room_match"] / n, 3)
    return metrics


def main():
    print("=== LIVE VLM TIMETABLE BENCHMARK (S03) ===")
    print("Model: configured local vision engine; inspect runtime digest in the result")
    out_dir = BASE_DIR / "data" / "evaluation" / "timetable-20261005"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Image 1 - Explicit clock times
    print("\n[Test 1] Synthesizing Image 1 (Explicit Clock Times)...")
    img1_bytes = create_synthetic_image_1()
    img1_hash = hashlib.sha256(img1_bytes).hexdigest()
    img1_path = out_dir / "synthetic_timetable_1.png"
    img1_path.write_bytes(img1_bytes)
    print(f"Saved Image 1: {img1_path} ({len(img1_bytes)} bytes, SHA256: {img1_hash[:12]}...)")

    oracle1 = [
        {"weekday": 1, "course": "Dai so tuyen tinh", "start_time": "07:30", "end_time": "09:30", "room": "A101"},
        {"weekday": 1, "course": "Ky thuat lap trinh C++", "start_time": "09:45", "end_time": "11:45", "room": "B204"},
        {"weekday": 3, "course": "Tam ly hoc dai cuong", "start_time": "13:00", "end_time": "15:00", "room": "C302"},
        {"weekday": 5, "course": "Vat ly dai cuong 1", "start_time": "08:00", "end_time": "10:00", "room": "D105"},
    ]

    print("Executing live extraction on Image 1 via the configured local vision engine...")
    t0 = time.time()
    res1 = timetable_vision.call_vlm_extract(
        image_bytes=img1_bytes,
        image_hash=img1_hash,
        filename="synthetic_timetable_1.png",
        timeout_seconds=90.0
    )
    t1_dur = time.time() - t0
    print(f"Image 1 extraction completed in {t1_dur:.2f}s (cached={res1.get('cached')})")

    metrics1 = evaluate_extraction(res1.get("entries", []), oracle1)
    metrics1["latency_seconds"] = round(t1_dur, 2)
    metrics1["uncertainties"] = res1.get("uncertainties", [])
    print(f"Metrics 1: Course Acc: {metrics1['course_accuracy']*100}% | Weekday Acc: {metrics1['weekday_accuracy']*100}% | Time Acc: {metrics1['time_accuracy']*100}% | Room Acc: {metrics1['room_accuracy']*100}%")

    # 2. Image 2 - Period-based and Mixed Hours
    print("\n[Test 2] Synthesizing Image 2 (Period-based / Missing Times)...")
    img2_bytes = create_synthetic_image_2()
    img2_hash = hashlib.sha256(img2_bytes).hexdigest()
    img2_path = out_dir / "synthetic_timetable_2.png"
    img2_path.write_bytes(img2_bytes)
    print(f"Saved Image 2: {img2_path} ({len(img2_bytes)} bytes, SHA256: {img2_hash[:12]}...)")

    oracle2 = [
        {"weekday": 2, "course": "Giai tich 1", "start_time": None, "end_time": None, "period": "1-3", "room": "Giang duong 1"},
        {"weekday": 2, "course": "Cau truc du lieu", "start_time": "13:30", "end_time": "15:30", "period": None, "room": "Phong may 2"},
        {"weekday": 4, "course": "Toan roi rac", "start_time": None, "end_time": None, "period": "7-9", "room": "Hoi truong B"},
    ]

    print("Executing live extraction on Image 2 via the configured local vision engine...")
    t0 = time.time()
    res2 = timetable_vision.call_vlm_extract(
        image_bytes=img2_bytes,
        image_hash=img2_hash,
        filename="synthetic_timetable_2.png",
        timeout_seconds=90.0
    )
    t2_dur = time.time() - t0
    print(f"Image 2 extraction completed in {t2_dur:.2f}s (cached={res2.get('cached')})")

    metrics2 = evaluate_extraction(res2.get("entries", []), oracle2)
    metrics2["latency_seconds"] = round(t2_dur, 2)
    metrics2["has_periods_without_clock_time"] = res2.get("has_periods_without_clock_time", False)
    metrics2["uncertainties"] = res2.get("uncertainties", [])
    print(f"Metrics 2: Course Acc: {metrics2['course_accuracy']*100}% | Weekday Acc: {metrics2['weekday_accuracy']*100}% | Missing Times Flagged: {metrics2['has_periods_without_clock_time']}")

    overall = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_name": timetable_vision.VLM_MODEL_NAME,
        "model_digest": res1.get("model_digest"),
        "prompt_version": timetable_vision.PROMPT_VERSION,
        "tests": [
            {
                "test_id": "S03_TEST_1_EXPLICIT_HOURS",
                "filename": "synthetic_timetable_1.png",
                "image_hash": img1_hash,
                "metrics": metrics1,
                "extracted_entries": res1.get("entries", []),
            },
            {
                "test_id": "S03_TEST_2_PERIOD_BASED_MIXED",
                "filename": "synthetic_timetable_2.png",
                "image_hash": img2_hash,
                "metrics": metrics2,
                "extracted_entries": res2.get("entries", []),
            }
        ],
        "summary": {
            "total_oracle_entries": len(oracle1) + len(oracle2),
            "overall_course_accuracy": round((metrics1["course_exact_match"] + metrics2["course_exact_match"]) / (len(oracle1) + len(oracle2)), 3),
            "overall_weekday_accuracy": round((metrics1["weekday_match"] + metrics2["weekday_match"]) / (len(oracle1) + len(oracle2)), 3),
            "period_warning_detected": metrics2.get("has_periods_without_clock_time", False),
            "live_inference_confirmed": not (res1.get("cached") and res2.get("cached")),
        }
    }

    report_path = out_dir / "timetable-live-evaluation-20261005.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(overall, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Benchmark Report successfully written to: {report_path}")
    print(json.dumps(overall["summary"], indent=2))


if __name__ == "__main__":
    main()
