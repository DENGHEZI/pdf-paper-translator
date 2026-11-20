#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把论文里的表格整张裁成图片（用于几十行的大数值表，避免转写数字出错）。

典型场景：CVPR/arXiv 论文的 benchmark 表有 6–11 列、20–40 行，
文本层抽取列错位，手工转写既慢又易错；而表里的模型名与数字本身不需要翻译。

做法：
1. 找到「Table N.」题注块；
2. 判断题注在表格**上方**还是**下方**（多数期刊在下、多数 CVPR 在上）；
3. 按栏位（双栏论文要分栏）从题注向外扩张，按「行距连续（gap < 20pt）」回溯到表格顶端／底端，
   遇到上一个题注或长段落即停；
4. 整块渲染为 PNG。

注意：表题可能在表格**上方**（多数 CVPR）或**下方**（多数期刊）。脚本用间距自动判断，
猜错时用 `--caption-side above|below` 强制指定。

用法:
    python extract_tables.py --pdf INPUT.pdf --out-dir work/figures [--dpi 200]
    python extract_tables.py --pdf INPUT.pdf --caption-side below --only 2,3,4
产物:
    tab_<n>.png    每张表
    tables.json    清单：label / page / rect / 题注位置
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

CAP = re.compile(r"^\s*Table\s+(\d+)\s*[.:]")
ROW_GAP = 20.0          # 表内行距上限；超过则认为表格结束
PARA_MIN_LEN = 220      # 视为“正文段落”的最小长度


def text_blocks(page):
    """产出 (bbox, first_line)。"""
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
        first = next((x for x in lines if x), "")
        if first:
            yield fitz.Rect(b["bbox"]), first, " ".join(lines)


def find_captions(doc):
    caps = []
    for pno in range(doc.page_count):
        for r, first, _ in text_blocks(doc[pno]):
            m = CAP.match(first)
            if m:
                caps.append((pno, int(m.group(1)), r, first))
    return caps


def table_region(page, cap_rect, cap_first, page_w, side="auto"):
    """返回该表格的裁切矩形（自动判断题注在上还是在下、并按栏位收窄）。"""
    x0, x1 = max(0, cap_rect.x0 - 2), min(page_w, cap_rect.x1 + 2)
    blocks = [(r, first, whole) for r, first, whole in text_blocks(page)]

    def in_col(r):
        return min(r.x1, x1) - max(r.x0, x0) > 4

    above = [(r, first, whole) for r, first, whole in blocks
             if in_col(r) and r.y1 <= cap_rect.y0 - 2]
    below = [(r, first, whole) for r, first, whole in blocks
             if in_col(r) and r.y0 >= cap_rect.y1 + 2]

    # 判断题注在表上还是表下：比两侧内容与题注的间距，近的一侧就是表格
    if side == "below":
        cap_below = True                    # 表题在表格下方
    elif side == "above":
        cap_below = False                   # 表题在表格上方
    else:
        d_up = cap_rect.y0 - max((r.y1 for r, _, _ in above), default=0)
        d_down = min((r.y0 for r, _, _ in below), default=1e9) - cap_rect.y1
        cap_below = d_up <= d_down

    seq = sorted(above, key=lambda t: t[0].y1) if cap_below else sorted(below, key=lambda t: t[0].y0)

    if cap_below:                                   # 表格在题注上方，向上扩张
        top, bot = cap_rect.y0, cap_rect.y0
        cur = top
        for r, first, whole in reversed(seq):
            if CAP.match(first):                    # 遇到上一张表的题注
                break
            if len(whole) > PARA_MIN_LEN and whole.count(". ") >= 1:
                break                               # 遇到正文段落
            if cur - r.y1 < ROW_GAP:
                top = r.y0
                cur = r.y0
            else:
                break
        return fitz.Rect(x0, max(40, top - 2), x1, bot - 2)

    # 题注在表上方（CVPR 风格）：向下扩张
    top, bot = cap_rect.y0, cap_rect.y1
    cur = bot
    for r, first, whole in seq:
        if CAP.match(first):
            break
        if len(whole) > PARA_MIN_LEN and whole.count(". ") >= 1:
            break
        if r.y0 - cur < ROW_GAP:
            bot = r.y1
            cur = r.y1
        else:
            break
    return fitz.Rect(x0, top, x1, min(page_w, bot + 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", default="work/figures")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--only", default="", help="只处理这些表号，逗号分隔，如 2,3,4")
    ap.add_argument("--caption-side", default="auto", choices=["auto", "below", "above"],
                    help="表题相对表格的位置；auto 靠间距猜测，猜错时手动指定")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    doc = fitz.open(args.pdf)
    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None

    caps = find_captions(doc)
    if not caps:
        sys.exit("未找到任何 “Table N.” 题注")

    manifest = []
    seen = set()
    for pno, num, rect, first in caps:
        if num in seen:
            continue
        if only and num not in only:
            continue
        seen.add(num)
        page = doc[pno]
        region = table_region(page, rect, first, page.rect.width, args.caption_side)
        if region.height < 30 or region.width < 60:
            print(f"  ! 表 {num}: 区域异常 ({region.width:.0f}x{region.height:.0f})，跳过")
            continue
        pix = page.get_pixmap(clip=region, dpi=args.dpi)
        path = os.path.join(args.out_dir, f"tab_{num}.png")
        pix.save(path)
        manifest.append({
            "label": str(num), "page": pno + 1,
            "rect": [round(v, 1) for v in region],
            "caption": first[:120],
            "file": os.path.basename(path),
        })
        print(f"  表 {num:<3} p{pno + 1:<3} "
              f"x{region.x0:.0f}-{region.x1:.0f} y{region.y0:.0f}-{region.y1:.0f} "
              f"{pix.width}x{pix.height}")

    with open(os.path.join(args.out_dir, "tables.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n共导出 {len(manifest)} 张表 -> {args.out_dir}")
    print("提示：表格图片用 content.json 的 [\"pic\", \"<n>\", \"表 n 中文表题\"] 块插入。")


if __name__ == "__main__":
    main()
