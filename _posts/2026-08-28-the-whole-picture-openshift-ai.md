---
layout: post
title: "全景圖：從一台筆電到一個被治理的模型服務"
series: "OpenShift AI 實戰紀錄"
date: 2026-08-28 09:00:00 +0800
tags: [openshift-ai, rhoai, odh, mlops, governance, roadmap]
excerpt: "官網文件是按元件分章的——裝什麼、怎麼用某個功能。缺的是「一條完整的路走一次」。這篇是那張圖，標出官網講到哪、我補在哪，以及一個官網目前還在教 2.x 做法的實例。"
feedback_question: "你在導入 AI 平台時，最卡的是哪一站？裝機、上線、治理，還是驗收？"
---

這是這個系列的入口。

如果你被交辦要評估或導入一套 OpenShift AI，你會遇到的第一個問題不是技術，
是**不知道整條路長什麼樣**——官網文件很完整，但它是**按元件分章**的：
安裝一章、模型服務一章、pipeline 一章、監控一章。

每一章都對，但**沒有一章告訴你這些東西串起來是什麼形狀**，
也沒有一章告訴你**串的時候會斷在哪裡**。

這篇是那張圖。

---

## 先講：官網該看哪些

我不是要取代官網。**規格、參數、支援矩陣，官網一定比我準**——那是他們的產品。
以下是我實際用到的幾份，建議先存起來：

| 你要做什麼 | 看這個 |
|---|---|
| 裝 RHOAI（商用版） | [Installing and uninstalling OpenShift AI Self-Managed](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5) |
| 裝 ODH（開源上游） | [Open Data Hub — Quick Installation](https://opendatahub.io/docs/quick-installation-new-operator/) |
| 模型上線 | [Deploying models（KServe RawDeployment）](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.4/html/deploying_models/deploying_models) |
| Pipeline | [Working with data science pipelines](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/2.25/html-single/working_with_data_science_pipelines/working_with_data_science_pipelines) |
| 端到端範例 | [Fraud detection tutorial](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_cloud_service/1/html/openshift_ai_tutorial_-_fraud_detection_example/implementing-pipelines) |

**我的文章不重寫這些。我寫的是照著做之後，會發生什麼它沒說的事。**

---

## ⚠️ 但先看一個實例：官網現在還在教 2.x 的做法

ODH 的「Quick Installation」頁面，**今天（2026-08-29）去看**還是這樣寫的：

> to use "kserve" component, users are required to install **two operators** via
> OperatorHub before enable it in DataScienceCluster CR：
> **Red Hat OpenShift Serverless Operator**、**Red Hat OpenShift Service Mesh Operator**

而我叢集上跑的是 **ODH 3.5.0**：

```bash
oc get csv -A | grep -icE 'serverless|servicemesh|authorino|knative'
# 0

oc get csv -A | grep -i cert-manager
# cert-manager-operator.v1.20.0   Succeeded
```

**一個 Serverless、一個 Service Mesh 都沒有，而 KServe 好好地跑著。**
3.x 需要的是 cert-manager。

同一頁還寫著元件叫 `datasciencepipeline`，而我的 DSC 上它叫 **`aipipelines`**。

這不是官網「錯」——那份文件是 ODH v2 時代寫的，**operator 已經走到 3.5，文件還沒跟上**。
問題是：**你 Google 進來會落在那一頁，而它不會告訴你自己過期了。**

> 這就是這整個系列存在的理由。詳細的版本差異在
> [OpenShift AI 3.x：為什麼你搜到的教學會壞給你看](/2026/08/rhoai-3x-why-your-tutorial-breaks/)。

---

## 整條路：六站

```
① 平台          ② 模型上線        ③ 交付鏈
   OCP + ODH  →   KServe/ISvc  →   Pipeline（prepare→train→eval→gate）
   cert-manager    S3 + registry     Kubeflow Pipelines (DSPA)
        ↓                ↓                    ↓
   ④ 監控          ⑤ 治理            ⑥ 驗收
   Prometheus   →  台帳 / lineage  →  「哪一點會擋下東西？」
   + Grafana       promotion gate     可重跑的證據
```

逐站看，**每一站我都標出「官網講到哪」和「我補什麼」**：

### ① 平台：把 OpenShift AI 裝起來

- **官網講到**：operator 怎麼裝、DSC 怎麼建、元件怎麼開
- **我補**：3.x 相依已經換成 cert-manager（不是 Serverless/Service Mesh）；
  冷啟動之後 KServe controller 會因為 [RBAC 缺權限而 CrashLoop，但不回 Forbidden](/2026/09/rbac-does-not-say-forbidden/)；
  DSC 顯示 `Not Ready` 不一定是壞的——reason 是 `Removed` 代表「我沒開」

### ② 模型上線：讓它變成能打的端點

- **官網講到**：`InferenceService` 的欄位、支援的 runtime
- **我補**：[41 行 YAML 到底生了什麼](/2026/09/what-inferenceservice-actually-creates/)、
  為什麼權重不進 image、**3.x 不會幫你開 Route**（「上線了」≠「打得到」）、
  容器裡沒有 curl 而且 port 不是 8080

### ③ 交付鏈：讓模型是被生產出來的，不是被跑出來的

- **官網講到**：KFP v2 的寫法、DSPA 怎麼建
- **我補**：[四棒 pipeline 的最小可用版](/2026/09/four-step-pipeline-on-openshift-ai/)、
  **一定要關掉 caching**（不然你看到全綠但什麼都沒跑）、
  程式碼身份要自己帶進去（容器裡沒有 git）

### ④ 監控：知道它有沒有在變壞

- **官網講到**：怎麼接 Prometheus
- **我補**：[datasource 不給 uid，九個面板全是壞的](/2026/09/nine-broken-panels/)——
  而 pod、target、dashboard 全部顯示正常

### ⑤ 治理：讓「不該上線的」真的上不了線

- **官網講到**：Model Registry、promotion 的概念
- **我補**：[三顆一樣的模型，一顆上線兩顆被擋](/2026/09/three-identical-models-one-shipped/)——
  決定的是門檻參數不是模型；以及**這條鏈上目前沒有任何一點會因為台帳說「不」而讓部署失敗**

### ⑥ 驗收：怎麼確認對方交的東西是真的

- **官網講到**：（沒有。這不是產品文件的職責）
- **我補**：[版本試紙](/2026/09/rhoai-version-litmus-test/)、
  [llm-d 的前置怎麼問](/2026/08/llm-d-i-cannot-run-it/)、
  以及一條可以直接用的問題：**「這條鏈上，哪一個點會因為檢查沒過而讓部署失敗？」**

---

## 這個系列的規矩

因為我自己被錯的數字騙過好幾次，所以定了三條：

1. **每個數字都是在活著的環境上跑出來的**，不是抄文件、不是憑記憶
2. **推論與實測分開標**。〔實測〕是我親眼看到的，沒標的是我的推論，我會說
3. **做不到的就說做不到**。[llm-d 我跑不起來](/2026/08/llm-d-i-cannot-run-it/)，
   那篇寫的是「它需要什麼、你該怎麼問廠商」

## 我的環境（決定了哪些結論能延用）

- CRC 4.22.7（單節點 VM，10 CPU / 32 GB，**叢集內看不到 GPU**）
- **ODH 3.5.0**（RHOAI 的上游開源版，元件同源但 **namespace 與部分名稱不同**）
- 模型是我自己從零訓練的小 GPT（不是 LLaMA 那個量級）

⚠️ **所以：指令的邏輯可以照用，字串要自己對一次。**
商用版 RHOAI 的 namespace 是 `redhat-ods-*` 那一套，我這邊是 `opendatahub`。

---

**你在導入 AI 平台時，最卡的是哪一站？裝機、上線、治理，還是驗收？**

{% include lab-env.html %}
