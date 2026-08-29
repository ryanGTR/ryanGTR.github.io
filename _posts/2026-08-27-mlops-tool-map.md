---
layout: post
title: "這一堆工具各是誰：OpenShift AI 的角色分工表"
date: 2026-08-27 09:00:00 +0800
tags: [openshift-ai, rhoai, odh, mlops, kserve, kubeflow-pipelines, tutorial]
excerpt: "KServe、DSPA、storage-initializer、MinIO、Harbor、Prometheus、cert-manager⋯⋯第一次看到會矇。這篇把每個角色用一句話定位、對應到流程哪一步、以及那一步該看什麼指標——並區分「跑完了」和「做對了」。"
feedback_question: "你們現在這條線上，哪個角色是缺的？（沒有 registry？沒有 pipeline？還是沒有台帳？）"
---

如果你第一次看 OpenShift AI 的架構圖，大概會有這個反應：

> 我只是要讓一個模型能被打，為什麼要這麼多東西？

因為**「訓練一個模型」和「經營一個模型服務」是兩件事**。

前者你在筆電上跑個 Python 就行。後者要回答：這顆模型哪來的、誰核准的、
現在健不健康、壞了怎麼換回去、下次還能不能做出一樣的。

每多一個問題，就多一個角色。這篇是那份角色表。

---

## 一張圖：誰跟誰講話

```
     ┌─────────── 底座 ───────────┐
     │  OpenShift (k8s)           │
     │  ODH / RHOAI operator      │  ← 管理下面所有元件的開關
     │  cert-manager              │  ← 3.x 的必要相依
     └────────────┬───────────────┘
                  │
   ┌──────────────┼──────────────────┐
   │              │                  │
┌──▼───┐   ┌──────▼──────┐   ┌───────▼────────┐
│ 倉庫 │   │  生產模型   │   │   服務模型     │
│      │   │             │   │                │
│MinIO │◄──┤ Pipelines   ├──►│ KServe         │
│(S3)  │   │ (DSPA)      │   │ InferenceService│
│      │   │             │   │  └ storage-init│
│Harbor│───┼─────────────┼──►│    (去 S3 拿權重)│
│(image)│  │             │   │                │
└──────┘   └─────────────┘   └───────┬────────┘
                                     │
                        ┌────────────┴──────────┐
                        │                       │
                  ┌─────▼─────┐        ┌────────▼────────┐
                  │  看它     │        │   記它          │
                  │Prometheus │        │ 台帳 / lineage  │
                  │Grafana    │        │ Model Registry  │
                  └───────────┘        └─────────────────┘
```

---

## 逐個角色

### 底座

| 角色 | 一句話 | Java 類比 |
|---|---|---|
| **OpenShift** | Kubernetes 加上企業要的東西（Route、SCC、內建 registry） | 應用伺服器 |
| **ODH / RHOAI operator** | 一個總開關。你在 `DataScienceCluster` 裡開哪些元件，它就裝哪些 | Spring Boot 的 auto-configuration：宣告要什麼，它幫你裝配 |
| **cert-manager** | 自動簽發與續期叢集內部憑證 | 沒有直接對應；想成「幫你管 keystore 的服務」 |

> ⚠️ **cert-manager 是 3.x 才需要的。** 2.x 需要的是 Serverless + Service Mesh，
> 那兩個在 3.x 已經拿掉了。照 2.x 的清單裝會缺這個，而缺了它 KServe 起不來。

### 倉庫：東西放哪

| 角色 | 放什麼 | 為什麼要分開 |
|---|---|---|
| **MinIO（或任何 S3）** | **模型權重**、pipeline 的中間產物 | 模型很大、會常換 |
| **Harbor（或任何 registry）** | **容器 image** | image 不可變、要簽章、要掃描 |

**這兩個為什麼不能合成一個？** 因為它們的生命週期完全不同：

- image 是「程式碼的成品」——建好就不該再變，要簽章、要掃 CVE、要走版本流程
- 模型是「資料的成品」——可能一天換三次

把模型打包進 image，等於每次換模型都要重跑一次完整的建置與掃描流程。

> **Java 類比**：你的 jar 不會把 `application.yml` 和資料庫內容打包進去。
> 同樣的道理，只是這裡的「設定」是幾百 MB 的權重。

⚠️ **這兩個在我的 lab 是跑在主機上的容器，不在叢集裡**——
所以叢集的健康檢查看不到它們。這件事咬過我一次。

### 服務模型：讓它能被打

| 角色 | 做什麼 |
|---|---|
| **KServe** | 模型服務的 operator。你給它一個 `InferenceService`，它生出 Deployment + Service |
| **InferenceService（ISvc）** | 你要寫的那份 YAML。宣告：用哪個 image、模型在哪、開哪個 port |
| **storage-initializer** | KServe 自動塞進去的 init container。開機前把 S3 的模型抓到本地 |
| **kube-rbac-proxy** | ODH 自動加的 sidecar，用叢集 RBAC 保護 metrics 端點 |

**你寫一個容器，跑起來是三個。** 中間兩個是平台幫你加的。

> **為什麼要 storage-initializer**：它讓你的應用程式只要讀本地路徑，
> 完全不用知道 S3 存在，也不用在程式裡放任何 credential。

### 生產模型：讓它是被做出來的，不是被跑出來的

| 角色 | 做什麼 |
|---|---|
| **Kubeflow Pipelines（KFP v2）** | 把「準備資料→訓練→評估→放行」寫成有順序的步驟 |
| **DSPA** | OpenShift AI 上的 KFP 實例。建一個 DSPA，它生出七個 pod |
| **Argo Workflows** | 藏在 DSPA 底下真正跑那些容器的引擎 |
| **MariaDB** | 存 run 的紀錄與 lineage。⚠️ 資料表是 `utf8mb4`（中文存得進去），但**伺服器與連線的預設字元集是 `latin1`**——自己寫的查詢或備份沒指定就會拿到亂碼 |

**沒有 pipeline 會怎樣？** 你還是做得出模型——在某台機器上跑個 notebook。
但你沒辦法回答「這顆模型是怎麼來的」，因為那個過程沒有留下任何可查的東西。

> **Java 類比**：等於你有一個能編譯的專案，但沒有 CI。
> 東西做得出來，只是沒人知道是誰在哪台機器上用哪個版本做的。

### 看它：知道有沒有在變壞

| 角色 | 做什麼 |
|---|---|
| **Prometheus** | 定時去抓你的服務的 `/metrics`，存成時間序列 |
| **Grafana** | 把那些數字畫成人看得懂的圖 |

模型服務的指標比一般 API 多一層：除了延遲和錯誤率，還要看
**輸入分布有沒有漂移**、**輸出有沒有退化**。

### 記它：這顆模型憑什麼上線

| 角色 | 做什麼 |
|---|---|
| **台帳（model registry）** | 一份紀錄：這顆模型的 digest、指標、資料來源、程式碼版本、現在什麼狀態 |
| **lineage** | 那顆模型的來歷：用了哪份資料、哪一版程式碼、哪一次 run |
| **promotion gate** | 決定它能不能從候選變成 production 的那道檢查 |

**這一層是最容易被跳過的**，因為跳過它東西照樣會動。
差別在出事的時候：你能不能在五分鐘內回答「線上這顆是什麼、誰核准的、怎麼換回去」。

> ⚠️ RHOAI 有內建的 **Model Registry** 元件，但它需要外部 MySQL。
> 我的 lab 用的是最土的做法：一個 JSON 檔掛成 ConfigMap。
> **能回答問題的爛台帳，勝過沒有台帳。**

---

---

角色講完了。下一篇是實際要用的那張表：
[**哪一步、用什麼工具、看什麼指標**](/2026/08/which-tool-which-step-which-metric/)——
以及為什麼「跑完了」和「做對了」要分成兩欄。

---

**你們現在這條線上，哪個角色是缺的？沒有 registry？沒有 pipeline？還是沒有台帳？**

{% include lab-env.html %}
