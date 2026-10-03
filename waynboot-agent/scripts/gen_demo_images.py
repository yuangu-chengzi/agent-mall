"""生成本地演示用的商品图片，把 mall 的 `E:\\waynshop\\webp` 填满。

**为什么需要**：上游仓库只带数据库，不带图片文件。本机 `/upload/*.webp` 全部 404，
商品卡片、轮播、分类图标全是占位图——页面能跑，但看不出"这是个商城"。
本脚本按数据库里真实引用到的文件名，**逐个生成**一张风格统一的图（渐变底 + 文字），
所以前端所有位置都能显示真实图片，不需要改任何前端代码。

**做法**：
  * 商品主图 / 相册 / SKU 图 → 600×600，柔和渐变 + 商品名（相册带"细节 n"、SKU 带规格值）；
  * 分类图标 / 金刚区 / 头像 → 200×200 圆形，取名字首字；
  * 轮播 → 1200×525，带标题；
  * 图文详情图（7551 张，占了引用总数的 84%）→ **不逐张生成**，改为把商品 detail 里
    的图片地址统一指到 6 张共享演示图（`--repoint-detail`，默认开启）。

用法::

    # 生成到默认目录（D:/waynshop/webp），已存在的不重生成
    .venv/Scripts/python.exe scripts/gen_demo_images.py
    # 只生成前 50 张试试效果
    .venv/Scripts/python.exe scripts/gen_demo_images.py --limit 50
    # 换目录 / 强制重画
    .venv/Scripts/python.exe scripts/gen_demo_images.py --out E:/waynshop/webp --force

连的是 mall 的 `application-dev.yml` 里的库（不额外保存口令）。

⚠️ mall 默认的上传目录是 `E:\\waynshop\\webp`（本机没有 E 盘），启动应用时要指一下::

    UPLOAD_DIR=D:/waynshop/webp java -jar jars/waynboot-mobile-api.jar
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

import pymysql
from PIL import Image, ImageDraw, ImageFont

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
MALL_ROOT = ROOT.parent
DEV_YML = MALL_ROOT / "waynboot-common/src/main/resources/application-dev.yml"

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]

#: 图文详情用的共享图（生成 6 张，所有商品的 detail 都指过来）
SHARED_DETAIL = [f"/upload/demo/detail-{i}.webp" for i in range(1, 7)]


# --------------------------------------------------------------------------- #
# 配色：按名字哈希挑一组柔和的渐变，保证同一商品每次生成颜色一致
# --------------------------------------------------------------------------- #
PALETTE = [
    ((255, 236, 230), (255, 205, 190)),
    ((232, 240, 254), (200, 220, 252)),
    ((233, 246, 238), (198, 230, 214)),
    ((252, 243, 227), (247, 224, 185)),
    ((243, 237, 251), (219, 206, 246)),
    ((235, 244, 247), (203, 228, 235)),
    ((252, 238, 243), (246, 209, 224)),
    ((239, 241, 245), (214, 219, 228)),
]


def pick_colors(seed: str):
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return PALETTE[int(digest[:8], 16) % len(PALETTE)], digest


def gradient(size: tuple[int, int], colors) -> Image.Image:
    """竖向渐变底（够用且比径向渐变快很多）。"""
    top, bottom = colors
    w, h = size
    base = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        base.putpixel(
            (0, y),
            tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return base.resize(size)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap(text: str, per_line: int, max_lines: int = 3) -> str:
    text = (text or "").strip()
    lines = [text[i : i + per_line] for i in range(0, len(text), per_line)][:max_lines]
    return "\n".join(lines)


def centered_text(draw: ImageDraw.ImageDraw, box, text: str, font, fill, spacing=8):
    """在多行文本块里居中绘制。"""
    x0, y0, x1, y1 = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        ((x0 + x1 - tw) / 2 - bbox[0], (y0 + y1 - th) / 2 - bbox[1]),
        text,
        font=font,
        fill=fill,
        spacing=spacing,
        align="center",
    )


def save_webp(img: Image.Image, path: Path, quality: int = 78) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=4)
    path.write_bytes(buf.getvalue())
    return len(buf.getvalue())


# --------------------------------------------------------------------------- #
# 四类图
# --------------------------------------------------------------------------- #
def make_product(name: str, seed: str, size=(600, 600), tag: str = "") -> Image.Image:
    colors, _ = pick_colors(seed)
    img = gradient(size, colors)
    draw = ImageDraw.Draw(img)
    w, h = size

    # 装饰：两个半透明圆，避免大色块太空
    overlay = Image.new("RGBA", size, (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse((w * 0.62, -h * 0.18, w * 1.25, h * 0.45), fill=(255, 255, 255, 46))
    od.ellipse((-w * 0.28, h * 0.68, w * 0.35, h * 1.3), fill=(255, 255, 255, 38))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    centered_text(
        draw, (w * 0.1, h * 0.3, w * 0.9, h * 0.68), wrap(name, 8, 3), load_font(int(w * 0.075)), (60, 64, 72)
    )
    if tag:
        centered_text(
            draw, (w * 0.1, h * 0.7, w * 0.9, h * 0.84), tag, load_font(int(w * 0.045)), (120, 126, 136)
        )
    centered_text(
        draw, (w * 0.1, h * 0.86, w * 0.9, h * 0.95), "wayn 商城", load_font(int(w * 0.035)), (150, 155, 165)
    )
    return img


def make_icon(name: str, seed: str, size=(200, 200), circle=True) -> Image.Image:
    colors, _ = pick_colors(seed)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((0, 0, w - 1, h - 1), fill=colors[1] + (255,))
    d.ellipse((w * 0.16, h * 0.16, w * 0.84, h * 0.84), fill=colors[0] + (255,))
    img = Image.alpha_composite(img, layer)
    draw = ImageDraw.Draw(img)
    char = (name or "?").strip()[:1]
    centered_text(draw, (0, h * 0.06, w, h * 0.94), char, load_font(int(w * 0.42)), (90, 96, 106))
    return img


def make_banner(title: str, seed: str, size=(1200, 525)) -> Image.Image:
    colors, _ = pick_colors(seed)
    img = gradient(size, colors)
    w, h = size
    overlay = Image.new("RGBA", size, (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse((w * 0.55, -h * 0.5, w * 1.15, h * 0.75), fill=(255, 255, 255, 52))
    od.ellipse((-w * 0.1, h * 0.55, w * 0.35, h * 1.4), fill=(255, 255, 255, 40))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    centered_text(
        draw,
        (w * 0.1, h * 0.25, w * 0.62, h * 0.75),
        wrap(title or "wayn 商城", 7, 2),
        load_font(int(h * 0.16)),
        (72, 76, 86),
    )
    return img


def make_detail(index: int, size=(750, 750)) -> Image.Image:
    """图文详情用的共享图：浅底 + 一条说明，不喧宾夺主。"""
    tones = [((250, 250, 251), (238, 240, 244)), ((250, 249, 246), (240, 236, 228))]
    img = gradient(size, tones[index % len(tones)])
    w, h = size
    draw = ImageDraw.Draw(img)
    overlay = Image.new("RGBA", size, (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        (w * 0.18, h * 0.28, w * 0.82, h * 0.72), radius=int(w * 0.05), fill=(255, 255, 255, 210)
    )
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    centered_text(
        draw,
        (w * 0.2, h * 0.32, w * 0.8, h * 0.68),
        f"图文详情 {index + 1}\n（演示用占位图）",
        load_font(int(w * 0.065)),
        (128, 133, 142),
    )
    centered_text(
        draw, (w * 0.2, h * 0.74, w * 0.8, h * 0.82), "wayn 商城", load_font(int(w * 0.04)), (176, 180, 188)
    )
    return img


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def connect():
    """复用 mall 的开发配置连库（口令只存在 yml 一处）。"""
    cfg = DEV_YML.read_text(encoding="utf-8")
    m = re.search(r"url:\s*jdbc:mysql://([\d.]+):(\d+)/(\w+)", cfg)
    user = re.search(r"username:\s*(\S+)", cfg).group(1)
    pwd = re.search(r"password:\s*(\S+)", cfg).group(1)
    return pymysql.connect(
        host=m.group(1), port=int(m.group(2)), user=user, password=pwd, database=m.group(3), charset="utf8mb4"
    )


def collect_jobs(cur) -> list[tuple[str, str, str, str]]:
    """返回 [(相对路径, 类型, 正文, 角标)]，类型决定用哪个绘制函数。"""
    jobs: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    def add(path: str, kind: str, text: str, tag: str = "") -> None:
        if not path:
            return
        # 有些引用带查询串（`xxx.webp?quality=90&thumbnail=200x200&imageView`）：
        # mall 的静态资源处理器会忽略它，但直接当文件名会在 Windows 上报
        # `OSError: [Errno 22] Invalid argument`（`?` 不是合法文件名字符）。
        path = path.split("?")[0]
        if not path.startswith("/upload/") or path in seen:
            return
        seen.add(path)
        jobs.append((path, kind, text, tag))

    cur.execute("select name, pic_url, gallery from shop_goods")
    for name, pic, gallery in cur.fetchall():
        add(pic, "product", name)
        for i, u in enumerate(re.findall(r"/upload/[^\"')\s]+?\.webp", gallery or "")):
            add(u, "product", name, f"细节图 {i + 1}")

    cur.execute(
        "select g.name, p.url, p.specifications "
        "from shop_goods_product p join shop_goods g on g.id = p.goods_id"
    )
    for name, url, specs in cur.fetchall():
        # specifications 列存的是 JSON 数组字符串（如 ["1.5m床垫*1","浅杏粉"]）
        try:
            values = json.loads(specs) if specs else []
        except (TypeError, ValueError):
            values = [str(specs or "")]
        for u in re.findall(r"/upload/[^\"')\s]+?\.webp", url or ""):
            add(u, "product", name, " / ".join(str(v) for v in values))

    cur.execute("select pic_url from shop_goods_specification")
    for (u,) in cur.fetchall():
        add(u, "icon", "规格")

    cur.execute("select name, icon_url, pic_url from shop_category")
    for name, icon, pic in cur.fetchall():
        add(icon, "icon", name)
        add(pic, "product", name)

    cur.execute("select name, icon_url, pic_url from shop_diamond")
    for name, icon, pic in cur.fetchall():
        add(icon, "icon", name)
        add(pic, "product", name)

    cur.execute("select title, img_url from shop_banner")
    for title, url in cur.fetchall():
        add(url, "banner", title)

    cur.execute("select nickname, avatar from shop_member")
    for nickname, avatar in cur.fetchall():
        add(avatar, "icon", nickname or "客")

    for path in SHARED_DETAIL:
        add(path, "detail", "")
    return jobs


#: 商品图文详情里指向本地上传目录的 <img src="/upload/xxx.webp">
DETAIL_IMG_RE = re.compile(r'(<img[^>]+src=")/upload/[^"]+\.webp(")')


def repoint_detail(html: str) -> str:
    """把详情 HTML 里的图片按顺序换成共享演示图（第 1 张换 detail-1，第 2 张换 detail-2…）。"""
    used = 0

    def repl(match: re.Match) -> str:
        nonlocal used
        url = SHARED_DETAIL[used % len(SHARED_DETAIL)]
        used += 1
        return match.group(1) + url + match.group(2)

    return DETAIL_IMG_RE.sub(repl, html)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成演示商品图片")
    parser.add_argument("--out", default="D:/waynshop/webp", help="输出目录（mall 的 uploadDir）")
    parser.add_argument("--limit", type=int, default=0, help="只生成前 N 张（调试用）")
    parser.add_argument("--force", action="store_true", help="已存在的也重画")
    parser.add_argument(
        "--repoint-detail",
        dest="repoint",
        action="store_true",
        default=True,
        help="把商品 detail 里的图片地址指向共享演示图（默认开）",
    )
    parser.add_argument("--no-repoint-detail", dest="repoint", action="store_false")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    conn = connect()
    cur = conn.cursor()
    jobs = collect_jobs(cur)
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"待生成 {len(jobs)} 张 → {out}")
    made = skipped = 0
    total_bytes = 0
    for rel, kind, text, tag in jobs:
        # mall 把 /upload/** 映射到 {uploadDir}/，所以要去掉前缀里的 upload 这一层
        target = out / rel[len("/upload/") :].replace("/", os.sep)
        if target.exists() and not args.force:
            skipped += 1
            continue
        seed = rel.rsplit("/", 1)[-1]
        if kind == "product":
            img = make_product(text, seed, tag=tag)
        elif kind == "banner":
            img = make_banner(text, seed)
        elif kind == "detail":
            img = make_detail(SHARED_DETAIL.index(rel))
        else:
            img = make_icon(text, seed)
        total_bytes += save_webp(img, target)
        made += 1
        if made % 100 == 0:
            print(f"  …已生成 {made} 张", flush=True)

    print(f"生成 {made} 张（跳过已存在 {skipped} 张），本次写入 {total_bytes / 1024 / 1024:.1f} MB")

    if args.repoint:
        cur.execute("select id, detail from shop_goods where detail like '%/upload/%'")
        rows = cur.fetchall()
        changed = 0
        for goods_id, detail in rows:
            new_detail = repoint_detail(detail)
            if new_detail != detail:
                cur.execute("update shop_goods set detail=%s where id=%s", (new_detail, goods_id))
                changed += 1
        conn.commit()
        print(f"图文详情里的图片地址已指向共享演示图：{changed} 个商品")

    conn.close()
    print("完成。启动 mall 时记得带上 UPLOAD_DIR，例如：")
    print(f"  UPLOAD_DIR={out.as_posix()} java -jar jars/waynboot-mobile-api.jar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
