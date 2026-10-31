#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探查 PDF 论文结构：页数、页面尺寸、栏数、标题层级、图注/表注、图片分布。

用法:
    python probe_pdf.py --pdf INPUT.pdf [--max-pages 30]
"""
import argparse
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("需要 PyMuPDF：pip install pymupdf")

CAP_RE = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*[:.]?\s", re.I)
TAB_RE = re.compile(r"^\s*(?:Table|表)\s*(\d+|[IVX]+|[A-Z]\d+)\s*[:.]?\s", re.I)
HEAD_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+[A-Z]")


def page_text_stats(page):
    xs, sizes, bold_heads = [], [], []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        xs += [b["bbox"][0], b["bbox"][2]]
        for line in b["lines"]:
            txt = "".join(s["text"] for s in line["spans"]).strip()
            if not txt or len(txt) > 90:
                continue
            sz = max(s["size"] for s in line["spans"])
            is_bold = any(re.search(r"Bold|Heavy|Medi", s["font"]) for s in line["spans"])
            if sz >= 11.5 or (is_bold and sz >= 10):
                bold_heads.append((round(sz, 1), txt))
    return xs, bold_heads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--max-pages", type=int, default=40)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    print(f"file      : {args.pdf}")
    print(f"pages     : {doc.page_count}")
    print(f"page size : {doc[0].rect.width:.0f} x {doc[0].rect.height:.0f} pt")

    all_x = []
    for pno in range(min(doc.page_count, args.max_pages)):
        xs, _ = page_text_stats(doc[pno])
        all_x += xs
    if all_x:
        lo, hi = min(all_x), max(all_x)
        print(f"text x-range: {lo:.0f} - {hi:.0f} pt "
              f"({'双栏' if _looks_two_column(doc) else '单栏/整幅'})")

    print("\n--- 每页：图片数 / 图注 / 表注 ---")
    for pno in range(min(doc.page_count, args.max_pages)):
        page = doc[pno]
        caps, tabs = _captions(page)
        n_img = len(page.get_images(full=True))
        n_draw = len(page.get_drawings())
        if n_img or n_draw or caps or tabs:
            print(f"p{pno + 1:<3} imgs={n_img:<3} draws={n_draw:<4} caps={caps} tabs={tabs}")

    print("\n--- 标题候选（字号 >=11.5 或加粗 >=10）---")
    seen = set()
    for pno in range(min(doc.page_count, args.max_pages)):
        _, heads = page_text_stats(doc[pno])
        for sz, txt in heads:
            key = (sz, txt[:40])
            if key in seen:
                continue
            seen.add(key)
            print(f"  p{pno + 1:<3} {sz:>5}pt  {txt[:70]}")


def _looks_two_column(doc, sample=4):
    """粗判栏数：看最宽的那批文本块占页宽的比例。

    单栏正文一般占 ~0.75–0.85 页宽；双栏单栏正文仅 ~0.40–0.48。
    """
    widths, pw = [], doc[0].rect.width
    for pno in range(min(doc.page_count, sample)):
        for b in doc[pno].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            w = b["bbox"][2] - b["bbox"][0]
            if w > 20:
                widths.append(w)
    if not widths:
        return False
    widths.sort()
    top = widths[-max(1, len(widths) // 5):]          # 最宽的 20%
    median_wide = top[len(top) // 2]
    return median_wide < 0.55 * pw


def _captions(page):
    """图注/表注：块内任意一行匹配 + 块高限制（排除 “Figure 1 shows…” 这类正文段）。

    图注块首行可能是空行，因此不能只看 lines[0]。
    """
    caps, tabs = [], []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        bh = b["bbox"][3] - b["bbox"][1]
        lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
        if not lines:
            continue
        for ln in lines:
            if not ln:
                continue
            m = CAP_RE.match(ln)
            if m and bh < 45:
                caps.append(m.group(1))
                break
            m = TAB_RE.match(ln)
            if m and bh < 60:
                tabs.append(m.group(1))
                break
    return caps, tabs


if __name__ == "__main__":
    main()
