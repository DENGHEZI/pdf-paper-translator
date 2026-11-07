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
MIN_CAPTION_W = 100.0


# ---------------------------------------------------------------- 基础工具
def bbox_of(page, b):
    """块/图 bbox → 显示空间（处理 /Rotate）。"""
    r = fitz.Rect(b["bbox"])
    if page.rotation:
        r = r * page.rotation_matrix
    return r


CAP_INLINE = re.compile(r"(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*([:：.])?\s*(.*)$", re.I)


def match_caption_ex(line):
    """返回图号；若这行其实是正文（如 “Figure 1 shows …”），返回 None。

    两种写法都支持：
    - 行首：“Figure 3: Transfer to ImageNet …”
    - 行内前 40 字符：“a. b. c. d. Figure 3: Sample images …”（子图标签并到同一块）
    """
    m = CAP_HEAD.match(line)
    if not m:
        # 行内匹配只允许“子图标签 + 图注”的情形（如 “a. b. c. d. Figure 3: …”），
        # 否则正文里的 “… as seen in Figure 6.” 会被当成图注
        m2 = CAP_INLINE.search(line)
        if not m2 or m2.start() >= 40:
            return None, None
        prefix = line[:m2.start()].strip()
        if prefix and not is_panel_prefix([prefix]):
            return None, None
        m = m2
    sep, rest = m.group(2), m.group(3).strip()
    if rest and rest[0].isascii() and rest[0].islower():
        return None, None               # Figure 1 shows / illustrates / depicts
    if not sep and rest.startswith("("):
        return None, None               # Figure 4(A) is increased …
    if rest and rest[0] in ",;)]":
        return None, None               # Figure 13, present … / Figure 5) …
    return m.group(1), rest


def match_caption(line):
    """兼容旧接口：只要图号。"""
    return match_caption_ex(line)[0]


PANEL_RE = re.compile(r"^(?:\([A-Za-z0-9]{1,2}\)|[A-Za-z0-9]{1,2}[.)])$")


def is_panel_prefix(lines):
    """前置行是否只是 “a. b. c. d.” / “(a) (b)” 这类子图标签。"""
    toks = []
    for ln in lines:
        t = ln.strip()
        if not t:
            continue
        toks += [x for x in re.split(r"\s+", t) if x]
    return bool(toks) and len(toks) <= 8 and all(PANEL_RE.match(t) for t in toks)


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
        ph = page.rect.height
        for r, _ in page_text_boxes(page):
            if r.x0 < pw * 0.05:
                continue
            if r.y1 < 55 or r.y0 > ph - 45:      # 页眉/页脚不参与栏数判定
                continue
            if r.x1 <= mid + 2:
                left += [r.x0, r.x1]
            elif r.x0 >= mid - 2:
                right += [r.x0, r.x1]
    # 跨中线的宽块（通栏正文）≥3 条 → 单栏；图内标签会fake出左右两簇
    crossing = 0
    for pno in range(min(doc.page_count, sample)):
        page = doc[pno]
        for r, _ in page_text_boxes(page):
            if r.y1 < 55 or r.y0 > page.rect.height - 45:
                continue                          # 页眉/页脚不算通栏正文
            if r.x0 < mid - 40 and r.x1 > mid + 40 and r.width > page.rect.width * 0.5:
                crossing += 1
    if crossing >= 3:
        return False, (lo, hi), (lo, hi)

    if len(left) > 16 and len(right) > 16:
        return True, (min(left), max(left)), (min(right), max(right))
    return False, (lo, hi), (lo, hi)


def scan(page, col_w):
    """返回 (captions, body_bottoms, table_bottoms)。

    captions = [(label, y0, y1, x0, x1)]
    图注判定要同时满足：
      - 首个**非空行**（或其后的纯子图标签行）以 Figure N 开头；
      - 编号后有说明文字（"Figure 9." 这种句末引用不算）；
      - 说明首词不是小写动词（"Figure 1 shows …" 是正文）。
    """
    caps, body, tables = [], [], []
    for r, lines in page_text_boxes(page):
        w, h = r.width, r.height
        body_lines = [ln for ln in lines if ln]
        if not body_lines:
            continue

        label = None
        if h < MAX_CAPTION_H and w > MIN_CAPTION_W:
            for i, ln in enumerate(body_lines):
                if i == 0:
                    lab, rest = match_caption_ex(ln)
                else:
                    # 只允许 “a. b. c. d.” 这类子图标签打头，再接图注
                    lab, rest = (None, None)
                    if is_panel_prefix(body_lines[:i]):
                        lab, rest = match_caption_ex(ln)
                    if lab and not rest:
                        lab = None
                if lab:
                    label = lab
                    break
        if label and not (len(body_lines) == 1 and not match_caption_ex(body_lines[0])[1]):
            caps.append((label, r.y0, r.y1, r.x0, r.x1))
            continue
        label = None

        first = body_lines[0]
        joined = " ".join(body_lines)

        if TABLE_HEAD.match(first):                      # 表注
            tables.append(r.y1)
            continue

        nums = re.findall(r"\d+(?:\.\d+)?", joined)     # 表格数据行
        if w >= col_w * 0.50 and len(nums) >= 4 and 20 <= len(joined) <= 600:
            tables.append(r.y1)
            continue

        if w >= col_w * 0.72 and len(joined) >= 80:      # 正文段落
            body.append(r.y1)

    caps.sort(key=lambda x: x[1])
    return caps, body, tables


def figure_x_range(page, top, cy0, cx0, cx1, col_x0, col_x1):
    """按图注的水平范围去找它自己的图，避免把并排的另一张图裁进来。

    做法：取 [top, cy0] 竖带内、与图注 x 范围有水平重叠的图形/图像，
    求其 x 外接框并夹到本栏范围内。
    """
    rects = []
    for dr in page.get_drawings():
        r = dr["rect"]
        if page.rotation:
            r = r * page.rotation_matrix
        if r.y1 <= cy0 + 2 and r.y0 >= top - 4 and r.width > 2 and r.height > 2:
            rects.append(r)
    for im in page.get_image_info():
        r = fitz.Rect(im["bbox"])
        if page.rotation:
            r = r * page.rotation_matrix
        if r.y1 <= cy0 + 2 and r.y0 >= top - 4 and r.width > 2 and r.height > 2:
            rects.append(r)

    cand = [r for r in rects if min(r.x1, cx1) - max(r.x0, cx0) > 2]
    if not cand:
        return col_x0, col_x1
    x0 = max(col_x0, min(r.x0 for r in cand) - 2)
    x1 = min(col_x1, max(r.x1 for r in cand) + 2)
    if x1 - x0 < 40:
        return col_x0, col_x1
    return x0, x1


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
        prev_top = -1.0          # 上一图注的 y0，用于识别“左右并排”
        for label, cy0, cy1, cx0, cx1 in caps:
            x0, x1 = (right_col if (two_col and (cx0 + cx1) / 2 > mid) else left_col)

            # 若本图注与上一图注在竖直方向重叠 → 二者是并排的，不能用 prev 压下界
            same_row = (prev_top >= 0 and cy0 < prev + 4 and abs(cy0 - prev_top) < 60)
            prev_eff = PAGE_MARGIN_TOP if same_row else prev
            above = [y for y in (body + tables) if y <= cy0 - 2]
            top = max([PAGE_MARGIN_TOP, prev_eff] + above)
            if top > cy0 - 40:
                top = max(prev_eff, PAGE_MARGIN_TOP)

            # 位图型插图：从本图最上面那张位图的顶端开始，避免把上方正文裁进来
            imgs_here = []
            for im in page.get_image_info():
                r = fitz.Rect(im["bbox"])
                if page.rotation:
                    r = r * page.rotation_matrix
                if r.width < 8 or r.height < 8:
                    continue
                if r.y1 <= cy0 - 2 and r.y1 > prev_eff + 2 and \
                   min(r.x1, cx1) - max(r.x0, cx0) > 2:
                    imgs_here.append(r)
            if imgs_here:
                top = max(top, min(r.y0 for r in imgs_here) - 4)
            fx0, fx1 = figure_x_range(page, top, cy0, cx0, cx1, x0, x1)
            rect = fitz.Rect(fx0, top + 2, fx1, cy0 - 2)

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
            prev_top = cy0
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
