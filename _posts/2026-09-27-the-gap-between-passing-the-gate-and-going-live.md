---
layout: post
title: "從 gate 放行到真的上線，中間那一步是空的"
date: 2026-09-27 09:00:00 +0800
tags: [mlops, governance, promotion-gate, kserve, openshift-ai, deployment]
excerpt: "我的 pipeline 有完整的四棒、有 gate、有台帳。gate 也真的放行了一顆模型。然後我去查線上跑的是哪一顆——是四天前的另一顆，而且跟這條 pipeline 完全無關。"
feedback_question: "你們的模型從「訓練完」到「線上服務」，中間那一步是誰做的？自動的還是人工的？"
---

前面我寫了[四棒 pipeline](/2026/09/four-step-pipeline-on-openshift-ai/)、
寫了 [gate 擋不擋得住](/2026/09/three-identical-models-one-shipped/)、
寫了 [InferenceService 怎麼上線](/2026/09/what-inferenceservice-actually-creates/)。

但有一段我一直沒寫，因為我以為它是顯而易見的：

> **模型訓練完、gate 放行之後，它怎麼變成線上服務？**

今天我去查了。**答案是：不會。中間那一步是空的。**

---

## 先看程式碼說什麼

gate 那一棒通過之後，最後做的事是：

```python
print("  ✓ 放行")
reg.append(entry)
put(REG_LOCAL, MODELS, REG_KEY)                       # 寫台帳
put(ART / "ckpt.pt",        MODELS, "llm-candidate/ckpt.pt")
put(ART / "tokenizer.json", MODELS, "llm-candidate/tokenizer.json")
print("  已註冊進台帳並送進候選區 s3://models/llm-candidate/")
```

**放行 = 把模型複製到 `llm-candidate/`。**

而 InferenceService 讀的是哪裡？

```bash
oc get isvc llm-scratch -o jsonpath='{.spec.predictor.containers[0].env[?(@.name=="STORAGE_URI")].value}'
# s3://models/llm/
```

**`llm/`，不是 `llm-candidate/`。**

---

## ⭐ 時間戳說得更清楚

```bash
mc ls --recursive local/models
```
```
[2026-08-28 08:13]  5.5 MiB   llm-candidate/ckpt.pt      ← gate 放行的那顆
[2026-08-28 08:13]   36 KiB   llm-candidate/tokenizer.json
[2026-08-24 02:41]   32 MiB   llm/ckpt.pt                ← 線上服務實際讀的
[2026-08-24 03:35]   99 MiB   llm/clean_corpus.txt
[2026-08-24 02:41]   97 KiB   llm/tokenizer.json
```

**候選區那顆是 8/28 的，5.5 MB。線上那顆是 8/24 的，32 MB。**

不只時間差四天——**大小差六倍，那根本是兩顆完全不同的模型。**

**gate 放行的東西，從來沒有上線過。**

---

## 更麻煩的：服務講的是另一份台帳

去問服務它自己是誰：

```bash
curl -s localhost:18000/model | jq
```
```json
{
  "serving_digest": "sha256:4d694be9342d…",
  "in_registry": true,
  "status": "production",
  "metrics": {"test_loss": 3.462, "test_bpc": 4.9946},
  "data_quality_gate": true
}
```

**`status: production`。看起來一切正常。**

但那份台帳是用 ConfigMap 掛進容器的本機檔，裡面兩筆，
**建立時間都是 2026-06-21**：

```
00b47fc84755   archived     2026-06-21
4d694be9342d   production   2026-06-21     ← 線上這顆
```

而 gate 寫的台帳應該在 S3 上。我去找：

```bash
mc ls --recursive local/models | grep registry.json
# （沒有）
```

**兩份台帳，互不相干。**

- **服務讀的那份**：ConfigMap，兩筆，六月的
- **gate 寫的那份**：S3，而我在 bucket 裡找不到它

所以 `/model` 回的 `production` 是真的——**它只是在講六月那顆模型，
跟這條 pipeline 跑出來的東西完全沒有關係。**

---

## 整條鏈畫出來長這樣

```
  pipeline train  →  s3://models/runs/<run_id>/ckpt.pt
                            ↓
  gate 放行       →  s3://models/llm-candidate/ckpt.pt      8/28, 5.5 MB
                            ↓
                    ┌───────────────┐
                    │  ？？？        │  ← 這裡什麼都沒有
                    └───────────────┘
                            ↓
  ISvc 讀取       →  s3://models/llm/ckpt.pt                8/24, 32 MB
```

**中間那個框，就是「上線」這件事。而它沒有被實作。**

---

## 為什麼會這樣——而且為什麼很常見

我不是忘了寫。我是**做到「候選區」就停下來了，因為那一步之後的東西不是技術問題**。

從候選到上線，需要決定的是：

| 問題 | 這是誰的決定 |
|---|---|
| 誰有權按下「上線」 | **不是工程問題** |
| 上線要不要人工確認 | 流程問題 |
| 換上去之後怎麼回滾 | 要先有回滾機制 |
| 舊的那顆留多久 | 保存政策 |
| 出事了誰負責 | **組織問題** |

**技術上，「上線」可以是一行 `mc cp llm-candidate/ llm/`。**

而正因為它技術上這麼簡單，**它幾乎總是最後才被做**，
或者被做成「某個人手動跑一下」——然後那個人變成單點。

> ⭐ **這就是「治理只做到一半」的具體長相：
> 前面每一棒都自動化、有紀錄、有 gate，
> 而最後那個真正改變線上狀態的動作，是人工的、沒有紀錄的。**

---

## 兩種補法

### A. 自動 promote（適合換版頻繁）

gate 通過就直接複製到 `llm/`，然後重啟 ISvc。

```bash
mc cp local/models/llm-candidate/ckpt.pt local/models/llm/ckpt.pt
oc rollout restart deploy/llm-scratch-predictor
```

⚠️ **這樣做的前提是你的 gate 真的擋得住東西。**
如果 gate 的門檻是 run 參數（[誰都能填](/2026/09/three-identical-models-one-shipped/)），
自動 promote 等於把「上線」的權限交給任何能建 run 的人。

### B. 人工放行，但留下紀錄（適合金融業）

候選區保持不動，上線是一個**明確的、有簽核的動作**——
而且那個動作應該是 **git commit**，不是 `mc cp`。

把 ISvc 的 `STORAGE_URI` 或模型 digest 寫進 GitOps repo，
上線 = 改那個檔案 + PR + merge。

**這樣「誰放行的」這個問題，答案就在 git log 裡。**

> 這跟容器 image 的 `demo-tmp → demo` 放行機制是同一個形狀：
> **待審區和放行區分開，而搬動的那個動作要留痕。**

---

## 最誠實的一段

**我兩種都還沒做。**

我的 lab 現在的狀態是：pipeline 會跑、gate 會擋、台帳會寫、
候選區會更新——**而線上那顆是六月手動放上去的，四天前的候選從來沒上去過。**

而我一直沒發現，因為：

- `oc get isvc` → `READY=True` ✅
- 推論打得通 ✅
- `/model` 回 `production` ✅
- pipeline 的 run 是綠的 ✅

**每一個檢查都通過，而這條鏈中間是斷的。**

要發現它，唯一的辦法是**去比對兩個路徑的時間戳**——
而那不在任何一份健康檢查清單上。

---

## 給驗收的一句

> **「請告訴我，線上這顆模型是哪一次 run 產出的。」**

如果對方要去翻聊天記錄、或者說「應該是上次那個」，
**那條鏈就是斷的**——不管前面的 pipeline 做得多漂亮。

而這一題比「有沒有 gate」有用得多，因為它問的是**結果**不是**流程**。

---

**你們的模型從「訓練完」到「線上服務」，中間那一步是誰做的？自動的還是人工的？**

{% include lab-env.html %}
