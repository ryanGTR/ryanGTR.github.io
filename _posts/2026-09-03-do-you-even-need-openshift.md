---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 3：先問一句——這些用 podman 做不行嗎？"
date: 2026-09-03 09:00:00 +0800
tags: [openshift-ai, podman, docker, comparison, mlops, ironman2026]
excerpt: "社群教的是 podman 和 docker compose，而且它走得比多數人以為的遠。這篇是我兩邊都做過之後的對照：podman 版做得到什麼、在哪裡斷、以及那個交叉點在哪。"
feedback_question: "你們現在的模型服務跑在哪？podman/docker、VM，還是已經在 k8s 上？"
---

## 這是什麼、解決什麼問題

在裝任何東西之前，先誠實回答一個問題：

> **社群教的都是 podman 和 docker compose，我為什麼要一整套 OpenShift AI？**

這個問題該在 Day 3 問，不是在導入到一半才問。
**而且它的答案不是「因為 k8s 比較好」。**

我兩邊都做過：先在筆電上用 podman 把一個自己刻的小 GPT 從訓練到上線跑完，
之後才把同一件事搬上 OpenShift AI。這篇是那個對照。

---

## 先講結論：podman 走得比你以為的遠

**很多人低估了 podman 版能做到的程度。** 我那套 podman stack 有：

| 能力 | 怎麼做的 |
|---|---|
| 推論 API | FastAPI + uvicorn，一個 Containerfile |
| **模型不進 image** | `-v ./artifacts:/app/artifacts:ro` mount 進去 |
| GPU | `--device nvidia.com/gpu=all`（CDI passthrough） |
| 多容器一起跑 | `podman pod`（**pod 這個概念就是從這來的**） |
| 監控 | 同一個 pod 裡跑 Prometheus + Grafana |
| **金絲雀 + shadow** | 環境變數 `CANARY_PCT` / `SHADOW_PCT` |
| **模型台帳** | 一份 `registry.json`，以 sha256 digest 為主鍵 |

起整套的指令長這樣：

```bash
podman pod create --name llm-stack -p 8000:8000 -p 9090:9090 -p 3000:3000
podman run -d --pod llm-stack --name llm-api --device nvidia.com/gpu=all \
  -v ./artifacts:/app/artifacts:ro,Z llm-from-scratch:latest
podman run -d --pod llm-stack --name llm-prometheus \
  -v ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro,Z prom/prometheus
podman run -d --pod llm-stack --name llm-grafana \
  -v ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro,Z grafana/grafana
```

**四行，一分鐘起來。**

對照一下前面幾天講的東西：`STORAGE_URI` 對應 `-v` mount，
`InferenceService` 對應 `podman run`，Prometheus 那段一模一樣，
**`canaryTrafficPercent` 對應那兩個環境變數**。

> **概念是同一套。** 這也是為什麼 podman 版很值得先做一次——
> **你會先懂概念，再去看那些 CRD 就不是天書。**

---

## 那它在哪裡斷？

**不在功能，在「不只你一個人」和「你不在的時候」。**

### 斷點一：多人

podman 版的隱含前提是「**這台機器是我的**」。

兩個人要用同一台，馬上要回答：誰的容器、誰能停誰的、
GPU 誰先用、你 `podman rm -f` 的時候會不會殺到別人的。

**這些問題 podman 沒有答案**，因為它不是設計來解這個的。
Day 21（權限）和 Day 13（Hardware Profile）在解的就是這個。

### 斷點二：你不在的時候

- 機器重開，容器會自己起來嗎？（`--restart` 加了嗎？）
- 服務掛了誰重啟它？
- 機器壞了，服務去哪？

**單機沒有 HA，這不是設定問題，是物理問題。**

### 斷點三：誰在什麼時候改了什麼

`podman run` 打在某個人的終端機裡。**那筆紀錄在 shell history。**

Day 30 的七題（誰核准上線的、做了哪些檢查、上線的是哪一版），
**podman 版一題都答不出來**——不是它不能，是**沒有地方留**。

### 斷點四：規模

十個模型、五個團隊、三十個 workbench。
**podman 不是撐不住，是你會開始自己寫排程器和權限系統**——
然後你就在重寫 k8s，只是比較難用。

---

## ⭐ 交叉點在哪

**用一句話判斷：**

> **當「誰能做什麼」和「不在場時會怎樣」開始需要答案，就該換了。**

具體一點，出現以下任何**兩件**，podman 就開始比較貴：

- [ ] 超過一個人要用同一批資源
- [ ] 有人（稽核、法遵、主管）會問「線上是哪一版、誰放上去的」
- [ ] 服務不能停超過幾分鐘
- [ ] 資料不能落在個人設備上
- [ ] 模型超過三個、或會定期換版
- [ ] 需要跨環境（dev / test / prod）一致

**一件都沒有的話，podman 是對的選擇**，而且會讓你活得比較輕鬆。

---

## 一張對照表

| | podman / compose | OpenShift AI |
|---|---|---|
| 起步 | **幾分鐘** | 幾小時到幾天 |
| 學習成本 | 低 | 高（前面兩天那些名詞） |
| 資源開銷 | 幾乎零 | **平台本身就要 2.4 核 / 4.3 GB**（Day 23） |
| 模型服務 | `podman run` | `InferenceService` |
| GPU | `--device`，先搶先贏 | Hardware Profile + 配額 |
| 多人 | ❌ 沒有概念 | RBAC、namespace 隔離 |
| **誰改了什麼** | ❌ shell history | 稽核紀錄、GitOps |
| 自動重啟／HA | ⚠️ `--restart` 只到單機 | 內建 |
| 血緣 | 自己記 | pipeline 自動記（Day 16） |
| 換版 | 換 mount、重跑 | 改 `STORAGE_URI` 或切 Route |
| **重建環境** | 一份 shell script | GitOps（Day 25） |

⚠️ **注意「重建環境」那一列**：
podman 版反而**可能比較好重建**——一份 script 就是全部。
**OpenShift 上如果沒做 GitOps，重建難度是高的。**
所以那一列不是自動贏，是「你有沒有做」的問題。

---

## 我的建議

**如果你正在學：先用 podman 做一次。**

概念一樣、迴圈快、壞了重來的成本接近零。
**你在 podman 上懂的每一件事，到 OpenShift 上都還算數**——
只是換了個名字，而且多了「別人也在用」這個維度。

**如果你在評估導入：**

先誠實勾一次上面那六個框。
**勾不到兩個就別導**——你會付出平台的成本，卻得不到平台在解的那些問題的回報。

**而如果你是被交辦的人**，這篇最有用的地方是：
你現在可以回答「為什麼不用 docker 就好」這個問題了，
**而且答案裡沒有一句是「因為比較先進」。**

---

## 常見問題

**Q：那 docker compose 呢？**
A：同一個結論。compose 比 `podman run` 好管一點（設定寫在檔案裡），
**但斷點完全一樣**——它也沒有多人、沒有排程、沒有稽核。

**Q：可以用 podman + systemd 撐久一點嗎？**
A：可以，`podman generate systemd`（或 Quadlet）能解決「重開機自動起來」。
**這確實把斷點二往後推了**，值得做。
但它解不了多人和稽核。

**Q：k3s / minikube 算中間選項嗎？**
A：算。它們給你 k8s 的 API 和 RBAC，**但沒有 OpenShift AI 那層**
（dashboard、workbench、pipeline、model registry 都要自己裝）。
**如果你要的只是 k8s 而不是 AI 平台**，這是合理的中間站。

**Q：那我前面兩天學的白學了嗎？**
A：沒有。**Day 2 那張圖在 podman 上一樣成立**——
只是每個框變成一個容器而不是一個 CR。
**流程不變，換的是誰來管。**

---

## 明天

Day 4 開始動手，**在一台筆電上把 OpenShift AI 裝起來。**

如果你看完這篇覺得「我們還不需要」——那也是個結論，
**而且是這 30 天能給你最省錢的一個。**

---

**你們現在的模型服務跑在哪？podman/docker、VM，還是已經在 k8s 上？**

{% include lab-env.html %}
