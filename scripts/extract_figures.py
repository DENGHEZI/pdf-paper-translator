#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按图注锚定抽取论文插图（整块裁切渲染）。

已在 8 篇真实论文上验证（IEEE 双栏 / MDPI / Elsevier / arXiv / 数据论文），
处理过的情况：
1. 图注是居中窄块（宽 170~390pt）——宽度阈值放小到 120；
2. 图注块首行可能是空行——扫描块内首个非空行；
3. 正文 “Figure 1 shows…” / “Figure 4(A) is increased…” 误判——按分隔符与首词大小写排除；
4. 图注很长（3~5 行）——不能用「块高 < 45」一刀切；
5. 图跨页（图在上页底部、图注在下页顶部）——回退到上一页；
6. 双栏论文——按栏裁切，不能按整页宽；
7. 图上方的表格被裁进来——把表注与数字密集行也当障碍物；
8. 横向页（/Rotate 90）——bbox 用 rotation_matrix 映射到显示空间。

用法:
    python extract_figures.py --pdf INPUT.pdf --out-dir work/figures [--dpi 250]
产物:
    fig_<label>.png     每张图
    figures.json        清单：label / page / rect / dpi / 非白占比 / 最长行
"""
import argparse
import json
import os
import re
import sys

try:
    import fitz
except ImportError:
    sys.exit("需要 PyMuPDF：pip install pymupdf")

CAP_HEAD = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*([:：.])?\s*(.*)$", re.I)
TABLE_HEAD = re.compile(r"^\s*(?:Table|表)\s*(\d+|[IVX]+|[A-Z]\d+)\s*[:：.]?\s*(.*)$", re.I)

PAGE_MARGIN_TOP = 55.0
MAX_CAPTION_H = 120.0
MIN_CAPTION_W = 120.0


# ---------------------------------------------------------------- 基础工具
def bbox_of(page, b):
    """块/图 bbox → 显示空间（处理 /Rotate）。"""
    r = fitz.Rect(b["bbox"])
    if page.rotation:
        r = r * page.rotation_matrix
    return r


def match_caption(line):
    """返回图号；若这行其实是正文（如 “Figure 1 shows …”），返回 None。"""
    m = CAP_HEAD.match(line)
    if not m:
        return None
    sep, rest = m.group(2), m.group(3).strip()
    if rest and rest[0].isascii() and rest[0].islower():
        return None                     # Figure 1 shows / illustrates / depicts
    if not sep and rest.startswith("("):
        return None                     # Figure 4(A) is increased …
    return m.group(1)


def page_text_boxes(page):
    """产出 (bbox_display, lines) —— 只在文本块上。"""
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
        if lines:
            yield bbox_of(page, b), lines


def content_span(doc, sample=8):
    xs = []
    for pno in range(min(doc.page_count, sample)):
        page = doc[pno]
        for r, _ in page_text_boxes(page):
            xs += [r.x0, r.x1]
    if not xs:
        return 60.0, doc[0].rect.width - 60
    pw = doc[0].rect.width
    xs = sorted(x for x in xs if x > pw * 0.05)      # 丢掉页边水印/装订线
    if not xs:
        return 60.0, pw - 60
    return xs[max(0, int(len(xs) * 0.03))] - 2, xs[min(len(xs) - 1, int(len(xs) * 0.97))] + 2


def column_ranges(doc, sample=6):
    """判断是否双栏，并给出左右栏的 x 范围（显示空间）。"""
    lo, hi = content_span(doc)
    mid = (lo + hi) / 2
    left, right = [], []
    for pno in range(min(doc.page_count, sample)):
        page = doc[pno]
        pw = page.rect.width
        for r, _ in page_text_boxes(page):
            if r.x0 < pw * 0.05:
                continue
            if r.x1 <= mid + 2:
                left += [r.x0, r.x1]
            elif r.x0 >= mid - 2:
                right += [r.x0, r.x1]
    if len(left) > 16 and len(right) > 16:
        return True, (min(left), max(left)), (min(right), max(right))
    return False, (lo, hi), (lo, hi)


def scan(page, col_w):
    """返回 (captions, body_bottoms, table_bottoms)。

    captions = [(label, y0, y1, x0, x1)] —— 只认块内首个非空行
    """
    caps, body, tables = [], [], []
    for r, lines in page_text_boxes(page):
        w, h = r.width, r.height
        first = next((ln for ln in lines if ln), "")
        if not first:
            continue

        if h < MAX_CAPTION_H and w > MIN_CAPTION_W:
            label = match_caption(first)
            if label:
                caps.append((label, r.y0, r.y1, r.x0, r.x1))
                continue

        joined = " ".join(lines)

        if TABLE_HEAD.match(first):                      # 表注
            tables.append(r.y1)
            continue

        nums = re.findall(r"\d+(?:\.\d+)?", joined)      # 表格数据行
        if w >= col_w * 0.50 and len(nums) >= 4 and 20 <= len(joined) <= 600:
            tables.append(r.y1)
            continue

        if w >= col_w * 0.72 and len(joined) >= 80:      # 正文段落
            body.append(r.y1)

    caps.sort(key=lambda x: x[1])
    return caps, body, tables


def footer_top(page):
    h = page.rect.height
    top = h - 20
    for r, _ in page_text_boxes(page):
        if r.y0 < h * 0.88:
            continue
        if r.height < 40:
            top = min(top, r.y0 - 2)
    return top


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", default="work/figures")
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--no-cross-page", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    doc = fitz.open(args.pdf)

    two_col, left_col, right_col = column_ranges(doc)
    col_w = (min(left_col[1] - left_col[0], right_col[1] - right_col[0])
             if two_col else left_col[1] - left_col[0])
    mid = (left_col[0] + right_col[1]) / 2
    print(f"版式: {'双栏' if two_col else '单栏'}"
          f" | 左 x {left_col[0]:.0f}-{left_col[1]:.0f}"
          + (f" | 右 x {right_col[0]:.0f}-{right_col[1]:.0f}" if two_col else ""))

    results = []
    for pno in range(doc.page_count):
        page = doc[pno]
        caps, body, tables = scan(page, col_w)
        prev = PAGE_MARGIN_TOP
        for label, cy0, cy1, cx0, cx1 in caps:
            x0, x1 = (right_col if (two_col and (cx0 + cx1) / 2 > mid) else left_col)

            above = [y for y in (body + tables) if y <= cy0 - 2]
            top = max([PAGE_MARGIN_TOP, prev] + above)
            if top > cy0 - 40:
                top = max(prev, PAGE_MARGIN_TOP)
            rect = fitz.Rect(x0, top + 2, x1, cy0 - 2)

            if rect.height < 40 and pno > 0 and not args.no_cross_page:
                pp = doc[pno - 1]
                _, body_prev, tables_prev = scan(pp, col_w)
                imgs = [bbox_of(pp, im) for im in pp.get_image_info()]
                if imgs:
                    last_y0 = min(r.y0 for r in imgs if r.y0 > 200)
                    tb = [y for y in (body_prev + tables_prev) if y <= last_y0 - 2]
                    t2 = max([PAGE_MARGIN_TOP] + tb)
                    rect = fitz.Rect(x0, t2 + 2, x1, footer_top(pp))
                    results.append((label, pno + 1, pno - 1, rect, True))
                    prev = cy1
                    continue

            if rect.height < 40:
                print(f"  ! p{pno + 1} 图 {label}: 区域过小 ({rect.height:.0f}pt)，跳过")
                prev = cy1
                continue
            results.append((label, pno + 1, pno, rect, False))
            prev = cy1

    manifest = []
    for label, page_no, render_idx, rect, crossed in results:
        page = doc[render_idx]
        pix = page.get_pixmap(clip=rect, dpi=args.dpi)      # clip 为显示坐标
        path = os.path.join(args.out_dir, f"fig_{label}.png")
        pix.save(path)

        # 校验：裁切区内最长文本行（>110 字符视为混入正文）
        text_clip = rect * page.derotation_matrix if page.rotation else rect
        inner = page.get_text("text", clip=text_clip)
        longest = max((len(x) for x in inner.splitlines()), default=0)

        sm = pix.samples
        n = len(sm) // 3
        step = max(1, n // 4000)
        nw = sum(1 for k in range(0, n, step)
                 if sm[k * 3] < 245 or sm[k * 3 + 1] < 245 or sm[k * 3 + 2] < 245)
        frac = nw / max(1, n // step)

        manifest.append({
            "label": label, "page": page_no,
            "rect": [round(v, 1) for v in rect],
            "file": os.path.basename(path), "dpi": args.dpi,
            "nonwhite": round(frac, 3), "max_line_len": longest,
            "rotated": bool(page.rotation), "cross_page": crossed,
        })
        print(f"  图 {label:<3} p{page_no:<3} y{rect.y0:>5.0f}-{rect.y1:<5.0f} "
              f"{pix.width}x{pix.height} 非白={frac:>4.0%} 最长行={longest}"
              + ("  [跨页]" if crossed else "") + ("  [旋转]" if page.rotation else ""))

    with open(os.path.join(args.out_dir, "figures.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    labels = sorted({int(m["label"]) for m in manifest if str(m["label"]).isdigit()})
    print(f"\n共导出 {len(manifest)} 张图 -> {args.out_dir}")
    if labels:
        missing = [i for i in range(1, max(labels) + 1) if i not in labels]
        print(f"图号范围 1..{max(labels)}，缺失: {missing if missing else '无'}")


if __name__ == "__main__":
    main()
