#!/usr/bin/env python3
"""把部落格的 B 線文章轉成 iThome 可以直接貼的 markdown。

iThome 不吃 Jekyll 的 Liquid，而且相對連結貼過去會壞掉。這支做四件事：
  1. 去掉 front matter，標題變成內文第一行
  2. {{ '/x/' | relative_url }} → https://ryanGTR.github.io/x/（絕對網址）
  3. {% include lab-env.html %} → 純文字的環境區塊
  4. 文末加導流：本篇原文、lab repo、系列目錄

用法：python3 scripts/make-ithome.py           # 全部 30 篇
      python3 scripts/make-ithome.py 3 4       # 只出 Day 3、Day 4
輸出：ithome/dayNN-<slug>.md
"""
import re, sys, pathlib

ROOT   = pathlib.Path(__file__).resolve().parent.parent
POSTS  = ROOT / "_posts"
OUT    = ROOT / "ithome"
SITE   = "https://ryanGTR.github.io"
REPO   = "https://github.com/ryanGTR/openshift-ai-30days"
MODEL  = "https://github.com/ryanGTR/llm-from-scratch"
SERIES = "OpenShift AI 入門 30 天"

LAB_ENV = f"""---

<details>
<summary>🧪 這篇的實驗環境</summary>

- **叢集**：CRC 2.63.0 · OpenShift 4.22.7 · Kubernetes v1.35.6
  單節點 13 vCPU / 40 GiB / 120 GB，**叢集內沒有 GPU**
- **Operator**：`opendatahub-operator.v3.5.0`（即 RHOAI 3.x 的上游開源版）、
  `cert-manager-operator.v1.20.0`（3.x 的必要相依，2.x 不需要）
- **開啟的元件**：`kserve`、`aipipelines`、`dashboard`、`workbenches`、`modelregistry`
- **叢集外**：Harbor v2.15.2（私有 registry）、MinIO（S3），跑在宿主的 podman 上
- **宿主**：Framework Laptop 16（Ryzen AI 7 350 · 8C/16T · 64 GB · 1 TB NVMe）

⚠️ **ODH ≠ RHOAI**：元件同源，但 namespace 與部分名稱不同
（這裡是 `opendatahub`，商用版是 `redhat-ods-*`）。**指令邏輯可照用，字串要自己對一次。**

</details>
"""


def convert(md: pathlib.Path) -> tuple[int, str, str]:
    raw = md.read_text()
    fm, body = raw.split("---", 2)[1], raw.split("---", 2)[2]

    title = re.search(r'^title: "(.*)"$', fm, re.M).group(1)
    day   = int(re.search(r"Day (\d+)", title).group(1))
    slug  = md.stem[11:]
    url   = f"{SITE}/2026/09/{slug}/"

    q = re.search(r'^feedback_question: "(.*)"$', fm, re.M)
    question = q.group(1) if q else ""

    # 正文結尾「---／**提問**／lab-env」這三段裡的提問與頁尾重複，去掉正文那份
    if question:
        body = re.sub(
            r"\n+---\n+\*\*" + re.escape(question) + r"\*\*\n+(?=\{%\s*include lab-env)",
            "\n\n", body)

    # 相對連結 → 絕對網址（Liquid 形式）
    body = re.sub(r"\{\{\s*'([^']+)'\s*\|\s*relative_url\s*\}\}", SITE + r"\1", body)
    body = re.sub(r"\{%\s*include lab-env\.html\s*%\}", LAB_ENV, body)
    # {% raw %} 只是給 Jekyll 看的，iThome 不需要
    body = re.sub(r"\{%\s*(end)?raw\s*%\}\n?", "", body)
    # 純 markdown 的相對連結（](/2026/... 、](/assets/...）
    body = re.sub(r"\]\((/(?:2026|assets)/[^)]*)\)", lambda m: f"]({SITE}{m.group(1)})", body)

    # 正文結尾那句提問與頁尾重複，去掉正文那一份（連同它前面的分隔線）
    body = body.rstrip()
    if question:
        body = re.sub(r"\n+(---\n+)?\*\*" + re.escape(question) + r"\*\*\s*$", "", body)
    body = body.rstrip()

    head = f"# {title}\n\n> 這是《{SERIES}》系列的第 {day} 篇。\n"
    foot = f"""

---

## 補充資料

- 📖 **本篇原文（含完整實驗環境與後續更新）**：{url}
- 🧪 **這篇用到的 YAML／腳本**：{REPO}
- 🤖 **lab 服務的那個模型（從零手刻的小 GPT）**：{MODEL}
- 📚 **系列完整目錄**：{SITE}

{("**" + question + "**　留言或到原文留言都可以，我會回。") if question else ""}
"""
    return day, slug, head + body + foot


def main():
    want = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    OUT.mkdir(exist_ok=True)
    n = 0
    for md in sorted(POSTS.glob("*.md")):
        head = md.read_text()[:400]
        if SERIES not in head:
            continue
        day, slug, text = convert(md)
        if want and day not in want:
            continue
        (OUT / f"day{day:02d}-{slug}.md").write_text(text)
        n += 1
    print(f"已產生 {n} 篇 → {OUT}")


if __name__ == "__main__":
    main()
