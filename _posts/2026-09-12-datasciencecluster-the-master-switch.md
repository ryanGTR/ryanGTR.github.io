---
layout: post
title: "DataScienceCluster：整套平台的總開關"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-12 09:00:00 +0800
tags: [openshift-ai, rhoai, odh, datasciencecluster, operator, tutorial]
excerpt: "一個 CR 決定你的叢集上有哪些 AI 元件。這篇講它是什麼、什麼時候你會動到它、怎麼改，以及為什麼「先全開再說」是最貴的選擇。"
feedback_question: "你們的 DataScienceCluster 開了幾個元件？有沒有開了但從來沒用過的？"
---

## 1. 這是什麼

裝完 OpenShift AI 的 operator 之後，**叢集上什麼都還沒有**。

你要建一個叫 `DataScienceCluster`（簡稱 DSC）的自訂資源，
在裡面宣告要開哪些元件，operator 才會去把它們裝起來。

```yaml
apiVersion: datasciencecluster.opendatahub.io/v2
kind: DataScienceCluster
metadata:
  name: default-dsc
spec:
  components:
    kserve:        { managementState: Managed }    # 模型服務
    aipipelines:   { managementState: Managed }    # pipeline
    dashboard:     { managementState: Managed }    # 網頁介面
    workbenches:   { managementState: Managed }    # Jupyter
    ray:           { managementState: Removed }    # 不裝
    kueue:         { managementState: Removed }
    trustyai:      { managementState: Removed }
```

> **Java 類比**：Spring Boot 的 auto-configuration。
> 你在設定檔宣告要什麼，框架幫你把那些 bean 裝配起來——
> 差別在這裡裝配的是一整組 operator 和 Deployment。

**`Managed` = 裝它並且持續維持；`Removed` = 不裝（已裝的會移掉）。**

---

## 2. 什麼時機你會動到它

**不是每天。** DSC 是那種「裝機時設一次，之後半年不碰」的東西。
你會回來動它，通常是這四種情況：

| 你遇到的情況 | 要做的事 |
|---|---|
| **要用一個新功能**（例如要跑 pipeline） | 把對應元件從 `Removed` 改成 `Managed` |
| **平台顯示 `Not Ready`，你想知道為什麼** | 看它的 `status.conditions`，逐條讀 reason |
| **要盤點離線鏡像清單** | 開了哪些元件 → 決定要鏡哪些 image |
| **驗收廠商裝的東西** | 這份 CR 就是「他到底裝了什麼」的單一真相 |

最後一項最常被忽略。**要知道一套 OpenShift AI 實際開了什麼，
不要看簡報，看這份 CR。**

---

## 3. 怎麼用

### 看現在開了什麼〔實測〕

```bash
oc get dsc default-dsc -o json | \
  jq -r '.spec.components | to_entries[] | "\(.key)\t\(.value.managementState)"'
```

我的 lab 回這個：

```
aipipelines          Managed
dashboard            Managed
kserve               Managed
workbenches          Managed
feastoperator        Removed
kueue                Removed
llamastackoperator   Removed
modelregistry        Removed
ray                  Removed
trainingoperator     Removed
trustyai             Removed
```

**開四個，關七個。**

### 開一個元件〔實測〕

```bash
oc patch dsc default-dsc --type=merge \
  -p '{"spec":{"components":{"modelregistry":{"managementState":"Managed"}}}}'
```

⚠️ **一定要用 `--type=merge` 或 `--type=json`，不要整份 `oc apply` 蓋過去**——
DSC 裡有很多欄位是 operator 自己填的（例如 kserve 底下的
`rawDeploymentServiceConfig`、`nim`），整份覆蓋會把它們洗掉。

### 看它為什麼 Not Ready〔實測〕

```bash
oc get dsc default-dsc -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.reason}{"\t"}{.message}{"\n"}{end}'
```

```
Ready            False   NotReady   Some modules are not ready: workbenches
ComponentsReady  True
ModulesReady     False   NotReady   Some modules are not ready: workbenches
AIGatewayReady   False   Removed    Module ManagementState is set to Removed
KserveReady      True
```

**這裡有兩個很重要的讀法：**

1. 最上面那個 `Ready` 是**所有模組的 AND**。你開了但沒在用的模組會把它拉紅
2. `AIGatewayReady False` 的 reason 是 **`Removed`**——那是「**我沒開**」，
   不是「它壞了」。關掉的元件也會出現在條件列表裡

**所以看到 `Not Ready` 先別緊張，逐條讀 reason。**
我的叢集長期是 `Not Ready`，而模型服務、pipeline、監控全部正常。

---

## 4. 它在流程的哪一步

**第 0 步，在所有事情之前。**

```
裝 operator → 【建 DSC】 → 有了 kserve → 才能建 InferenceService
                    ↓
              有了 aipipelines → 才能建 DSPA → 才能跑 pipeline
```

上游是 operator 與 `DSCInitialization`（那個 CR 決定 applications namespace，
我的是 `opendatahub`；商用版 RHOAI 是 `redhat-ods-applications`）。

下游是**所有東西**。

---

## 5. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| DSC | `oc get dsc` 有東西、`ComponentsReady=True` | **開的元件數 == 你實際會用的數量**；每一個 `Managed` 你都答得出「它解決我什麼問題」 |

第二欄不是形式主義。理由在下一節。

---

## 6. 什麼時候不需要它——以及為什麼別全開

DSC 本身你一定需要。但**元件不要先全開。**

每開一個元件，你會多付三種成本：

**① 離線鏡像清單長一截。** 你要把那個元件的所有 image 鏡進內網、
掃描、簽章、維護。開一個 `ray` 可能就是好幾 GB。

**② 相依可能多一個 operator。** 有些元件會要求你先裝別的東西。

**③ 平台的 `Ready` 會被它拉紅。** 開了卻沒配好的元件，
會讓整個 DSC 長期顯示 `Not Ready`——然後你就學會忽略那個狀態了。
**而下次真的壞掉時，你不會注意到。**

第三點是最貴的，因為它花掉的不是資源，是**你對這個訊號的信任**。

**建議的順序**：先只開 `dashboard` + `kserve`，把一個模型上線；
需要「這模型怎麼來的」再開 `aipipelines`；需要「它憑什麼上線」再開 `modelregistry`。

> 我的 lab 到現在只開四個。`ray`、`kueue`、`trustyai`、`feast` 我一次都沒需要過——
> **而它們每一個都會讓我的鏡像清單變長。**

---

## 7. 它會怎麼咬你

**① 元件名稱在 3.x 改過。**
2.x 叫 `datasciencepipelines`，3.x 叫 **`aipipelines`**。
照 2.x 的 YAML 寫，DSC 會拒絕。
（怎麼快速判斷版本 → [一張版本試紙](/2026/09/rhoai-version-litmus-test/)）

**② 開了元件不代表它會起來。**
我的 `kserve` 是 `Managed`、DSC 說 `KserveReady=True`，
但兩個 controller 冷啟動後 CrashLoop——因為 **operator 的 CSV 權限清單漏了東西**。
（症狀與修法 → [RBAC 缺權限不會回 Forbidden](/2026/09/rbac-does-not-say-forbidden/)）

**③ `ComponentsReady=True` 不代表服務可用。**
DSC 只看得到叢集裡的東西；你的模型放在叢集外的 S3，它不知道。
（→ [平台全綠，但服務是死的](/2026/09/all-green-but-dead/)）

---

**你們的 DataScienceCluster 開了幾個元件？有沒有開了但從來沒用過的？**

{% include lab-env.html %}
