---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 19：下線與回滾——把東西拿掉這件事"
date: 2026-09-19 09:00:00 +0800
tags: [openshift-ai, odh, rollback, decommission, operations, tutorial, ironman2026]
excerpt: "教學都教怎麼裝，很少教怎麼拆。但事故當下你需要的是拆，而且要快。這篇是回滾的三個層次、下線的檢查清單，以及刪掉之後會一起消失的東西。"
feedback_question: "你們最近一次回滾模型是什麼時候？花了多久？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。⚠️ 下面「副本數會被拉回」那節的行為取決於叢集有沒有 metrics-server，CRC 上沒有，所以在這台看不到。"
---

## 這是什麼、解決什麼問題

**所有教學都教你怎麼把東西裝起來，很少教怎麼拿掉。**

但在真實環境裡，「拿掉」的場景比「裝上」更急：
模型出問題要立刻停、PoC 結束要清乾淨、
某個團隊離開要收回資源。

而且拿掉比裝上更危險——**裝錯了可以再裝，刪錯了可能救不回來。**

---

## 什麼時候你會用到

- 模型上線後行為不對，要退回上一版
- PoC 結束要清理環境
- 一個服務要正式除役
- **稽核問「舊模型下線了嗎」**

---

## 第一層：停止服務（最快，可逆）

出事的時候先做這個，**不要先查原因**。

```bash
# 把副本數降到 0
oc scale deployment my-model-predictor --replicas=0 -n <ns>
```

服務停了，但所有設定都還在。查完原因再 `--replicas=1` 起回來。

⚠️ **這一招可能撐不住，而且原因跟你想的不一樣。**

管那個 Deployment 副本數的**不是 KServe controller，是 HPA**：

```bash
$ oc get hpa
NAME                    REFERENCE                          TARGETS             MINPODS  MAXPODS
llm-scratch-predictor   Deployment/llm-scratch-predictor   cpu: <unknown>/80%  1        1
```

`MINPODS` 就是 ISvc 的 `minReplicas`。所以：

| 你的叢集 | `oc scale --replicas=0` 的下場 |
|---|---|
| **有 metrics-server（多數正式環境）** | HPA 活著，**會把副本拉回 `minPods`** |
| **沒有 metrics-server**（CRC、精簡 lab） | HPA `ScalingActive=False`，**scale 會一直有效** |

我這台 CRC 屬於後者——`oc scale --replicas=0` 放了 90 秒、
再主動戳一次 reconcile，副本數都沒有被拉回來：

```
$ oc get apiservices v1beta1.metrics.k8s.io
Error from server (NotFound)     ← HPA 拿不到指標，直接停擺
```

**不管哪一種，要「真的停」都是改 ISvc**，因為那才是 `minPods` 的來源：

```bash
oc patch isvc my-model --type=merge \
  -p '{"spec":{"predictor":{"minReplicas":0}}}'
```

**驗證這一步：**

```bash
oc get pods -l serving.kserve.io/inferenceservice=my-model
# No resources found            ← 真的停了

oc get isvc my-model            # 設定還在
```

🔴 **不要拿 `oc get isvc` 判斷服務停了沒。** 實測：

```
$ oc get pods -l serving.kserve.io/inferenceservice=llm-scratch
（空的，一個 pod 都沒有）

$ oc get isvc llm-scratch
NAME          READY
llm-scratch   True        ← 零個 pod，它說 Ready
```

連續量三次都是 `True`。**Day 18 講的是「換版失敗時它說 True，因為舊 pod 還在服務」——
這裡是真的沒有任何 pod 在跑。**

> **「停了沒」只能問 pod，不能問 ISvc。** 這條在第三層（刪掉）之後一樣適用。

---

## 第二層：回到上一版（要事先準備）

**能不能快速回滾，取決於你換版時怎麼做的。**

| 你當初怎麼換的 | 回滾要多久 |
|---|---|
| **換法 D（改 git，Argo 部署）** | **`git revert` + push**，而且回滾本身留下紀錄 |
| 換法 B（新開 ISvc + 切 Route） | **一個指令** |
| 換法 A（改 `STORAGE_URI`） | 改回舊 URI，等 pod 重啟 |
| 直接覆蓋 S3 上同一個檔案 | ❌ **舊版已經不存在了** |

第三列是真的會發生的事，而且發現的時候通常已經在事故中。
**這就是 Day 18 說「每一版一個路徑」的原因。**

換法 B 的回滾：

```bash
oc patch route my-model --type=merge \
  -p '{"spec":{"to":{"name":"my-model-v2-predictor"}}}'
```

**驗證回滾成功**——不要只看 Route 改了：

```bash
oc exec <pod> -c kserve-container -- sha256sum /mnt/models/model.bin
```

**digest 要等於舊版的那一串。**

### ⭐ 我實際回滾了一次

寫這篇之前我在 lab 上真的做了一輪：換到候選版本，再滾回來。

| | 實測 |
|---|---|
| 回滾（改 `STORAGE_URI` 改回去） | **14 秒**（含 pod 重建與 32 MB 權重重抓） |
| 換過去 | 13 秒（5.5 MB） |
| **這期間的服務中斷** | **0**（465 次 `/health` 全通） |

⚠️ **那個 0 是有條件的：patch 完什麼都不要刪。**
`replicas: 1` 加上預設的滾動策略（`maxSurge` 進位成 1、`maxUnavailable` 捨去成 0）
本來就保證新 pod 先 Ready 才收掉舊的。
**我第一次做的時候用 `oc delete pod -l <selector>` 讓它重抓，
那條指令把新舊兩個都刪了，服務真的斷了。** 細節在 [Day 18]({{ '/2026/09/swapping-model-versions/' | relative_url }})。

**兩件比秒數更值得知道的事：**

**① 回滾之後要確認的不只是 digest，還有它在不在台帳上。**
我換過去的那一版，服務自己回報 `"in_registry": false, "status": "UNREGISTERED"`——
digest 換對了，但沒有人知道那一版的評估指標是多少。
回滾之後才變回 `"status": "production"` 加上完整的 metrics。

**② 換版失敗的時候，`oc get isvc` 會是 `READY True`。**
我第一次換版是失敗的（新版本的路徑少了一個服務啟動需要的檔案），
新 pod 一直 CrashLoop，**而 ISvc 的所有 condition 都是 True、API 照常回應**——
因為舊 pod 還活著。詳細的訊號對照在 [Day 18]({{ '/2026/09/swapping-model-versions/' | relative_url }})。

> **所以「回滾成功了嗎」不能問 ISvc，要問那個 pod 裡的檔案。**

---

## 第三層：真正刪掉（不可逆）

⚠️ **這一節每個指令都會刪東西。**

### 刪一個模型服務

```bash
oc delete isvc my-model -n <ns>
```

**跟著一起消失的**：predictor Deployment、Service、pod。
**不會消失的**：你自己建的 Route、S3 上的權重、registry 的紀錄。

**這個不對稱要記住**——刪了 ISvc 之後，
你的 Route 會指向一個不存在的 Service（回 503），
而 registry 上那筆紀錄還寫著 LIVE。

### 刪一個 project

```bash
oc delete project <ns>
```

**這個要小心。** 跟著消失的包括：

- 所有 workbench **和它們的 PVC**（有人的程式碼在裡面）
- pipeline 的執行紀錄
- **DSPA 的 MariaDB（如果是內建的）→ 血緣紀錄全沒**

**S3 上的東西會留著**，因為那不在叢集裡。

### 關掉一個元件

```yaml
spec:
  components:
    modelregistry: { managementState: Removed }
```

operator 會把它的 pod 和設定收掉。
**PVC 通常留著，但別賭這個**——先確認資料在哪。

---

## 下線檢查清單

一個模型服務要正式除役，這幾件事要一起做：

| | 項目 | 指令／動作 |
|---|---|---|
| 1 | 確認沒有流量 | 看監控，**不是問人** |
| 2 | 通知使用端 | 給截止日 |
| 3 | 停服務（可逆） | `minReplicas: 0`，觀察幾天 |
| 4 | 刪 ISvc | `oc delete isvc` |
| 5 | 刪 Route | 不刪會留一個 503 |
| 6 | **registry 標記狀態** | 從 `LIVE` 改成 `ARCHIVED` |
| 7 | **權重歸檔不刪** | 稽核可能要調 |
| 8 | 紀錄下線時間與原因 | 這是稽核會問的 |

**第 6 和第 8 項最常被漏。**
沒做第 6 項，registry 就開始說謊；
沒做第 8 項，半年後沒人記得為什麼下線。

第 1 項要強調：**「應該沒人在用了」不是證據。**
去看監控上的請求數，看夠久（含月結、季結這種週期）。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | pod 沒了 | `oc get pods -l ...` 空的 |
| 2 | 端點不通 | curl 得到連不上（**不是 200**） |
| 3 | **registry 狀態對** | 查 API，state 不該還是 LIVE |
| 4 | **資源真的釋放了** | `oc describe node`，GPU/記憶體回來了 |
| 5 | 沒有孤兒物件 | 見下 |

第 5 項的掃法：

```bash
# 指向不存在 service 的 route
oc get route -n <ns> -o json | jq -r --argjson svcs \
    "$(oc get svc -n <ns> -o json | jq '[.items[].metadata.name]')" '
  .items[] | select(.spec.to.name as $t | ($svcs | index($t)) | not)
  | "⚠️  route/\(.metadata.name) → service/\(.spec.to.name)（不存在）"'

# 沒有掛載者的 PVC
oc get pvc -n <ns>
```

⚠️ **那個 `--argjson svcs` 不能省。** 只印 `route → service名` 是看不出孤兒的——
孤兒 route 長得跟正常的一模一樣，差別只在它指的那個 Service 已經不存在了。

**你現在就可以在自己叢集上跑一次。** 我跑第一次就中了一個：

```
⚠️  route/ds-pipeline-md-dspa → service/ds-pipeline-metadata-envoy-dspa（不存在）
```

打它確認：**HTTP 503**。這是幾週前某次清理留下來的，**而我完全不知道它在那裡。**

**PoC 結束後這兩樣最容易留下來**，然後在下一次盤點時沒人知道是什麼。

---

## 常見問題

**Q：刪掉的東西救得回來嗎？**
A：**沒有回收桶。** 有 etcd 備份的話理論上可以，
但那是叢集層級的還原，不會有人為了一個 ISvc 做這件事。
**PVC 刪掉就是刪掉。**

**Q：怎麼避免誤刪？**
A：正式環境的 namespace 上加 label 標明環境，
刪之前先 `--dry-run=server` 看會動到什麼。
**更根本的做法是走 GitOps**——刪東西變成一個 PR，有人看過。

**Q：回滾之後監控要怎麼處理？**
A：**記得把基準也改回去。** 不然舊版模型配新版基準，
會一直噴告警。這件事我看過不只一次。

**Q：模型下線了，訓練資料要刪嗎？**
A：**這題不是技術問題。** 個資有保存期限規定，
稽核又可能要求留存——**去問法遵，不要自己決定。**
技術上你要能做到的是「講得出哪些資料屬於這個模型」，
那就回到 Day 16 的血緣。

---

**你們最近一次回滾模型是什麼時候？花了多久？**

{% include lab-env.html %}
