#!/usr/bin/env python3
"""تصدير أسبوعي من سجل مربين المنصورة لملفات CSV اللي رادار مستنيها في tracking/inbox/.

الاستخدام:
    python3 export_weekly.py mansoura-breeder-register.xlsx --week 2026-W49 --out weekly/
    python3 export_weekly.py mansoura-breeder-register.xlsx --week 2026-W49 --out weekly/ --upload

- الملف لازم يكون اتفتح واتحفظ في Excel أو LibreOffice قبل التصدير، عشان الخانات المحسوبة (الشريحة، كود الفرع) يبقى ليها قيم.
- الصف 3 (المثال) والصفوف الفاضية وأي كود بيبدأ بـ EX- بيتشالوا.
- مبيعات التجربة وأسعار القرى والملاحظات والشكاوى: بيتصدر بس اللي تاريخه جوه الأسبوع المطلوب (ISO week).
- سجل المربين: بيتصدر كله (لقطة) كل أسبوع.
- --upload بيرفع الملفات في مخزن ذاكرة رادار بأمر ant. لازم يتشغل من فولدر gff-market-radar/field-kit/
  أو تحدد --lock لمسار claude-lock.json.
- ملف الموافقات (mansoura-consent-register.xlsx) فيه أسماء وأرقام، ومش بيتقري هنا خالص.
"""
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from openpyxl import load_workbook

SHEETS = {
    # اسم الشيت: (بادئة اسم الملف، عمود التاريخ للفلترة أو None، عمود الكود اللي بيتشال لو EX-)
    "سجل المربين": ("breeders", None, "customer_code"),
    "مبيعات التجربة": ("sales", "invoice_date", "customer_code"),
    "أسعار القرى": ("field-prices", "date", None),
    "ملاحظات المندوبين": ("rep-notes", "date", "customer_code_or_survey_code"),
    "الشكاوى": ("complaints", "date", "customer_code"),
}
MAX_BYTES = 100_000  # حد الملف الواحد في الذاكرة
STORE_KEY = "./agents/gff-market-radar/memory_store-market-knowledge.yaml"


def parse_week(s):
    year, week = s.upper().split("-W")
    start = dt.date.fromisocalendar(int(year), int(week), 1)
    return start, start + dt.timedelta(days=6)


def as_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return dt.date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def export_sheet(ws, date_key, code_key, start, end, calc_segment=False):
    keys = [c.value for c in ws[2]]
    cols = [(i, k) for i, k in enumerate(keys) if k]
    rows, missing_calc = [], 0
    for row in ws.iter_rows(min_row=4, values_only=True):  # الصف 3 مثال دايماً
        rec = {k: row[i] if i < len(row) else None for i, k in cols}
        if all(cell_text(v) == "" for v in rec.values()):
            continue
        if code_key and cell_text(rec.get(code_key)).upper().startswith("EX-"):
            continue
        if date_key:
            d = as_date(rec.get(date_key))
            if d is None or not (start <= d <= end):
                continue
        if calc_segment and rec.get(code_key) and rec.get("customer_segment") is None:
            missing_calc += 1
        rows.append({k: cell_text(rec[k]) for _, k in cols})
    return [k for _, k in cols], rows, missing_calc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--week", required=True, help="مثال: 2026-W49")
    ap.add_argument("--out", default="weekly")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--lock", default="../claude-lock.json")
    a = ap.parse_args()

    start, end = parse_week(a.week)
    wb = load_workbook(a.workbook, data_only=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    year, week = a.week.upper().split("-W")
    written = []
    for sheet, (prefix, date_key, code_key) in SHEETS.items():
        header, rows, missing = export_sheet(wb[sheet], date_key, code_key, start, end,
                                             calc_segment=prefix in ("sales", "rep-notes"))
        if missing:
            print(f"تحذير: {sheet}: {missing} صف من غير قيم محسوبة - افتح الملف في Excel واحفظه وبعدين صدّر تاني", file=sys.stderr)
        name = f"{prefix}-{year}-W{int(week):02d}.csv"
        path = out / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        size = path.stat().st_size
        print(f"{name}: {len(rows)} صف، {size} بايت")
        if size > MAX_BYTES:
            print(f"تحذير: {name} أكبر من 100KB - لازم يتقسم قبل الرفع", file=sys.stderr)
        written.append(path)

    if not a.upload:
        print("للرفع: شغّل نفس الأمر مع --upload")
        return 0

    lock = json.loads(Path(a.lock).read_text(encoding="utf-8"))
    store = lock["resources"][STORE_KEY]["id"]
    failed = 0
    for p in written:
        mem_path = f"/tracking/inbox/{p.name}"
        rel = Path(os.path.relpath(p)).as_posix()
        payload = f'content: "@./{rel}"\n'
        r = subprocess.run(
            ["ant", "beta:memory-stores:memories", "create", "--memory-store-id", store, "--path", mem_path],
            input=payload, text=True, capture_output=True,
        )
        if r.returncode != 0:
            failed += 1
            print(f"فشل رفع {p.name}: {r.stderr.strip()}", file=sys.stderr)
        else:
            print(f"اترفع: {mem_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
