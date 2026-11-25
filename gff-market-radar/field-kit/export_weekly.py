#!/usr/bin/env python3
"""تصدير أسبوعي من سجل مربين المنصورة لملفات CSV اللي رادار مستنيها في tracking/inbox/.

الاستخدام:
    python3 export_weekly.py mansoura-breeder-register.xlsx --week 2026-W49 --out weekly/
    python3 export_weekly.py mansoura-breeder-register.xlsx --week 2026-W49 --out weekly/ --upload
    python3 export_weekly.py mansoura-breeder-register.xlsx --week 2026-W49 --out weekly/ \
        --legacy-sales sales-export.xlsx --legacy-map legacy_map.json

- الملف لازم يكون اتفتح واتحفظ في Excel أو LibreOffice قبل التصدير، عشان الخانات المحسوبة (الشريحة، كود الفرع) يبقى ليها قيم.
- الصف 3 (المثال) والصفوف الفاضية وأي كود بيبدأ بـ EX- بيتشالوا.
- مبيعات التجربة وأسعار القرى والملاحظات والشكاوى: بيتصدر بس اللي تاريخه جوه الأسبوع المطلوب (ISO week).
- سجل المربين: بيتصدر كله (لقطة) كل أسبوع.
- --upload بيرفع الملفات في مخزن ذاكرة رادار بأمر ant. لازم يتشغل من فولدر gff-market-radar/field-kit/
  أو تحدد --lock لمسار claude-lock.json.
- ملف الموافقات (mansoura-consent-register.xlsx) فيه أسماء وأرقام، ومش بيتقري هنا خالص.
- --legacy-sales: تصدير مبيعات من السيستم القديم (xlsx أو csv). بيتقري منه **بس** الأعمدة المكتوبة في
  legacy_map.json، فأي عمود فيه اسم أو تليفون أو عنوان مابيعدّيش. لو اتحدد، مبيعات الأسبوع بتيجي منه
  بدل شيت "مبيعات التجربة" (والسكريبت بينبّه لو الشيت فيه صفوف في نفس الأسبوع عشان مايتحسبوش مرتين).
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
SALES_HEADER = ["invoice_date", "branch_code", "customer_code", "customer_segment",
                "product_code", "qty_kg", "unit_price", "channel"]
SEGMENTS = {"H1", "H2", "H3", "H4", "S1", "S2", "S3", "S4"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]
MAX_BYTES = 100_000  # حد الملف الواحد في الذاكرة
STORE_KEY = "./agents/gff-market-radar/memory_store-market-knowledge.yaml"


def parse_week(s):
    year, week = s.upper().split("-W")
    start = dt.date.fromisocalendar(int(year), int(week), 1)
    return start, start + dt.timedelta(days=6)


def as_date(v, formats=DATE_FORMATS):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and v.strip():
        txt = v.strip().split(" ")[0]
        for fmt in formats:
            try:
                return dt.datetime.strptime(txt, fmt).date()
            except ValueError:
                continue
    return None


def as_number(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("٫", ".").strip())
    except ValueError:
        return None


def register_segments(wb):
    """كود العميل -> كود الشريحة من سجل المربين."""
    ws = wb["سجل المربين"]
    out = {}
    for row in ws.iter_rows(min_row=4, values_only=True):
        code, seg = cell_text(row[0]), cell_text(row[1])
        if code and not code.upper().startswith("EX-"):
            out[code] = seg or "UNKNOWN"
    return out


def read_legacy_rows(path, cfg):
    """بيرجّع صفوف كـ dict بأسماء أعمدة السيستم القديم، للأعمدة المطلوبة بس."""
    wanted = [h for h in cfg["columns"].values() if h]
    hdr_row = int(cfg.get("header_row", 1))
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        header, data = rows[hdr_row - 1], rows[hdr_row:]
    else:
        wbl = load_workbook(path, data_only=True, read_only=True)
        ws = wbl[cfg["sheet"]] if cfg.get("sheet") else wbl.worksheets[0]
        rows = list(ws.iter_rows(min_row=hdr_row, values_only=True))
        header, data = rows[0], rows[1:]
    header = [cell_text(h) for h in header]
    missing = [h for h in wanted if h not in header]
    if missing:
        raise SystemExit(f"الأعمدة دي مش موجودة في تصدير السيستم القديم: {missing}\nالأعمدة الموجودة: {header}")
    idx = {h: header.index(h) for h in wanted}
    for r in data:
        yield {h: (r[i] if i < len(r) else None) for h, i in idx.items()}


def legacy_sales(path, cfg, start, end, seg_lookup):
    c = cfg["columns"]
    formats = [cfg["date_format"]] if cfg.get("date_format") else DATE_FORMATS
    branches = set(cfg.get("branch_values") or [])
    products = set(cfg.get("product_codes") or [])
    kg_per_unit = cfg.get("kg_per_unit_by_product") or {}
    unit = cfg.get("qty_unit", "kg")
    out, skipped_units = [], set()
    for r in read_legacy_rows(path, cfg):
        d = as_date(r.get(c["invoice_date"]), formats)
        if d is None or not (start <= d <= end):
            continue
        if branches and c.get("branch") and cell_text(r.get(c["branch"])) not in branches:
            continue
        prod = cell_text(r.get(c["product_code"]))
        if not prod or (products and prod not in products):
            continue
        qty, price = as_number(r.get(c["qty"])), as_number(r.get(c.get("unit_price"))) if c.get("unit_price") else None
        if qty is None:
            continue
        if unit != "kg":
            k = kg_per_unit.get(prod)
            if not k:
                skipped_units.add(prod)
                continue
            qty, price = qty * k, (price / k if price is not None else None)
        code = cell_text(r.get(c["customer_code"]))
        group = cell_text(r.get(c["customer_group"])) if c.get("customer_group") else ""
        seg = group if group in SEGMENTS else seg_lookup.get(code, "UNKNOWN")
        channel = cell_text(r.get(c["channel"])) if c.get("channel") else "RETAIL"
        out.append({"invoice_date": d.isoformat(), "branch_code": cfg.get("branch_code", "MNS"),
                    "customer_code": code, "customer_segment": seg, "product_code": prod,
                    "qty_kg": f"{qty:g}", "unit_price": "" if price is None else f"{price:.2f}",
                    "channel": channel or "RETAIL"})
    if skipped_units:
        print(f"تحذير: أصناف مالهاش وزن في kg_per_unit_by_product واتشالت: {sorted(skipped_units)}", file=sys.stderr)
    return out


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
    ap.add_argument("--legacy-sales", help="تصدير مبيعات السيستم القديم (xlsx أو csv)")
    ap.add_argument("--legacy-map", default="legacy_map.json")
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
        if prefix == "sales" and a.legacy_sales:
            cfg = json.loads(Path(a.legacy_map).read_text(encoding="utf-8"))
            if rows:
                print(f"تنبيه: شيت مبيعات التجربة فيه {len(rows)} صف في الأسبوع ده، واتجاهلوا لأن المبيعات جاية من السيستم القديم", file=sys.stderr)
            header = SALES_HEADER
            rows = legacy_sales(a.legacy_sales, cfg, start, end, register_segments(wb))
            unknown = sum(1 for r in rows if r["customer_segment"] == "UNKNOWN")
            if unknown:
                print(f"تنبيه: {unknown} عملية شرا لعملاء من غير شريحة - المندوب يسجلهم في سجل المربين أو في مجموعة العملاء", file=sys.stderr)
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
