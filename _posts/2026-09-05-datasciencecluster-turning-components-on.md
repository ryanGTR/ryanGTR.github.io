---
layout: post
title: "Day 5：DataScienceCluster——平台元件的總開關"
series: "OpenShift AI 入門 30 天"
date: 2026-09-05 09:00:00 +0800
tags: [openshift-ai, odh, datasciencecluster, dsc, tutorial]
excerpt: "裝完 operator 之後，平台上什麼都沒有。要有東西，得先建 DataScienceCluster。這篇講它管什麼、怎麼開關元件、怎麼讀它的狀態。"
feedback_question: "你們評估 AI 平台時，是先列清單再挑元件，還是先全開再砍？"
---

## 這是什麼、解決什麼問題

裝完 ODH／RHOAI 的 operator，你打開 dashboard——**什麼都沒有**。

因為 operator 只是「有能力裝那些元件的程式」，
**它不會自作主張裝任何東西**。要有東西，你得告訴它要哪些。

那份清單就是 `DataScienceCluster`（DSC）：**整個平台唯一的總開關。**

```bash
oc get dsc
# NAME          READY   REASON
# default-dsc   False   NotReady
```

（`READY` 那欄先別緊張，**步驟四**會解釋為什麼它常態是 `False`。）

一個叢集只會有一個。它是 **cluster-scoped** 的，不屬於任何 namespace。

---

## 什麼時候你會用到

- 第一次裝平台（一定要建，不然沒東西）
- 要加一個元件（例如「我們要開始用 model registry 了」）
- 要關一個元件（省資源、或是安全評估沒過）
- **除錯**：dashboard 上某個功能找不到，第一個該看的就是這裡

---

## 前置條件

- ODH／RHOAI operator 已安裝
- `DSCInitialization` 已經 Ready（operator 自己建的，管全域設定）

```bash
oc get dsci
```

---

## 步驟一：看有哪些元件可以開

```bash
oc explain dsc.spec.components
```

ODH 3.5 上有 11 個（⚠️ 3.6 起變 17 個，見文末補記）：

| 元件 | 給你什麼 |
|---|---|
| `dashboard` | 網頁介面 |
| `workbenches` | 叢集上的 JupyterLab |
| `kserve` | 模型服務（InferenceService） |
| `aipipelines` | Data Science Pipelines |
| `modelregistry` | 模型登錄 |
| `trustyai` | 偏誤偵測與可解釋性 |
| `ray` | 分散式運算 |
| `trainingoperator` | 分散式訓練 |
| `kueue` | 批次任務排隊 |
| `feastoperator` | Feature store |
| `llamastackoperator` | Llama Stack |

⚠️ **元件名稱在 3.x 改過。** 例如 pipelines 從 `datasciencepipelines`
改成 `aipipelines`。**照 2.x 教學貼 YAML 會直接被拒絕**，
而錯誤訊息只會說有個不認識的欄位。

`oc explain` 是唯一不會過期的清單——它讀的是你叢集上的 CRD。

---

## 步驟二：三種狀態

每個元件有 `managementState`，值有三個：

| 值 | 意思 |
|---|---|
| `Managed` | 開。operator 負責裝、負責維持成你寫的樣子 |
| `Removed` | 關。operator 會把它刪掉 |
| `Unmanaged` | **開，但 operator 不管它** |

第三個容易誤解。`Unmanaged` **不是關掉**——
它是「這東西可以存在，但我不幫你維護」。

實際會用到的情境：某個元件在你的版本上不再由 operator 託管
（我的 lab 上 `kueue` 就是 `Unmanaged`），
或是你要手動改它的設定而不想被 operator 蓋回去。

**這對評估很重要：盤點平台有什麼元件時，只數 `Managed` 會漏。**

---

## 步驟三：建一個

```yaml
apiVersion: datasciencecluster.opendatahub.io/v2
kind: DataScienceCluster
metadata:
  name: default-dsc
spec:
  components:
    dashboard:      { managementState: Managed }
    workbenches:    { managementState: Managed }
    kserve:         { managementState: Managed }
    aipipelines:    { managementState: Managed }
    modelregistry:
      managementState: Managed
      registriesNamespace: odh-model-registries
    trustyai:       { managementState: Removed }
    ray:            { managementState: Removed }
    trainingoperator: { managementState: Removed }
```

**建議一開始只開你真的要用的。** 每個元件都是 pod、都吃 CPU 和記憶體，
單節點的 lab 上全開很容易直接卡住。要加隨時可以加。

```bash
oc apply -f dsc.yaml
```

**驗證這一步：**

```bash
oc get dsc default-dsc -o jsonpath='{.status.phase}'
oc get pods -n opendatahub
```

元件 pod 陸續起來要幾分鐘。

---

## 步驟四：⭐ 讀狀態（這裡有個陷阱）

```bash
oc get dsc default-dsc -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}'
```

我的 lab 上長這樣（節錄）：

```
Ready=False               ← ?!
ComponentsReady=False     ← ?!
DashboardReady=True
KserveReady=True
AIPipelinesReady=True
WorkbenchesReady=True
ModelRegistryReady=True
TrustyAIReady=False
RayReady=False
KueueReady=False
```

**平台是好的，dashboard、模型服務、pipeline 全都在正常運作。**
但頂層 `Ready` 是 `False`。

原因是：**沒開的元件也會出現在 conditions 裡，狀態是 `False`**，
而頂層 `Ready` 是全部 AND 起來的。
`Removed` 的東西當然不 Ready——但它不 Ready 是你要的。

> **所以 `Ready=False` 在這個 CR 上不代表故障。**
> 要判斷平台健康，得逐條看**你開的那幾個**是不是 True。

反過來也要小心：**不能因為知道它常態 False 就一律無視。**
逐條看到 `False` 時再看一層 `reason`，才分得出這個 False 是哪一種：

```bash
oc get dsc default-dsc -o json | jq -r '
  .status.conditions[] | select(.status=="False") | "\(.type)\t\(.reason)"'
```

我叢集上實際出現的 reason 有四種：

| reason | 意思 | 要查嗎 |
|---|---|---|
| `Removed` | 你沒開，本來就該是這樣 | 不用 |
| `NotReady` | 上層彙總條件（`Ready`／`ComponentsReady`／`ModulesReady`）被下面拉低 | 不用，往下看個別元件 |
| `PreConditionFailed` | 前置條件沒滿足就沒裝（我這台的 `KueueReady` 屬於這種，因為它是 `Unmanaged`） | 看你有沒有要用它 |
| `DeployFailed`（帶 message） | **真故障** | ⚠️ 一定要查 |

我這次重跑就吃到一個真的——`ModelRegistryReady=False`、
`reason: DeployFailed`，訊息是 operator 的 ServiceAccount 想授出自己沒有的 RBAC 權限。
**沒看 `reason` 的話，它會被我當成「喔那個沒開」放過去。**

我覺得這值得單獨拿出來講，是因為它會影響監控：
如果你拿 `dsc.status.Ready` 去接告警，**它會永遠在響**，
然後大家就會學會忽略它。

一行判斷你在意的元件——**請用精確比對，不要用 regex**：

```bash
oc get dsc default-dsc -o json | jq -r --argjson want '
  ["DashboardReady","KserveReady","WorkbenchesReady","AIPipelinesReady","ModelRegistryReady"]' '
  .status.conditions[] | select(.type as $t | $want|index($t)) |
  "\(.type)=\(.status)"'
```

我原本寫的是 `select(.type|test("Dashboard|Kserve|Workbench|AIPipelines"))`，
發文前重跑才發現它在新版上會多抓到
`KserveLLMInferenceServiceDependencies=False`、`WorkbenchesV2Ready=False`——
**我自己的指令重現了這篇在講的那個誤判。** 原因寫在文末的補記。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | DSC 存在 | `oc get dsc` |
| 2 | **你開的元件都 True** | 上面那行 jq（不是看頂層 Ready） |
| 3 | pod 都起來 | `oc get pods -n opendatahub` |
| 4 | dashboard 上看得到對應分頁 | 開了 pipelines 就該有 Pipelines 分頁 |

第 4 項是最直觀的對帳：**DSC 開什麼，dashboard 就長什麼。**
分頁沒出現，回頭看 DSC，不用去猜是不是權限問題。

---

## 常見問題

**Q：apply 被拒絕，說某個欄位不認識。**
A：八成是元件改名了，或你用的 apiVersion 是 `v1` 而範例是 `v2`。
兩個版本都還 served，但 **storage version 是 v2**：

```bash
oc get crd datascienceclusters.datasciencecluster.opendatahub.io \
  -o jsonpath='{range .spec.versions[*]}{.name} storage={.storage}{"\n"}{end}'
```

**Q：可以建兩個 DSC 嗎？**
A：不行，設計上就是一個叢集一份。
**要做環境隔離請用不同叢集或 namespace 層的權限**，不是靠多個 DSC。

**Q：把元件改成 `Removed`，資料會不見嗎？**
A：元件的 pod 和設定會被刪掉。
**PVC 通常會留著，但別賭這個**——關掉一個元件之前先確認它的資料在哪、
有沒有備份。這是「先在 lab 練一次」比較划算的操作。

**Q：改了 DSC 多久生效？**
A：operator 是持續調諧的，通常幾秒內開始動作，
元件完全起來要幾分鐘。看 operator log 最準：

```bash
oc logs -n openshift-operators -l control-plane=controller-manager --tail=50
```

---

**你們評估 AI 平台時，是先列清單再挑元件，還是先全開再砍？**

{% include lab-env.html %}
---

## 📌 補記：這篇發文前，我的叢集自己升版了

這段本來不在稿子裡。發文前我習慣把文章裡每一條指令在叢集上重跑一次，
結果第一條就對不上——**operator 從 `3.5.0` 變成了 `3.6.0-ea.1`**。

查下去原因很單純：

```bash
oc get subscription opendatahub-operator -n openshift-operators -o yaml
# spec:
#   channel: fast-3                    ← early-access 通道
#   installPlanApproval: Automatic     ← 預設值，有新版就自動裝
```

`fast-3` 這個 channel 會給你 3.x 的 early access 版本，
而 `installPlanApproval` 的預設是 `Automatic`——**兩個加起來，operator 會在你不知情的時候自己換版。**
我甚至沒有登入這台叢集，它就升好了。

### 那次升版改了三件事

| | 3.5.0 | 3.6.0-ea.1 |
|---|---|---|
| `dsc.spec.components` 元件數 | 11 | **17**（多了 `aigateway`、`trainer`、`ogx`、`mlflowoperator`、`sparkoperator`、`mcplifecycleoperator`） |
| `trainingoperator` / `llamastackoperator` | 正常元件 | **標記 Deprecated**（改用 `trainer` v2、`ogx`） |
| `status.conditions` 條目 | 只有元件對應的那幾條 | **24 條**，多出 `ModulesReady`、`WorkbenchesV2Ready`、`KserveLLMInferenceServiceDependencies`、`KserveLLMInferenceServiceWideEPDependencies`、`BatchGatewayReady`、`ModelsAsAServiceReady` 等 |

第三項就是害我那行 jq 出包的原因：
`test("Kserve|Workbench")` 這種模糊比對，在多了
`KserveLLMInferenceServiceDependencies`、`WorkbenchesV2Ready` 之後，
會把一堆與我無關的 `False` 一起撈進來。

**這正好是本文主題的加強版**：不只 `Ready=False` 會誤導，
連你自己寫來避開誤導的那行指令，也會在下一次升版時開始誤導你。
**條件用精確比對，不要用 regex。**

### 我做的處理

不降版。理由是：OLM 沒有 downgrade 路徑，而且真正決定 `oc explain` 印幾個元件的是 **CRD**，
把 CSV 降回 3.5 並不會把 CRD 降回去；要換 CRD 就會動到 DSC 本身，等於重建整個平台狀態。
為了讓文章數字好看去冒這個險，不划算。

改成把版本釘住：

```bash
oc patch subscription opendatahub-operator -n openshift-operators \
  --type=merge -p '{"spec":{"installPlanApproval":"Manual"}}'
```

改成 `Manual` 之後，有新版時 OLM 只會建一個等你批准的 InstallPlan，不會自己裝：

```bash
oc get installplan -n openshift-operators   # APPROVED=false 的就是在等你
```

### 給你的建議

- **lab 可以吃 `fast`，正式環境不要。** 至少把 `installPlanApproval` 設成 `Manual`。
- **你的平台版本是會自己動的**，所以任何「我們平台有哪些元件」的盤點文件都有保存期限。
  盤點請用 `oc explain`／`oc get dsc -o yaml` 現場拉，不要抄 wiki 上的表。
- 如果你在寫評估報告：**把 operator 版本和取得日期一起寫進去**，
  不然三個月後沒人知道那份清單是對著哪一版數的。
