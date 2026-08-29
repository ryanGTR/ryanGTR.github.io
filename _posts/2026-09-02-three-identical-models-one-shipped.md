---
layout: post
title: "三顆一樣的模型，一顆上線兩顆被擋"
date: 2026-09-02 09:00:00 +0800
tags: [mlops, governance, promotion-gate, openshift-ai, kubeflow-pipelines, acceptance]
excerpt: "同一條 pipeline、同一份資料、同樣的步數，三次訓練出來的模型能力幾乎相同：7.2343 / 7.2370 / 7.2428。一顆上線，兩顆被 gate 擋下。決定它們命運的不是模型。"
feedback_question: "你們的 promotion gate 有沒有真的擋下過東西？還是從來沒紅過？如果紅過，後來是怎麼處理的？"
---

我在 OpenShift AI 上搭了一條四棒的模型交付鏈：

```
prepare-data → train-model → evaluate-model → promotion-gate
```

最後那一棒讀評估報告，`val_loss` 超過門檻就 `exit 1`，run 變紅，模型不上線。
聽起來很合理。

然後我跑了五次。

![Runs 列表：四個 Failed 一個 Complete](/assets/img/rhoai/runs-list.png)

四個 Failed，一個 Complete。**這篇是關於那個 Complete 的。**

---

## 三顆能力幾乎相同的模型

先看 run1、run2、run4 的參數。這是從 pipeline API 直接拉出來的：

```
llm-lifecycle-run1     FAILED     max_iters=300  max_val_loss=6.0  sample_mb=2
mlops-pipeline-test    FAILED     max_iters=300  max_val_loss=6.0  sample_mb=2
run4-raise-threshold   SUCCEEDED  max_iters=300  max_val_loss=8.0  sample_mb=2
```

**除了 `max_val_loss` 之外，每一個參數都一模一樣。** 同一份語料、同樣的步數、同一條 pipeline。

訓練出來的模型：

| run | val_loss | 結果 |
|---|---|---|
| run1 | 7.2370 | ❌ FAILED |
| run2 | 7.2428 | ❌ FAILED |
| **run4** | **7.2343** | ✅ **SUCCEEDED** |

三個數字的全距是 **0.0085**。

而我另外量過這條產線的自然波動：同設定只換 random seed 跑五次，
`test_loss` 的標準差 **σ = 0.0211**（做法在[另一篇](/2026/08/my-drift-monitor-said-retrain/)講的邏輯）。

**0.0085 遠小於 1σ。這三顆模型在統計上分不出高下。**

而 run4 上線了，run1 和 run2 被擋下。

---

## 決定它們命運的是什麼

不是模型。是 `max_val_loss` 從 `6.0` 改成 `8.0`。

而那是一個 **run 參數**——執行的時候在表單裡填的。誰都能填。

![run4 詳情：四棒全綠](/assets/img/rhoai/run4-green.png)

四棒全綠。`prepare-data` → `train-model` → `evaluate-model` → `promotion-gate`，
每一格都是打勾的。任何看板、任何報表、任何稽核截圖上，這都是一條健康的交付鏈。

而標題正下方那行字，是我當初自己填的 run 描述：

> **「對照 run1/run2：唯一變因是門檻 6.0→8.0。模型沒變好，是標準降低了。」**

如果我沒填那句，這張圖看起來就是「模型通過品質閘門，正常上線」。

---

## 這個 gate 到底擋得住什麼

我把它的能力邊界逐條列出來：

| 情境 | 擋得住？ |
|---|---|
| 模型品質不達標 | ✅ 這就是它在做的事（run1：7.237 > 6.0 → FAILED） |
| 有人手動把 checkpoint 丟進 S3，繞過 pipeline | ❌ gate 在 pipeline 裡，不在上線路徑上 |
| 有人把 `max_val_loss` 填成 999 | ❌ 那是 run 參數，誰都能填 |
| KServe 部署一顆沒過 gate 的模型 | ❌ InferenceService 只認 S3 路徑，不查台帳 |

**只有第一列是綠的。而那一列，正是最容易被門檻參數繞過的一列。**

---

## 為什麼會這樣

因為這條鏈上的 gate 是**產生一個結論**，而不是**執行一個決定**。

pipeline 跑完，gate 說「不通過」，run 變紅。**然後呢？**
沒有然後。模型檔案已經在 S3 上了，KServe 的 `InferenceService` 只需要一個 S3 路徑，
它不會去問「這顆過 gate 了嗎」。

我實測過這件事：服務上線後打它的 `/model` 端點，它回 `"status": "UNREGISTERED"`——
**一顆答不出自己憑什麼上線的模型，照樣在對外服務。**
沒有任何機制阻止它。服務只是事後誠實地承認自己沒登記。

> **這條鏈上，目前沒有任何一個點會因為台帳說「不」而讓部署失敗。**

---

## 那要怎麼補

三個層次，由淺到深：

**1. 門檻不能是 run 參數。**
把它移到版控裡的政策檔，改門檻要走 PR。這樣「調鬆門檻」這個動作本身
會留下一筆可追溯的紀錄，而不是躲在某次 run 的表單裡。

**2. 門檻要有依據。**
`6.0` 和 `8.0` 都是我隨手填的。正確做法是先量這條產線的 σ，
門檻設在有統計意義的位置。**沒量過的門檻，改起來沒有心理成本——這才是它容易被調鬆的真正原因。**

**3. 上線路徑要查台帳。**
最小做法：`InferenceService` 加一個 init container，
啟動前查模型台帳，`status != production` 就拒絕啟動。

> 第 3 點我**還沒實作**。所以這篇的結論同時適用於我自己：
> 我做了治理，但我的治理現在還擋不住東西。

---

## 一個給驗收用的問題

如果你要驗收別人交付的 MLOps 平台，這一整篇可以壓縮成一句：

> **「這條鏈上，哪一個點會因為檢查沒過而讓部署失敗？」**

不是問「有沒有品質閘門」——那個答案永遠是「有」。
要問的是**失敗會在哪裡發生**。如果對方講不出一個具體的點，
那條鏈上的檢查全部是**產生報告**，不是**擋事**。

而這兩者的差別，在稽核截圖上看起來一模一樣。

---

**你們的 promotion gate 有沒有真的擋下過東西？還是從來沒紅過？
如果紅過，後來是怎麼處理的——修模型，還是改門檻？**

{% include lab-env.html %}
