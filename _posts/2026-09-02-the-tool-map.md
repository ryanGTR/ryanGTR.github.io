---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 2：一張圖看懂 OpenShift AI 有哪些東西"
date: 2026-09-02 09:00:00 +0800
tags: [openshift-ai, odh, rhoai, mlops, architecture, ironman2026]
excerpt: "KServe、Kubeflow、Model Registry、TrustyAI、Kueue、Ray、Feast——官網一個一章，但沒有一章說它們是什麼關係。這篇把它們排進 MLOps 流程，每一步標出關鍵指標。"
feedback_question: "這張圖上，你們現在做到哪一步？"
---

## 這是什麼、解決什麼問題

打開 OpenShift AI 的文件，你會看到一長串元件名字。
每個都有自己的一章，每一章都對，但**沒有一章告訴你它們彼此是什麼關係**。

這篇是那張圖：**哪個工具、對應流程哪一步、關鍵指標是什麼。**

先講結論，一張表：

| 流程步驟 | 工具 | 你要看的關鍵指標 |
|---|---|---|
| 0 平台本身 | `DataScienceCluster` | **你開的元件是不是都 Ready**（不是看頂層 Ready） |
| 1 資料進來 | Connection、PVC | 憑證真的連得上（不是「建出來了」） |
| 2 探索與開發 | Workbench | 存檔是不是寫在 PVC、GPU 是不是真的在算 |
| 3 流程固化 | Data Science Pipelines | **產物存不存在**（不是看 run 綠不綠） |
| 4 產物與版本 | S3 artifacts、MLMD | 這個模型是哪份資料、哪組參數來的 |
| 5 登錄 | Model Registry | 線上那版在不在名冊上 |
| 6 上線 | KServe / `InferenceService` | pod 2/2、權重時間戳、對外入口有沒有防護 |
| 7 監控 | Prometheus / Grafana | 延遲、錯誤率、吞吐、**輸入分布** |
| 8 治理 | TrustyAI、gate、稽核紀錄 | 這條鏈上哪一點真的會擋下東西 |

**中間欄大部分人講得出來，右邊那欄才是被交辦的人最後要交的東西。**

---

## 什麼時候你會用到

- 要跟主管解釋「我們要導入的是什麼」
- 要判斷「官網那個元件我們到底需不需要」
- 要寫評估報告或驗收清單，需要一份完整的盤點
- **有人問「這跟 SageMaker / Vertex AI 比呢」** —— 你得先知道自己有什麼

---

## 平台上有哪些元件

問你的叢集，不要問文件：

```bash
oc explain dsc.spec.components
```

ODH 3.5 上是 11 個。我把它們分成三組：

### 你幾乎一定會開的（四個）

| 元件 | 給你什麼 |
|---|---|
| `dashboard` | 網頁介面。**沒它也能用，但沒人會想** |
| `workbenches` | 叢集上的 JupyterLab |
| `kserve` | 模型服務 |
| `aipipelines` | Data Science Pipelines（Kubeflow v2） |

### 看情況的（三個）

| 元件 | 什麼時候需要 |
|---|---|
| `modelregistry` | **模型超過三個、或有人會問「線上是哪版」** 就需要 |
| `trustyai` | 要做偏誤偵測、可解釋性報告（法遵常會問） |
| `kueue` | 訓練任務要排隊搶資源時 |

### 特定場景才開（四個）

`ray`（分散式運算）、`trainingoperator`（分散式訓練）、
`feastoperator`（feature store）、`llamastackoperator`（Llama Stack）。

**建議一開始只開第一組。** 每個元件都是 pod、都吃資源，
單節點上全開會直接卡住。要加隨時能加。

---

## 一條路走一次

把元件排進流程，長這樣：

![OpenShift AI 元件流程圖：DataScienceCluster 之下，資料經 Connection、Workbench、Pipelines 到 S3 artifacts + MLMD，再到 Model Registry、KServe InferenceService、Prometheus/Grafana](/assets/img/rhoai/day02-tool-map.png)

**三個容易誤會的地方：**

1. **Connection 不是 CRD**，是一個貼了 label 的 Secret。
   所以 `oc get connections` 查不到東西。
2. **Model Registry 不在這條路的必經之處**。
   你可以完全不用它就把模型上線——**這正是問題**，
   因為那樣就沒有東西能回答「線上跑的是哪一版」。
3. **KServe 不會幫你建 Route**。
   `oc get isvc` 給你的 URL 是 `*.svc.cluster.local`，只有叢集內打得到。

---

## 這套東西不包含什麼

這是評估時最需要先講清楚的一節，因為**期待錯了後面全錯**。

| 你可能以為有 | 實際上 |
|---|---|
| 現成的模型 | **沒有。** 平台是跑模型的地方，不附模型 |
| 資料標註工具 | 沒有 |
| Feature store | 有（`feastoperator`），但要自己開、自己接 |
| A/B 測試框架 | KServe 有 canary 流量分配，**但實驗設計要自己做** |
| 自動 retrain | 沒有。你要自己用 pipeline + 排程接 |
| 「模型上線前自動擋」 | **沒有。gate 要自己設，這是第五部的主題** |

最後一列最重要。**平台給你的是「可以擋」的能力，不是「已經在擋」的機制。**
兩者之間差的是你自己要寫的東西——這個差距我 Day 28 會拆開講。

---

## 怎麼確認你的叢集長這樣

| | 檢查 | 指令 |
|---|---|---|
| 1 | DSC 存在 | `oc get dsc` |
| 2 | 你開了哪些元件 | `oc get dsc -o jsonpath='{.items[0].spec.components}' \| jq` |
| 3 | 開的那幾個 Ready | `oc get dsc -o json \| jq '.items[0].status.conditions[]'` |
| 4 | dashboard 分頁對得上 | 開了 pipelines 就該有 Pipelines 分頁 |

第 4 項是最快的對帳：**DSC 開什麼，dashboard 就長什麼。**

---

## 常見問題

**Q：ODH 跟 RHOAI 差在哪？**
A：ODH 是上游開源版，RHOAI 是 Red Hat 的商業版。
CRD 和元件絕大部分相同，主要差別是 **namespace 名稱**
（`opendatahub` vs `redhat-ods-applications`）、支援與部分商業元件。
**跟著 ODH 教學做 RHOAI，namespace 要換。**

**Q：這跟 Kubeflow 是什麼關係？**
A：Data Science Pipelines 的本體**就是** Kubeflow Pipelines v2，
KServe 也是 Kubeflow 生態出來的。
OpenShift AI 做的事是把這些上游專案打包、接上 OpenShift 的權限與網路、
加一個 dashboard。**所以 KFP 的知識可以直接用。**

**Q：一定要全部用嗎？**
A：不用。很多團隊實際上只用 Workbench + KServe，
pipeline 用既有的 CI/CD 跑。**這是合理的選擇**——
但要知道你放棄的是什麼（血緣、產物版本、可重跑）。

**Q：2.x 的教學可以照抄嗎？**
A：不行。3.x 預設不再需要 Serverless / Service Mesh，元件也改過名。
Day 24 會專門講怎麼判斷一篇教學是哪個版本的。

---

## 明天

Day 3 先問一個該在裝機之前問的問題：**這些用 podman 做不行嗎？**
社群教的都是那個，而它走得比多數人以為的遠——**該換的交叉點在哪，明天講。**

---

**這張圖上，你們現在做到哪一步？**

{% include lab-env.html %}
