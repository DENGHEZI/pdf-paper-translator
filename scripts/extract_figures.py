#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按图注锚定抽取论文插图（整块裁切渲染），处理真实论文里的四种坑：

1. 图注是居中窄块（宽 170~390pt）——宽度阈值必须放小到 ~120；
2. 正文里出现 “Figure 1 shows…” 开头的段落——用「块高 < 45pt」排除；
3. 图内文字与正文同字号——不能靠字号区分，只能靠宽度+长度；
4. 图跨页（图在上页底部、图注在下页顶部）——自动回退到上一页。

用法:
    python extract_figures.py --pdf INPUT.pdf --out-dir work/figures [--dpi 250]
产物:
    fig_<label>.png     每张图（250dpi）
    figures.json        清单：label / page / rect / size / 用于核对的非白像素占比
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

CAP_RE = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*[:.]?\s", re.I)
PAGE_MARGIN_TOP = 55.0


def content_span(doc, sample=6):
    """由文本块推内容区左右边界。"""
    xs = []
    for pno in range(min(doc.page_count, sample)):
        for b in doc[pno].get_text("dict")["blocks"]:
            if b["type"] == 0:
                xs += [b["bbox"][0], b["bbox"][2]]
    if not xs:
        return 60.0, doc[0].rect.width - 60
    xs.sort()
    lo = xs[max(0, int(len(xs) * 0.02))]
    hi = xs[min(len(xs) - 1, int(len(xs) * 0.98))]
    return lo - 4, hi + 4


def scan(page):
    """返回 (captions, body_bottoms)。captions = [(label, y0, y1)]

    注意：图注块的首行可能是**空行**（排版把编号前的空行也算进块里），
    所以要扫描块内任意一行，而不是只看 lines[0]。
    """
    caps, body = [], []
    cw = page.rect.width
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        h = b["bbox"][3] - b["bbox"][1]
        w = b["bbox"][2] - b["bbox"][0]
        lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
        if not lines:
            continue
        label = None
        for ln in lines:
            if not ln:
                continue
            m = CAP_RE.match(ln)
            if m:
                label = m.group(1)
                break
        if label and h < 45 and w > 120:
            caps.append((label, b["bbox"][1], b["bbox"][3]))
            continue
        if w > cw * 0.70 and len(" ".join(lines)) >= 80:
            body.append(b["bbox"][3])
    caps.sort(key=lambda x: x[1])
    return caps, body


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
    lo, hi = content_span(doc)
    left = args.left if args.left is not None else lo
    right = args.right if args.right is not None else hi
    print(f"内容区 x: {left:.0f} - {right:.0f}")

    results = []
    for pno in range(doc.page_count):
        page = doc[pno]
        caps, body = scan(page)
        prev = PAGE_MARGIN_TOP
        for label, cy0, cy1 in caps:
            above = [y for y in body if y <= cy0 - 2]
            top = max([PAGE_MARGIN_TOP, prev] + above)
            if top > cy0 - 40:
                top = max(prev, PAGE_MARGIN_TOP)
            rect = fitz.Rect(left, top + 2, right, cy0 - 2)

            if rect.height < 40 and pno > 0 and not args.no_cross_page:
                # 跨页：图在上一页底部
                prev_page = doc[pno - 1]
                _, body_prev = scan(prev_page)
                img_rects = [im["bbox"] for im in prev_page.get_image_info()]
                if img_rects:
                    last_y0 = min(r[1] for r in img_rects if r[1] > 200)
                    tb = [y for y in body_prev if y <= last_y0 - 2]
                    t2 = max([PAGE_MARGIN_TOP] + tb)
                    bottom = prev_page.rect.height - 20
                    # 避开页脚（页码 / 版权行）：底部 12% 区域内的高度很小的文本块
                    h = prev_page.rect.height
                    for b in prev_page.get_text("dict")["blocks"]:
                        if b["type"] != 0 or b["bbox"][1] < h * 0.88:
                            continue
                        if (b["bbox"][3] - b["bbox"][1]) < 40:
                            bottom = min(bottom, b["bbox"][1] - 2)
                    rect = fitz.Rect(left, t2 + 2, right, bottom)
                    # (label, 图注所在页 1-based, 渲染页索引 0-based, rect, crossed)
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
        flag = "  <-- 疑似混入正文" if longest > 110 else ""

        # 非白像素占比，用于识别空白裁切
        sm = pix.samples
        n = len(sm) // 3
        step = max(1, n // 4000)
        nw = sum(1 for k in range(0, n, step)
                 if sm[k * 3] < 245 or sm[k * 3 + 1] < 245 or sm[k * 3 + 2] < 245)
        frac = nw / max(1, n // step)

        manifest.append({
            "label": label, "page": page_no,
            "rect": [round(v, 1) for v in rect],
            "file": os.path.basename(path),
            "dpi": args.dpi, "nonwhite": round(frac, 3),
            "max_line_len": longest, "cross_page": crossed,
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
