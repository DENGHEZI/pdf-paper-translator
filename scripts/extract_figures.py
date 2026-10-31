#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按图注锚定抽取论文插图（整块裁切渲染）。

真实论文里会遇到的六种情况都已处理：
1. 图注是居中窄块（宽 170~390pt）——宽度阈值放小到 120；
2. 图注块首行可能是空行——扫描块内任意一行；
3. 正文里 “Figure 1 shows…” 开头的段落——按「编号后首个词是否小写」排除；
4. 图注很长（3~5 行，块高 50~80pt）——不能用「块高 < 45」一刀切；
5. 图跨页（图在上页底部、图注在下页顶部）——回退到上一页；
6. 双栏论文——按栏裁切，不能按整页宽，否则会把邻栏正文裁进来。

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

# 图注：Figure/Fig./图 + 编号 + 可选分隔符 + 说明
CAP_HEAD = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*([:：.])?\s*(.*)$", re.I)
TABLE_HEAD = re.compile(r"^\s*(?:Table|表)\s*(\d+|[IVX]+|[A-Z]\d+)\s*[:：.]?\s*(.*)$", re.I)

PAGE_MARGIN_TOP = 55.0
MAX_CAPTION_H = 120.0   # 图注块最高多少（再高就不是图注了）
MIN_CAPTION_W = 120.0


def match_caption(line):
    """返回图号；若这行其实是 “Figure 1 shows…” 这种正文，返回 None。"""
    m = CAP_HEAD.match(line)
    if not m:
        return None
    sep, rest = m.group(2), m.group(3).strip()
    # 编号后首个词若是小写英文开头 → 正文（如 “Figure 1 shows / illustrates / depicts”）
    if rest and rest[0].isascii() and rest[0].islower():
        return None
    # 无分隔符却以 “(” 开头 → 正文里的 “Figure 4(A) is increased …”
    if not sep and rest.startswith("("):
        return None
    return m.group(1)


def content_span(doc, sample=8):
    xs = []
    for pno in range(min(doc.page_count, sample)):
        for b in doc[pno].get_text("dict")["blocks"]:
            if b["type"] == 0:
                xs += [b["bbox"][0], b["bbox"][2]]
    if not xs:
        return 60.0, doc[0].rect.width - 60
    pw = doc[0].rect.width
    xs = sorted(x for x in xs if x > pw * 0.05)   # 丢掉页边水印/装订线
    if not xs:
        return 60.0, pw - 60
    return xs[max(0, int(len(xs) * 0.03))] - 2, xs[min(len(xs) - 1, int(len(xs) * 0.97))] + 2


def column_ranges(doc, sample=6):
    """判断是否双栏，并给出左右栏的 x 范围。"""
    lo, hi = content_span(doc)
    mid = (lo + hi) / 2
    left, right = [], []
    for pno in range(min(doc.page_count, sample)):
        for b in doc[pno].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            x0, x1 = b["bbox"][0], b["bbox"][2]
            if x0 < doc[0].rect.width * 0.05:      # 页边水印/装订线，跳过
                continue
            if x1 <= mid + 2:
                left += [x0, x1]
            elif x0 >= mid - 2:
                right += [x0, x1]
    if len(left) > 16 and len(right) > 16:
        return True, (min(left), max(left)), (min(right), max(right))
    return False, (lo, hi), (lo, hi)


def scan(page):
    """返回 (captions, body_bottoms)。

    captions = [(label, y0, y1, x0, x1)]
    body_bottoms = [y1, ...]  只统计“整幅正文段落”，用于定图区上界
    """
    caps, body = [], []
    pw = page.rect.width
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        h = b["bbox"][3] - b["bbox"][1]
        w = b["bbox"][2] - b["bbox"][0]
        lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
        if not lines:
            continue

        label = None
        if h < MAX_CAPTION_H and w > MIN_CAPTION_W:
            for ln in lines:
                if not ln:
                    continue
                label = match_caption(ln)
                if label:
                    break
        if label:
            caps.append((label, b["bbox"][1], b["bbox"][3], b["bbox"][0], b["bbox"][2]))
            continue

        # 正文段落：很宽 + 较长 + 不是表注
        joined = " ".join(lines)
        is_table_cap = bool(TABLE_HEAD.match(lines[0])) if lines and lines[0] else False
        if w > pw * 0.70 and len(joined) >= 80 and not is_table_cap:
            body.append(b["bbox"][3])
    caps.sort(key=lambda x: x[1])
    return caps, body


def footer_top(page):
    """页脚（页码/版权行）上沿。"""
    h = page.rect.height
    top = h - 20
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0 or b["bbox"][1] < h * 0.88:
            continue
        if (b["bbox"][3] - b["bbox"][1]) < 40:
            top = min(top, b["bbox"][1] - 2)
    return top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", default="work/figures")
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--left", type=float, default=None)
    ap.add_argument("--right", type=float, default=None)
    ap.add_argument("--no-cross-page", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    doc = fitz.open(args.pdf)

    two_col, left_col, right_col = column_ranges(doc)
    print(f"版式: {'双栏' if two_col else '单栏'}"
          f" | 左栏 x {left_col[0]:.0f}-{left_col[1]:.0f}"
          + (f" | 右栏 x {right_col[0]:.0f}-{right_col[1]:.0f}" if two_col else ""))
    if args.left is not None:
        left_col = (args.left, left_col[1])
    if args.right is not None:
        right_col = (right_col[0], args.right)
    mid = (left_col[0] + right_col[1]) / 2

    results = []
    for pno in range(doc.page_count):
        page = doc[pno]
        caps, body = scan(page)
        prev = PAGE_MARGIN_TOP
        for label, cy0, cy1, cx0, cx1 in caps:
            # 选栏：按图注中心落在哪一栏
            if two_col and (cx0 + cx1) / 2 > mid:
                x0, x1 = right_col
            else:
                x0, x1 = left_col

            above = [y for y in body if y <= cy0 - 2]
            top = max([PAGE_MARGIN_TOP, prev] + above)
            if top > cy0 - 40:
                top = max(prev, PAGE_MARGIN_TOP)
            rect = fitz.Rect(x0, top + 2, x1, cy0 - 2)

            if rect.height < 40 and pno > 0 and not args.no_cross_page:
                pp = doc[pno - 1]
                _, body_prev = scan(pp)
                imgs = [im["bbox"] for im in pp.get_image_info()]
                if imgs:
                    last_y0 = min(r[1] for r in imgs if r[1] > 200)
                    tb = [y for y in body_prev if y <= last_y0 - 2]
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
        pix = page.get_pixmap(clip=rect, dpi=args.dpi)
        path = os.path.join(args.out_dir, f"fig_{label}.png")
        pix.save(path)

        inner = page.get_text("text", clip=rect)
        longest = max((len(x) for x in inner.splitlines()), default=0)
        flag = "  <-- 疑似混入正文" if longest > 110 and not two_col else ""

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
            "nonwhite": round(frac, 3), "max_line_len": longest, "cross_page": crossed,
        })
        print(f"  图 {label:<3} p{page_no:<3} y{rect.y0:>5.0f}-{rect.y1:<5.0f} "
              f"{pix.width}x{pix.height} 非白={frac:>4.0%} 最长行={longest}{flag}")

    with open(os.path.join(args.out_dir, "figures.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    labels = sorted({int(m["label"]) for m in manifest if str(m["label"]).isdigit()})
    print(f"\n共导出 {len(manifest)} 张图 -> {args.out_dir}")
    if labels:
        missing = [i for i in range(1, max(labels) + 1) if i not in labels]
        print(f"图号范围 1..{max(labels)}，缺失: {missing if missing else '无'}")


if __name__ == "__main__":
    main()
