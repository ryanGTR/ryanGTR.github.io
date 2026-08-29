---
layout: post
title: "線上的 LLM 要監控什麼、怎麼評估"
date: 2026-09-19 09:00:00 +0800
tags: [mlops, llm, monitoring, prometheus, grafana, evaluation, openshift-ai]
excerpt: "一般服務壞掉會回 500，LLM 壞掉會回一個看起來正常的答案。所以只盯錯誤率和延遲等於沒監控。這篇是四層指標、埋法、以及「監控」和「評估」為什麼是兩件事。"
feedback_question: "你們線上的模型有在監控嗎？盯的是哪幾個數字？有沒有哪一個真的救過你？"
---

## 一句話講完差別

> **一般服務壞掉會回 500。LLM 壞掉會回一個看起來正常的答案。**

所以如果你只監控錯誤率和延遲——**那兩個永遠是綠的**，而模型可能已經在胡說八道三個禮拜了。

這是所有 LLM 監控設計的起點。

---

## 四層指標

| 層 | 看什麼 | 抓得到什麼 |
|---|---|---|
| ① **服務層** | 延遲 p50/p95、錯誤率、吞吐 | 掛掉、變慢 |
| ② **模型層** | 輸入漂移、輸出退化 | **內容變差** |
| ③ **成本層** | token 用量、GPU 利用率 | **帳單爆炸** |
| ④ **行為層** | 重問率、放棄率、人工接手率 | **使用者其實不滿意** |

**大部分團隊只做了第一層。** 而第一層抓不到 LLM 特有的任何一種失敗。

### ② 模型層要看什麼

- **輸入漂移（PSI）**：使用者問的東西跟訓練資料還像不像
- **OOV 率**：出現訓練時沒看過的字元／詞。對固定 vocab 的模型特別致命
- **重複率** ← **退化的第一個症狀，而且最好量**。模型開始壞掉時，
  第一個表現通常是重複同一個詞或句型
- **拒答率、格式違規率**：該回 JSON 卻不是

### ③ 成本層最常被忘

**LLM 的成本是按 token 算的。**

一個 prompt 改壞了、一個 few-shot 範例加太長、一個 retry 邏輯寫錯——
成本可能翻三倍，**而所有健康指標都是綠的**。

要看：每次請求的 token 數（不是總量，是**分布**）、tokens/sec、GPU 利用率。

### ④ 行為層最誠實，但你可能沒有

重問率、放棄率、**人工接手率**——如果你有客服流程，最後那個是最直接的訊號。

> ⚠️ **誠實標一下**：我的 lab 沒有真實使用者，這一層我一個都沒量過。
> 它是我認為最有價值的一層，但我沒有證據。

---

## 怎麼埋：四步

### ① 應用裡加指標

`prometheus_client`，五行：

```python
from prometheus_client import Counter, Histogram, Gauge

REQS         = Counter("llm_requests_total", "請求總數", ["endpoint", "status"])
LATENCY      = Histogram("llm_request_latency_seconds", "請求延遲(秒)", ["endpoint"])
TOKENS       = Counter("llm_generated_tokens_total", "累計生成 token 數")
DRIFT_PSI    = Gauge("llm_drift_psi", "請求字元分布 vs 訓練分布的 PSI")
DRIFT_OOV    = Gauge("llm_drift_oov_rate", "請求用到訓練外字元的比例")
SHADOW_AGREE = Histogram("llm_shadow_agreement", "候選 vs 現行 next-token 一致率",
                         buckets=(0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0))
```

⭐ **選型別是最常做錯的地方：**

| 型別 | 特性 | 用在 |
|---|---|---|
| **Counter** | 只增 | 總量：請求數、累計 token |
| **Histogram** | 自動分桶 | **分布**：延遲 p50/p95、一致率 |
| **Gauge** | 可上可下 | 當下狀態：PSI、OOV 率 |

**拿 Gauge 記延遲是最常見的錯**——你只會看到最後一筆，
永遠看不到 p95。而 p95 才是使用者感受到的那個數字。

### ② 在請求路徑上更新

```python
@app.post("/generate")
def generate(req: GenerateRequest):
    drift = STATE["drift"]
    drift.observe(req.prompt)            # 餵給漂移監控
    DRIFT_PSI.set(drift.psi())
    DRIFT_OOV.set(drift.oov_rate())
    ...
```

⚠️ **這一段要夠便宜。** 它在每一個請求的關鍵路徑上——
如果你的漂移計算要一秒，你就把服務拖垮了。
PSI 那類統計量要用增量更新，不要每次重算全部。

### ③ 開 `/metrics`

`prometheus_client` 會自動產生，掛上去就好。

### ④ Prometheus 抓、Grafana 畫

⚠️ 這一步有個坑我踩過：**datasource 沒指定 `uid`，
九個面板全部查不到資料，而 pod、target、dashboard 全部顯示正常。**
細節在[另一篇](/2026/09/nine-broken-panels/)。

---

## ⭐ 監控 ≠ 評估

這兩個詞常被混用，但它們的前提完全不同：

| | 監控 | 評估 |
|---|---|---|
| 頻率 | **持續** | 週期性 |
| 需要標準答案嗎 | **不需要** | **需要** |
| 回答的問題 | 它現在正不正常 | 它到底好不好 |

**線上不能算 loss**，因為 loss 的定義是 `-log P(下一個真實 token)`——
你有使用者的輸入，但沒有「正確的下一個 token」。**沒有標準答案，這個數字算不出來。**

### 離線評估

在 pipeline 的 `evaluate` 那一棒做：獨立的 test split → loss / perplexity / **BPC**。

⚠️ 兩個常見錯誤：
- **拿 val 當最終成績**——val 是你調參時看過的，它已經被汙染了
- **換過 tokenizer 之後直接比 loss**——那兩個數字單位不同，要用 BPC

### 線上評估的三條路

**1. Shadow（影子）** ← 最推薦
候選模型跟現行模型吃同一個輸入，比對 next-token 的 argmax 一致率。
**完全不影響使用者**，因為候選的輸出丟掉不用。

```python
def _shadow_compare(prod, cand, ids, device):
    """同一輸入下，候選與現行的 next-token 預測一致率（argmax 比對）"""
```

一致率突然掉下來 = 候選跟現行的行為分岔了，**放量之前先看這個數字**。

**2. 代理指標**
重複率、拒答率、長度分布——**不需要標準答案就能算**，而且對退化敏感。

**3. 人工抽樣**
定期抽 N 筆請人看。最貴，但**最真**，而且是唯一能抓到「答得很流暢但完全錯誤」的方法。

### ⚠️ 關於 LLM-as-judge

用另一個模型幫輸出打分，現在很流行。但要小心：

**judge 自己也有偏好**——它傾向給「跟自己風格相似」的輸出高分。

如果不先驗證，你只是把「這個模型好不好」換成了「這個模型像不像那個 judge」。
**要用的話，先拿一批人工標註去驗證那個 judge 準不準**，
就像你不會用一把沒校準過的尺去量東西。

---

## 監控的目的不是「知道它壞了」

這是我最想留下的一句。

**你量那些指標，是為了決定「要不要動這個模型」。而大部分時候，答案是不要。**

指標動了，處置的階梯由便宜到貴：

```
重啟 → 改 prompt → 更新 RAG 內容 → 路由/降級 → 後訓練 → 重訓
```

前四階能解決的佔絕大多數。
（這件事我另外寫過一篇：[我的漂移監控說「該重訓了」。它是錯的。](/2026/08/my-drift-monitor-said-retrain/)）

一個只會叫你重訓的監控，跟沒有監控的差別，
**只在於前者會讓你浪費 GPU。**

---

**你們線上的模型有在監控嗎？盯的是哪幾個數字？有沒有哪一個真的救過你？**

{% include lab-env.html %}
