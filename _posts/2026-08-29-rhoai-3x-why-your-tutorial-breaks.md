---
layout: post
title: "OpenShift AI 3.x：為什麼你搜到的教學會壞給你看"
series: "OpenShift AI 實戰紀錄"
date: 2026-08-29 09:00:00 +0800
tags: [openshift-ai, rhoai, odh, kserve, mlops, version-migration]
excerpt: "2.x 是把 AI 平台架在 Service Mesh / Serverless 上，3.x 把那層整個拆掉。網路上的教學絕大多數是 2.x 的，照做會缺 operator、模型上線模式不同、GPU 設定物件換了名字——而且 2.25 不能升級到 3.x。這篇列出實測到的差異與一組可以當場用的檢查。"
feedback_question: "你們 production 上跑的是 2.x 還是 3.x？升級（或重裝）的計畫怎麼排的？"
---

如果你正要導入 Red Hat OpenShift AI（RHOAI），而且是照著搜尋結果做的，
先確認一件事：**那篇教學是 2.x 還是 3.x 的。**

差別不是「新版多了幾個功能」。3.x 把 2.x 的底層換掉了，
所以 2.x 的教學在 3.x 上不是「有點不一樣」，是**步驟直接不存在**。

而且最麻煩的一條在最後：**2.25 不能升級到 3.x。**

> 本文的〔實測〕來自我自己筆電上的 CRC 4.22.7 + ODH 3.5.0
> （ODH 是 RHOAI 的上游開源版，元件同源），2026-08-29 重跑確認。
> 〔文件〕來自 Red Hat 官方文件。沒有標的是我的推論，我會說明。

---

## 一句話版本

**2.x 是「把 AI 平台架在 Service Mesh／Serverless 上」，3.x 是「把那層拆掉，改用 Kubernetes 原生的部署方式」。**

用 Java 比喻：2.x 像把服務跑在一個功能很多的應用伺服器上——附帶路由、認證、自動伸縮，
但你得先把那台伺服器裝起來、調好。3.x 像改成 Spring Boot 那種自帶容器的可執行檔：
少了一層中介，排錯回到熟悉的路徑，代價是路由和伸縮要自己接。

---

## 三個根本改變

### 1. 相依 operator 換了一整批

| | 2.x 需要 | 3.x 需要 |
|---|---|---|
| 模型上線 | OpenShift Serverless（Knative）+ Service Mesh + Authorino | **cert-manager** |
| 分散式工作負載 | CodeFlare + Ray | **Kueue** + Ray |
| Service Mesh | 必要 | 只有 Llama Stack 才要〔文件〕 |

自己驗一次：

```bash
oc get csv -A | grep -icE 'serverless|servicemesh|authorino|knative'
# 3.x → 0 〔實測〕

oc get csv -A | grep -i cert-manager
# cert-manager-operator.v1.20.0   Succeeded 〔實測〕
```

**為什麼要在意**：這直接決定離線鏡像清單的內容與體積。
照 2.x 的清單去鏡像，3.x 會**缺 cert-manager，KServe 起不來**；
反過來會多鏡好幾 GB 用不到的 Service Mesh。

### 2. 模型上線的預設模式換了

| | 2.x | 3.x |
|---|---|---|
| KServe 預設 | Serverless（Knative Serving） | **RawDeployment**（原生 Deployment + Service）〔實測〕 |
| Serverless 模式 | 可用 | 自 2.25 起 deprecated〔文件〕 |
| ModelMesh（多模型共用） | 可用 | 自 2.19 起 deprecated〔文件〕 |

lab 上的 DSC 長這樣〔實測〕：

```bash
oc get dsc default-dsc -o jsonpath='{.spec.components.kserve}'
```
```json
{"managementState":"Managed","rawDeploymentServiceConfig":"Headed",
 "nim":{"airGapped":false,"managementState":"Managed"},
 "modelsAsService":{"managementState":"Removed"},
 "wva":{"managementState":"Removed"}}
```

**做法上的差別**：2.x 要處理 Knative 的 Route／Ingress Gateway／自動縮至零；
3.x 就是一個普通的 Deployment + Service。

⚠️ **3.x 不會自動給你對外入口。**「模型上線了」和「模型打得到」在 3.x 是兩件事——
lab 上是自己 `oc create route` 之後才打得到的〔實測〕。驗收時要分開確認。

![InferenceService 詳情：RawDeployment 模式](/assets/img/rhoai/isvc-raw.png)

### 3. GPU 設定物件換了名字

| | 2.x | 3.x |
|---|---|---|
| GPU 資源設定檔 | Accelerator Profile | **Hardware Profile**〔文件〕 |
| 位置 | Dashboard → Settings | Dashboard → Settings → Hardware profiles |

不只是改名，涵蓋範圍變大了（不只加速器，也管 CPU／記憶體的可選規格）。

---

## 一組可以當場用的檢查

**版本試紙**——對方的文件或簡報裡出現這些字，那是 2.x 的材料：

```
Accelerator Profile
Knative / ServiceMeshMemberRoll
datasciencepipelines   ← 3.x 叫 aipipelines
```

`oc` 這邊也有一條硬證據，DSC 的 API 版本〔實測〕：

```bash
oc get crd datascienceclusters.datasciencecluster.opendatahub.io \
   -o jsonpath='{range .spec.versions[*]}{.name}{" storage="}{.storage}{"\n"}{end}'
# v1  storage=false
# v2  storage=true      ← 3.x 的 storage version 是 v2
```

---

## 最麻煩的一條：不能升級

〔文件〕**3.0 起不支援從 2.25 升級。** 之後的版本才會補上升級路徑。

這條的實際後果，如果你正在做 PoC：

- PoC 是新裝，正好避開這個限制 → **直接裝 3.x**
- 若為求保險裝了 2.25，**PoC 結束要正式上線時等於重裝一次**
- 更糟的是，PoC 期間驗證過的東西（Serverless 模式、Accelerator Profile）在 3.x 根本不存在，**驗證結果不能延用**

所以「裝舊版比較保險」在這裡是反的。

---

## 一個要小心的細節：平台 Ready 不等於服務可用

順帶講一個我今天早上才踩到的。我的叢集 DSC 顯示 **`Not Ready`**：

![DSC Conditions：Ready False，但服務是好的](/assets/img/rhoai/dsc-conditions.png)

```
Ready            False   NotReady   Some modules are not ready: workbenches
ComponentsReady  True
ModulesReady     False   NotReady   Some modules are not ready: workbenches
AIGatewayReady   False   Removed    Module ManagementState is set to Removed
```

看起來很嚴重。但同一時間，模型服務是好的、推論打得通、監控有資料。
`Not Ready` 的原因是我沒開 workbenches，而 `AIGatewayReady False` 的 reason 寫著
`Removed`——那是「我沒開」，不是「它壞了」。

**驗收時不要只看最上面那個 Ready。要看 Conditions 逐條的 reason，
再去打實際的端點。** 這兩件事我後面會單獨寫一篇。

---

## 我還沒查證的

誠實列出來，免得你當成結論：

- 2.25 是否明列支援 OCP 4.20（文件只寫「4.16 or greater」）
- 3.4 之後是否已有更新的 3.x（我的資料抓於 2026-08-22）
- 3.x 的 operator namespace 與管理者群組是否仍是 `redhat-ods-operator`／`rhods-admins`
  ——我的 lab 是 ODH，namespace 叫 `opendatahub`，**不能直接照抄**
- `aigateway`／`ogx`／`mcplifecycleoperator` 這幾個 3.x 新元件的正式定位，我沒實際開過

---

如果你也在做這件事，我特別想知道：
**你們 production 上跑的是 2.x 還是 3.x？升級（或重裝）的計畫怎麼排的？**

{% include lab-env.html %}
