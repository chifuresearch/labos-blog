# -*- coding: utf-8 -*-
"""
labos-blog / builder.py
=======================
BlogBuilder:Blog Builder Agent —— 把日報 Markdown 變成一張張靜態
blog 頁面,先在本地檢視版面,之後整個資料夾就是 GitHub Pages 站台。

輸入:posts/*.md (帶 YAML front-matter:title/date/tags/cover)
輸出:site/index.html (卡片牆 + tag 篩選) + site/posts/<slug>.html

- 內文用 marked.js 在瀏覽器端渲染 (builder 零依賴、離線可跑)
- 亮/暗色自動切換,行動裝置優先
- sync_reports():把 labos-evaluation/data/reports/*.md 撿進 posts/

用法:
  python builder.py            # 只重建 site/
  python builder.py --sync     # 先同步日報再重建
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).parent
POSTS = HERE / "posts"
SITE = HERE / "docs"   # GitHub Pages 從 main /docs 部署
SITE_NAME = "iiaHUB"
SITE_DESC = "iiaHUB — 研究日報與教學策展:數位建築 × AI × 哲學 × 建築"

MARKED_CDN = "https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"

# ── 共用樣式 (亮/暗自動) ────────────────────────────────────
CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;
 --ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--accent:#2a78d6;
 --border:rgba(11,11,11,.1)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;
 --surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;
 --accent:#3987e5;--border:rgba(255,255,255,.1)}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);line-height:1.75;
 font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:0 18px 60px}
header.site{padding:34px 0 6px}
header.site h1{margin:0;font-size:26px}
header.site h1 a{color:var(--ink);text-decoration:none}
header.site p{margin:6px 0 0;color:var(--muted);font-size:13.5px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 6px}
.chip{border:1px solid var(--border);background:var(--surface);
 color:var(--ink2);border-radius:999px;padding:3px 13px;font-size:13px;
 cursor:pointer;user-select:none}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
 gap:16px;margin-top:14px}
.card{background:var(--surface);border:1px solid var(--border);
 border-radius:14px;overflow:hidden;transition:transform .15s,box-shadow .15s}
.card:hover{transform:translateY(-4px);box-shadow:0 10px 22px rgba(0,0,0,.12)}
.card a{color:inherit;text-decoration:none}
.card .cover{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;
 background:var(--grid)}
.card .body{padding:12px 15px 15px}
.card .date{font-size:12px;color:var(--muted)}
.card h2{font-size:16.5px;margin:4px 0 8px;line-height:1.45}
.card .tags{display:flex;flex-wrap:wrap;gap:5px}
.card .tag{font-size:11px;color:var(--accent);border:1px solid var(--border);
 border-radius:999px;padding:1px 8px}
article{background:var(--surface);border:1px solid var(--border);
 border-radius:16px;padding:28px clamp(18px,4vw,44px);margin-top:16px}
article img{max-width:100%;border-radius:10px}
article h1{font-size:26px;line-height:1.4}
article h2{font-size:20px;border-bottom:1px solid var(--grid);
 padding-bottom:6px;margin-top:34px}
article h3{font-size:16.5px;margin-top:26px}
article a{color:var(--accent)}
article code{background:var(--page);border:1px solid var(--border);
 border-radius:6px;padding:1px 6px;font-size:.88em}
article blockquote{border-left:3px solid var(--accent);margin:0;
 padding:2px 16px;color:var(--ink2)}
.postmeta{color:var(--muted);font-size:13px;margin:14px 0 0}
.backlink{display:inline-block;margin:22px 0 0;color:var(--accent);
 text-decoration:none;font-size:14px}
footer{margin-top:44px;color:var(--muted);font-size:12.5px;text-align:center}
"""


class BlogBuilder:
    """把 posts/*.md 生成靜態 blog 站台。"""

    def __init__(self) -> None:
        POSTS.mkdir(exist_ok=True)
        (SITE / "posts").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    @staticmethod
    def parse_front_matter(text: str) -> dict:
        meta = {"title": "", "date": "", "tags": [], "cover": "", "body": text}
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not m:
            return meta
        meta["body"] = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if k == "tags":
                meta["tags"] = [t.strip().strip("'\"")
                                for t in v.strip("[]").split(",") if t.strip()]
            elif k in ("title", "date", "cover"):
                meta[k] = v.strip("'\"")
        return meta

    # ------------------------------------------------------------------
    def sync_reports(self) -> int:
        """把 labos-evaluation 的日報撿進 posts/ (已存在的跳過)。"""
        src = HERE.parent / "labos-evaluation" / "data" / "reports"
        n = 0
        if src.exists():
            for f in sorted(src.glob("*.md")):
                dest = POSTS / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                    n += 1
                    print(f"  📥 同步日報 {f.name}")
        return n

    # ------------------------------------------------------------------
    def load_posts(self) -> List[dict]:
        posts = []
        for f in sorted(POSTS.glob("*.md"), reverse=True):
            meta = self.parse_front_matter(f.read_text(encoding="utf-8"))
            meta["slug"] = f.stem
            meta["title"] = meta["title"] or f.stem
            meta["date"] = meta["date"] or f.stem[:10]
            posts.append(meta)
        return posts

    # ------------------------------------------------------------------
    def build(self) -> Optional[Path]:
        posts = self.load_posts()
        if not posts:
            print("posts/ 是空的:先 --sync 或放一篇 .md 進去")
            return None
        for i, p in enumerate(posts):
            newer = posts[i - 1] if i > 0 else None
            older = posts[i + 1] if i < len(posts) - 1 else None
            self._write_post(p, newer, older)
        self._write_index(posts)
        print(f"📰 已生成 {len(posts)} 篇文章 + 首頁 → {SITE / 'index.html'}")
        return SITE / "index.html"

    # ------------------------------------------------------------------
    def _write_post(self, p: dict, newer, older) -> None:
        nav = ""
        if newer:
            nav += (f'<a class="backlink" href="{newer["slug"]}.html">'
                    f'← 較新:{html.escape(newer["title"][:20])}…</a> ')
        if older:
            nav += (f'<a class="backlink" style="float:right" '
                    f'href="{older["slug"]}.html">'
                    f'較舊:{html.escape(older["title"][:20])}… →</a>')
        tags_html = " ".join(
            f'<span class="tag">#{html.escape(t)}</span>' for t in p["tags"])
        doc = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(p['title'])} · {SITE_NAME}</title>
<style>{CSS}</style>
<script src="{MARKED_CDN}"></script></head>
<body><div class="wrap">
<header class="site"><h1><a href="../index.html">⬡ {SITE_NAME}</a></h1></header>
<article>
  <div class="postmeta">{p['date']}&ensp;<span class="card">
  </span><span class="tags" style="display:inline-flex;gap:5px">{tags_html}</span></div>
  <div id="content"></div>
</article>
<a class="backlink" href="../index.html">← 回到全部文章</a> {nav}
<footer>由 Lab OS Blog Builder 自動生成</footer>
</div>
<script type="text/plain" id="md">{html.escape(p['body'])}</script>
<script>
  const raw = document.getElementById('md').textContent;
  document.getElementById('content').innerHTML = marked.parse(raw);
</script>
</body></html>"""
        (SITE / "posts" / f"{p['slug']}.html").write_text(doc, encoding="utf-8")

    # ------------------------------------------------------------------
    def _write_index(self, posts: List[dict]) -> None:
        all_tags: List[str] = []
        for p in posts:
            for t in p["tags"]:
                if t not in all_tags:
                    all_tags.append(t)
        cards_data = [{"slug": p["slug"], "title": p["title"],
                       "date": p["date"], "tags": p["tags"],
                       "cover": p["cover"]} for p in posts]
        doc = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{SITE_NAME}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header class="site"><h1><a href="index.html">⬡ {SITE_NAME}</a></h1>
<p>{SITE_DESC}</p></header>
<div class="chips" id="chips">
  <span class="chip on" data-tag="">全部</span>
  {''.join(f'<span class="chip" data-tag="{html.escape(t)}">#{html.escape(t)}</span>'
           for t in all_tags)}
</div>
<div class="grid" id="grid"></div>
<footer>由 Lab OS Blog Builder 自動生成 · 共 {len(posts)} 篇</footer>
</div>
<script>
  const POSTS = {json.dumps(cards_data, ensure_ascii=False)};
  const grid = document.getElementById('grid');
  function esc(s) {{ const d = document.createElement('div');
    d.textContent = s; return d.innerHTML; }}
  function render(tag) {{
    grid.innerHTML = POSTS
      .filter(p => !tag || p.tags.includes(tag))
      .map(p => `<div class="card"><a href="posts/${{p.slug}}.html">
        ${{p.cover ? `<img class="cover" loading="lazy" src="${{p.cover}}"
                       onerror="this.remove()">` : ''}}
        <div class="body"><div class="date">${{p.date}}</div>
        <h2>${{esc(p.title)}}</h2>
        <div class="tags">${{p.tags.slice(0,5).map(t =>
          `<span class="tag">#${{esc(t)}}</span>`).join('')}}</div>
        </div></a></div>`).join('');
  }}
  document.getElementById('chips').addEventListener('click', (e) => {{
    const c = e.target.closest('.chip'); if (!c) return;
    document.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
    c.classList.add('on'); render(c.dataset.tag);
  }});
  render('');
</script>
</body></html>"""
        (SITE / "index.html").write_text(doc, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Lab OS Blog Builder")
    ap.add_argument("--sync", action="store_true",
                    help="先從 labos-evaluation 同步日報")
    args = ap.parse_args()
    b = BlogBuilder()
    if args.sync:
        b.sync_reports()
    b.build()


if __name__ == "__main__":
    main()
