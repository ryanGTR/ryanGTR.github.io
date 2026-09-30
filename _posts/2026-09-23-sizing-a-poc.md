---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 23：資源規劃——一個 PoC 要多少機器"
date: 2026-09-23 09:00:00 +0800
tags: [openshift-ai, odh, sizing, capacity, tutorial, ironman2026]
excerpt: "「要幾台機器」是評估時第一個被問的問題，而官方的最低需求會讓你估錯。這篇是我筆電上量到的實際數字，以及一個能自己算的方法。"
feedback_question: "你們的 PoC 環境是幾核幾 G？夠用嗎？"
lab_env_note: "本篇的數字在 **ODH 3.6.0-ea.1** 上重量過一次（原數字是 3.5.0 時量的）。⚠️ 同一台機器、同一份元件清單，光是升一個小版本，平台的固定成本就漲了三成——文中已標出兩組數字。"
---

## 這是什麼、解決什麼問題

「要幾台機器」通常是評估時第一個被問到的問題，
而且**答錯的代價很高**——買少了跑不動，買多了要解釋。

官方會給一份最低需求，但那是「平台本身能起來」的門檻，
**不包含你要跑的東西**。這篇給的是實際量到的數字，
以及一個你可以自己套用的算法。

---

## 什麼時候你會用到

- 要開規格採購
- 要在既有叢集上評估「加得下嗎」
- PoC 跑不動，要判斷是設定問題還是資源問題

---

## 步驟一：先看平台本身吃多少

**這是所有計算的基礎，而且它可以直接量。**

我的 lab（單節點 CRC，13 vCPU / 40 GiB）：

```bash
oc get pods -n opendatahub --field-selector status.phase=Running --no-headers | wc -l
# 22
```

這 22 個 pod 的 requests 加總：

```
ODH 3.5.0（初稿時）   cpu = 2,350m   memory = 4,356 Mi
ODH 3.6.0-ea.1（重量） cpu = 3,150m   memory = 6,404 Mi
```

**平台本身大約 3.2 核、6.3 GB。**

⚠️ **這兩列值得停一下**：同一台機器、同一份元件清單、**pod 數一模一樣是 22 個**，
只是升了一個小版本，cpu 漲 **34%**、記憶體漲 **47%**。

> **容量規劃不是算一次就好的**——升級會吃掉你的餘裕，而它不會通知你。
這裡開的元件是 dashboard、workbenches、kserve、aipipelines、modelregistry
（Day 5 那五個）。

⚠️ **這是 requests 不是實際用量。** requests 是排程時佔住的額度——
**這個數字才是決定「加不加得下」的那個**。

---

## 步驟二：加上你的工作負載

| 東西 | 我量到的 requests | 備註 |
|---|---|---|
| **OpenShift 本身** | **5.2 核 / 15.6 GB**（95 個 pod） | ⚠️ 見下 |
| 平台元件（5 個，22 pod） | 3.2 核 / 6.3 GB | 固定成本，會隨版本漲 |
| DSPA（pipeline server，7 pod） | 1.3 核 / 3.3 GB | **每個 project 一份** |
| 一個 workbench | 看 HardwareProfile，預設 2 核 / 4 GB | 見下 |
| 一個模型服務（CPU 小模型） | 0.6 核 / 1 GB | 大模型完全不同 |

⚠️ **第一列我原本寫「3–4 核 / 8 GB」，實測是 5.2 核 / 15.6 GB。**
這是整個估算的基底，**低估它會讓後面每一項都跟著偏低**。

⚠️ **「Small / Medium / Large」在 3.x 已經沒有了。**
那是 2.x 的 `notebookSizes`，3.x 改用 `HardwareProfile`：

```bash
oc get odhdashboardconfig odh-dashboard-config -n opendatahub -o json | jq '.spec.notebookSizes'
# null                                    ← 3.x 沒有這個欄位了

oc get hardwareprofiles -A
# opendatahub/default-profile: CPU def=2 min=1 | Memory def=4Gi min=2Gi
```

**預設是 2 核 / 4 GB。** 要算 workbench 的成本，去看你叢集上的 HardwareProfile，
不要抄 size 名稱。

**「每個 project 一份 DSPA」這件事最容易被漏。**
十個團隊 = 十份 pipeline server = 大約十核。
評估時要問清楚**會有幾個 project**，不是幾個使用者。

---

## 步驟三：一個能用的算法

```
總需求 = OpenShift 本身
       + 平台元件（固定 ~3.2 核 / 6.3 GB，會隨版本漲）
       + project 數 × DSPA（~1.3 核 / 3.3 GB）
       + 同時活著的 workbench 數 × 使用者選的 size
       + 模型服務數 × 各自的 size
       + 20~30% 餘裕
```

⚠️ **「同時活著的 workbench 數」不等於使用者數。**
Workbench 閒置會自動停，但**停之前它一直佔著資源**。
實務上抓「使用者數 × 0.5」起跳，然後量。

---

## 步驟四：⭐ 我真正踩到的那條線

我一開始給 CRC **10 vCPU / 32 GB**，照著官方最低需求。

平台裝得起來、dashboard 打得開、模型也上線了。
**然後 CPU requests 到了 99%**——排程器沒有位置了。

症狀不是「當機」，是：

- 新的 workbench 一直 `Pending`
- pipeline 的 pod 排不進去，run 卡住不動
- **畫面上沒有任何錯誤訊息**

要 `oc describe pod` 才看得到 `Insufficient cpu`。

加到 **13 vCPU / 40 GB** 之後：

```
cpu    10,061m (78%)
memory 27,874Mi (70%)
```

**能動了，但也只是能動。**

### ⚠️ 三週後的同一台機器

寫這篇之後我繼續在同一台上做 Day 15–21 的實驗。現在再量一次：

```
cpu    12,361m (96%)      ← 78% → 96%
memory 33,250Mi (83%)     ← 70% → 83%
```

**我什麼機器都沒加，只是平台升了版、lab 上多了幾個服務。**

而且它真的撞上去了——Day 18 做換版實驗時，新 pod 直接排不進去：

```
Warning  FailedScheduling  0/1 nodes are available: 1 Insufficient cpu.
```

滾動更新的 `maxSurge` 會進位成 1，**換版期間必須多起一個 pod**，
而那時已經沒有位置了。症狀就是這篇上面寫的那三條：
Pending、沒有錯誤訊息、要 `oc describe` 才看得到。

> **所以這篇真正的教訓不是「13 核 40 G 夠不夠」，是
> 「你的餘裕會被時間吃掉」。** 規劃時留的 20–30% 餘裕，
> 是給三個月後的你用的，不是給開機第一天的。 這個數字是「一個人的 lab」的規模——
一個模型、一個 project、偶爾開一個 workbench。

> **所以官方最低需求的意思是「平台能起來」，不是「你能做事」。**
> 這是我覺得最該提前知道的一件事。

⚠️ **而這裡有個容易被忽略的前提：宿主還有得給。**

我的宿主是 **Framework Laptop 16**，64 GB RAM——
撥 40 GB 給 CRC 之後，剩下的還夠我開瀏覽器和編輯器。
**如果宿主本來就只有 32 GB，這個調整根本做不出來。**

而這台的 RAM 和 SSD 是標準規格、自己拆得開，**真的不夠還能加**。
焊死記憶體的機器就沒有這一步，選擇會變成
「把 CRC 開小，然後接受一半的元件排不進去」或「換一台」。

所以如果你正要挑一台跑 lab 的機器，**判準不是「現在幾 GB」，
是「撞牆的時候有沒有路走」**——這條路撞牆的機率接近 100%。

---

## 怎麼確認做對了

| | 檢查 | 指令 |
|---|---|---|
| 1 | 目前用了多少 | `oc describe node \| grep -A6 "Allocated resources"` |
| 2 | **requests 別超過 80%** | 同上，看百分比 |
| 3 | 有沒有東西排不進去 | `oc get pods -A \| grep Pending` |
| 4 | 誰吃最多 | 見下 |
| 5 | ⭐ 壓一次試試 | 見下 |

第 4 項——**找出資源大戶**：

```bash
oc get pods -A -o json | jq -r '.items[]
  | select(.status.phase=="Running")
  | ([.spec.containers[].resources.requests.cpu // "0"]
     | map(if test("m$") then (.[:-1]|tonumber) else (tonumber*1000) end) | add) as $cpu
  | "\($cpu) \(.metadata.namespace)/\(.metadata.name)"' \
  | sort -rn | head -10
```

⚠️ **我第一版寫的是 `.spec.containers[0]` 加 `sort -rh`，兩個地方都錯：**

**① `sort -rh` 會把最大的排到最後。** `-h` 認得的是 K/M/G，不是 milli：

```
$ printf '2\n500m\n1\n250m\n' | sort -rh
500m
250m
2          ← 2 核被排在 500m 後面
1
```

**② `containers[0]` 不算 sidecar。** KServe／Istio 的 pod 都有：

```
llm-scratch-predictor:  kserve-container 500m + kube-rbac-proxy 100m = 600m
                        ↑ containers[0] 只報 500m
```

找資源大戶卻把大戶漏掉，**這種 bug 不會報錯，只會讓你查錯方向。**

第 5 項：**同時開三個 workbench 加跑一條 pipeline，看會不會排不進去。**
規劃階段做這件事很便宜，上線後才發現很貴。

---

## 常見問題

**Q：GPU 怎麼算？**
A：GPU 是整數資源，**不能超賣**（除非用 MIG 或 time-slicing）。
所以算法很簡單：**同時要跑幾個要 GPU 的東西，就要幾張卡。**
真正該問的是「使用率」——一張卡被一個閒置的 workbench 佔著，
利用率是 0 而帳單是滿的。

**Q：可以用 requests 低、limits 高來省嗎？**
A：可以，OpenShift 允許超賣（我的 lab limits 現在是 **cpu 250% / memory 356%**）。
**但排程只看 requests，OOM 只看 limits。**
超賣得太兇的結果是「排得進去、跑一跑被殺掉」，
**而那種失敗比排不進去難查得多。**

**Q：儲存要多少？**
A：三塊分開算：**PVC**（workbench 每人 20 GB 起跳）、
**S3**（資料 + 模型 + pipeline 產物，會一直長）、
**registry**（image，一個 workbench image 就 1.7 GB）。
**S3 那塊最容易低估**，因為 pipeline 每次執行都留產物。

**Q：多節點跟單節點差在哪？**
A：我只有單節點，**多節點的排程、跨節點網路、HA 我沒驗過**。
但有一件事確定：**單節點沒有 HA**，任何維護都是停機。
PoC 可以，正式環境不行。

---

**你們的 PoC 環境是幾核幾 G？夠用嗎？**

{% include lab-env.html %}
