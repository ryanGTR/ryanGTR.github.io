---
layout: post
title: "從零刻一個 GPT，然後把交付鏈的紀律套上去"
date: 2026-08-21 18:00:00 +0800
tags: [llm, mlops, governance, evaluation, book]
excerpt: "一本線上書、十一章、全程可在筆電重現。模型只有 8M 參數，重點不是模型，是四次『以為對、量了才知道錯』——以及 digest、gate、lineage 這套交付鏈的東西原封不動搬到模型上。"
---

這個 blog 前三篇都在講 OpenShift 上的交付鏈。這篇換個對象：**模型**。

今年六月起我從零手刻了一個 decoder-only GPT，一路推完資料工程、現代架構、訓練評估、服務化、
治理、漂移重訓、到後訓練對齊（SFT → DPO/IPO → GRPO/PPO），寫成一本 Quarto 線上書：

**<https://ryangtr.github.io/llm-from-scratch/>**（原始碼：[ryanGTR/llm-from-scratch](https://github.com/ryanGTR/llm-from-scratch)，MIT，CI 綠）

它跟這裡的其他文章是同一個站底下的另一個 repo，所以不會出現在首頁列表。補一篇當入口。

## 為什麼一個做交付鏈的人去刻 GPT

因為 LLM 對我一直是黑盒，而黑盒沒辦法治理。與其追工具，我決定把一個 GPT 從零養大，
再用我熟的那套——digest 身份、promotion gate、lineage、可觀測——把它管起來。
兩塊的交集（MLOps ＋ 模型治理）正是我想站的位置。

模型刻意小：demo 0.8M、中文實戰 8M、char-level。小到一次對照實驗幾十秒到幾分鐘，
所以我能做**很多次**「先預測 → 實測 → 被打臉 → 想懂為什麼」。這本書的價值在那些打臉，不在模型。

## 四次被打臉

**1. 聚合指標說健康，資料裡有 21.6% 是髒的。**
換成 105 MB 中文維基後，熵、壓縮比、重複率全部 ✅。我不信，寫了一套偵測器逐條掃，
抓到 21.6% 的文件殘留維基繁簡轉換語法 `-{zh-tw:..;zh-cn:..}-`。修一條清洗規則 → 0.05%。
這跟疊稽核控制項是同一個動作：看樣本發現問題 → 寫成一條偵測器 → 累積成資料的測試套件。

**2. 同一顆模型，換把尺，結論相反。**
SFT 做完用維基 perplexity 量，顯示「變爛了」。那是預訓練的尺，量後訓練必然誤判（alignment tax）。
改量「只算回答段的 loss」，還是 base 贏——因為 gold 答案是維基句，base 預訓練時背過。
最後量「生成是否以定義句應答」這個**行為**：base 29% → SFT 72%。尺選錯，數字再漂亮也是錯的。

**3. train-acc 100% 不代表學會。**
DPO 兩種偏好軸，train-acc 都在第 200 步衝到 100%。held-out 一個 97%、一個 9%。
前者學到可遷移的特徵，後者是把 784 組訓練對背起來。只看 train 會宣稱兩個都學會了。

**4. reward model 給高分，不代表答案好。**
RLHF 拿掉 KL 錨、讓 RL 用力最大化 reward model：分數從 3.7 衝到 13.2，
輸出卻 collapse 成「不管問什麼都吐同一串垃圾」，多樣性 100% → 6%。那串垃圾落在 reward model 的訓練分布外、被誤判高分。
這是 Goodhart's law 的活體——指標一旦變成目標就不再是好指標。跟 KPI 被衝爆是同一件事，防法是 KL 錨把 policy 綁在可信的舊模型附近。

## 交付鏈的東西原封不動搬過去

| 交付鏈 | 模型 |
|---|---|
| image digest，不認 tag | checkpoint 的 sha256，不認檔名；服務端 `/model` 回報自己的 digest，對不上 registry 就是 `UNREGISTERED` |
| Harbor 待審區 ＋ 人工放行 | promotion gate：資料品質報表 ＋ 評估判準沒過，不准上線 |
| SBOM、provenance | lineage：資料 digest ＋ git commit ＋ 品質 gate 結果，綁在每顆模型上 |
| Prometheus 看 pipeline | Prometheus ＋ Grafana 看推論服務；一裝上就抓到冷啟動 354ms vs 暖機 50ms |
| 金絲雀、回滾 | 影子流量比對 agreement、PSI 量化漂移、觸發重訓 |

書的第 7 章「模型治理：把稽核接到 ML」就是這張表展開來。

## 怎麼讀、怎麼跑

| 章 | 在講什麼 |
|---|---|
| [1](https://ryangtr.github.io/llm-from-scratch/01-minimal-gpt.html) | 最小的 GPT：從 attention 到自回歸 |
| [2](https://ryangtr.github.io/llm-from-scratch/02-modern-components.html) | 把它變現代：RMSNorm、SwiGLU、RoPE、GQA |
| [3](https://ryangtr.github.io/llm-from-scratch/03-efficiency.html) | 跑多快：FlashAttention、KV-cache、取樣 |
| [4](https://ryangtr.github.io/llm-from-scratch/04-real-data.html) | 真實資料的坑：從 1 MB 英文到 105 MB 中文 |
| [5](https://ryangtr.github.io/llm-from-scratch/05-evaluation.html) | 不騙自己的評估 |
| [6](https://ryangtr.github.io/llm-from-scratch/06-serving.html) | 服務化與可觀測 |
| [7](https://ryangtr.github.io/llm-from-scratch/07-governance.html) | 模型治理：把稽核接到 ML |
| [8](https://ryangtr.github.io/llm-from-scratch/08-drift-retrain.html) | 會腐壞的系統：drift、重訓、放量 |
| [9](https://ryangtr.github.io/llm-from-scratch/09-alignment.html) | 對齊：SFT → DPO/IPO → GRPO/PPO |
| [10](https://ryangtr.github.io/llm-from-scratch/10-math-appendix.html) | 附錄：數學推導（RLHF → DPO 封閉式、margin ≈ 1/β、RoPE） |

不用 GPU 也能重現每一章的結論。`book/examples/tiny_*.py` 每支都是純 CPU、只要 `torch`、幾十秒到幾分鐘，CI 顧著不會壞：

```bash
git clone https://github.com/ryanGTR/llm-from-scratch && cd llm-from-scratch/book/examples
curl -o input.txt https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt
python tiny_gpt.py      # 幾分鐘訓出一個會「假裝莎士比亞」的小模型（GPU 上 35 秒）
python tiny_dpo.py      # 親眼看 train-acc 100%、held-out 才說真話
python tiny_serve.py    # digest 身份 ＋ promotion gate 真的擋下
```

## 誠實的邊界

這不是 ChatGPT。8M 參數、char-level、單機——它學會中文的字、詞、語法、標點，寫不出連貫文章。
能力上限受限於規模，這我從第一頁就講清楚。價值在走完整條鏈時的工程判斷：
可重現（uv lock）、可驗證（單元測試 ＋ 驗收 playbook ＋ CI）、可觀測、可治理——
以及那條貫穿全書的紀律：**永遠先問這把尺量到什麼、有沒有被汙染。**
