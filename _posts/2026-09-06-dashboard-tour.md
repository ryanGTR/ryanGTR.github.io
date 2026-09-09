---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 6：Dashboard 導覽——每個分頁在做什麼"
date: 2026-09-06 09:00:00 +0800
tags: [openshift-ai, odh, dashboard, tutorial, ironman2026]
excerpt: "你的資料科學家不會用 oc，他們會打開那個網頁。這篇把 dashboard 每一頁走一次，標出哪一欄是真的要看的。"
feedback_question: "你們的人比較常用 dashboard 還是 oc？卡在哪一頁最多？"
---

## 這是什麼、解決什麼問題

前幾天都在給 YAML 和 `oc` 指令。但實際情況是：
**用這個平台的人多半不會用 `oc`。**

Dashboard 是他們唯一的入口。而**它顯示的東西跟 `oc` 看到的是同一批物件**——
只是換了個呈現方式，並且**藏起了一部分**。

知道哪些被藏起來，是你能幫上忙的地方。

---

## 什麼時候你會用到

- 要教別人用這個平台
- 有人說「我在畫面上找不到」，你要知道去哪裡查
- 要示範給主管看（`oc` 的輸出沒有說服力）

**什麼時候不要用**：批次操作、要留下可重跑的紀錄、GitOps。
那些走 YAML。

---

## 前置條件

- DSC 裡 `dashboard` 是 `Managed`
- 你的使用者在對應 namespace 上有權限

---

## 步驟一：找到入口

⚠️ **3.x 的 route 名稱換過。** 2.x 是 `odh-dashboard` / `rhods-dashboard`，
3.x 走一個叫 `data-science-gateway` 的東西。

**不要記名字，直接列出來：**

```bash
oc get route -A | grep -iE 'dashboard|data-science-gateway'
```

我的 lab 上有三筆，真正的入口是最後那個：

```
opendatahub       odh-dashboard          odh-dashboard-opendatahub.apps-crc.testing
opendatahub       data-science-gateway   data-science-gateway.apps-crc.testing
openshift-ingress data-science-gateway   rh-ai.apps-crc.testing          ← 這個
```

**前兩個是 redirect，第三個才是 gateway 本體。**
照舊文件找 `odh-dashboard` 會連到，但那是轉址。

![ODH dashboard 首頁]({{ '/assets/img/rhoai/10-odh-home.png' | relative_url }})

左邊那排就是全部功能。

---

## 步驟二：Projects——一切的起點

![Projects 列表]({{ '/assets/img/rhoai/15-odh-projects.png' | relative_url }})

**「Project」就是一個 OpenShift namespace。**

```bash
oc get project llm-serve-demo     # 同一個東西
```

⚠️ **你用 `oc new-project` 建的 namespace，dashboard 看不到。**

要它出現，namespace 上要有這個 label：

```bash
oc label namespace <你的 ns> opendatahub.io/dashboard=true
```

**這是新手最常卡的第一關**，而畫面上不會告訴你原因——
它只是不顯示。

點進一個 project，裡面有五個分頁：
**Workbenches／Connections／Cluster storage／Models／Pipelines**。
接下來幾天講的東西都在這裡。

---

## 步驟三：Connections

![Connections]({{ '/assets/img/rhoai/41-odh-connections.png' | relative_url }})

這頁列的是**貼了 dashboard label 的 Secret**（Day 7 會細講）。

**真正要看的是 `Connected resources` 那一欄。**

它告訴你**有誰在用這份憑證**。如果是 `--`，代表沒有東西在用——
而如果你的 pipeline 同時在正常跑，那就表示
**它用的是另一份你在這頁看不到的憑證**。

盤點憑證的時候，這頁不能當唯一來源：

```bash
# 實際掛進 pod 的 secret
oc get pods -n <ns> -o json | jq -r '
  .items[].spec.containers[].envFrom[]?.secretRef.name,
  .items[].spec.volumes[]?.secret.secretName
  | select(. != null)' | sort -u
```

（`select(. != null)` 別省，不然沒掛 secret 的 volume 會印出一堆 `null`。）

---

## 步驟四：Workbenches

![Workbenches]({{ '/assets/img/rhoai/53-odh-workbenches.png' | relative_url }})

開／停 JupyterLab 的地方。要看的欄位是 **Status** 和 **Size**。

⚠️ **Status 顯示 Starting 卡很久，多半是資源不夠而不是還在下載。**
畫面不會說，去看 pod：

```bash
oc describe pod <workbench-pod> -n <ns> | tail -5
# Warning  FailedScheduling  ... Insufficient cpu
```

按 Open 進去就是標準的 JupyterLab：

![JupyterLab]({{ '/assets/img/rhoai/52-workbench-jupyterlab.png' | relative_url }})

---

## 步驟五：Pipelines——三個分頁要分清楚

這一區最容易搞混，因為有三層東西：

| 分頁 | 是什麼 | 類比 |
|---|---|---|
| **Pipelines** | 你上傳的定義 | 程式碼 |
| **Runs** | 每一次執行 | 執行紀錄 |
| **Artifacts** | 執行產生的東西 | 產物 |

![Pipeline 定義]({{ '/assets/img/rhoai/13-odh-pipeline-defs.png' | relative_url }})

![Runs]({{ '/assets/img/rhoai/11-odh-pipeline-runs.png' | relative_url }})

⚠️ **Runs 頁的綠勾只代表容器 exit code 是 0。**

一個什麼都沒做就結束的 step 也是綠的。
要確認事情真的發生了，**去 Artifacts 頁看產物**：

![Artifacts]({{ '/assets/img/rhoai/14-odh-artifacts.png' | relative_url }})

這頁列出每個 run 產出的檔案、大小、位置。
**檔案在、大小合理、時間戳對**，才算做完。

> 這是整個 dashboard 上我認為最值得教的一件事：
> **綠燈在 Runs 頁，證據在 Artifacts 頁。**

---

## 步驟六：Models

![Model serving]({{ '/assets/img/rhoai/12-odh-model-serving.png' | relative_url }})

列出這個 project 裡的 `InferenceService`。要看的是：

- **Status**——綠色代表 pod 起來了
- **Inference endpoint**——⚠️ 這是**叢集內部**的位址

第二點常被誤會。那個 `*.svc.cluster.local` **從你筆電 curl 不會通**，
而這常被當成「服務壞了」。要從外面打，得自己建 Route（Day 10 會講）。

⚠️ **這頁整排變紅，第一個要懷疑的是模型放的那個 S3 還在不在。**
`InferenceService` 是靠 init container（`storage-initializer`）
去 S3 把模型拉下來的，**S3 連不到，pod 連跑都跑不到**：

```bash
oc get pods -n <ns> | grep predictor
# llm-scratch-predictor-xxx   0/2   Init:CrashLoopBackOff
oc logs <pod> -c storage-initializer --tail=5
# ERROR ... Could not connect to the endpoint URL: "http://10.x.x.x:9000/models"
```

我這台 lab 的 MinIO 跑在宿主上，重開機沒跟著起來，
三個 `InferenceService` 就全部 `READY=False`。
**dashboard 上只會顯示紅色，不會告訴你是 S3 掛了。**

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 進得去 | route 打得開、登入成功 |
| 2 | 看得到你的 project | 沒有就補 `opendatahub.io/dashboard` label |
| 3 | **分頁跟 DSC 對得上** | 開了 pipelines 就該有 Pipelines 分頁 |
| 4 | 別人也進得去 | 用另一個帳號試，**不要只用 kubeadmin 測** |

第 4 項一定要做。**用管理員帳號測，你永遠不會發現權限問題。**
真正的使用者是誰、他看得到什麼，Day 21 會專門講。

---

## 常見問題

**Q：我的 namespace 不出現在 Projects。**
A：補 label：`oc label namespace <ns> opendatahub.io/dashboard=true`。

**Q：畫面上做的事，`oc` 看得到嗎？**
A：看得到，dashboard 只是幫你產 CR。
建一個 workbench 就是建一個 `Notebook`，建 connection 就是建 Secret。
**想學這個平台，最快的方法是在畫面上做一次，然後用 `oc get -o yaml` 看它產了什麼。**

**Q：dashboard 上刪掉東西，底層真的刪了嗎？**
A：是。**沒有回收桶。** 刪 project 就是刪 namespace，
裡面的 PVC 和資料一起走。

**Q：畫面卡住、載不出來。**
A：先看 pod：`oc get pods -n opendatahub | grep dashboard`。
另外 3.x 走 gateway，**gateway 那層也可能是問題**，
兩邊的 log 都要看。

---

**你們的人比較常用 dashboard 還是 `oc`？卡在哪一頁最多？**

---

**你們的人比較常用 dashboard 還是 oc？卡在哪一頁最多？**

{% include lab-env.html %}
