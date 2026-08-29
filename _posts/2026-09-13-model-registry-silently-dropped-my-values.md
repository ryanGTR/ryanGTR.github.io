---
layout: post
title: "Model Registry：登記成功，但每一個值都是 0"
date: 2026-09-13 09:00:00 +0800
tags: [openshift-ai, rhoai, model-registry, governance, api, tutorial]
excerpt: "OpenShift AI 的模型台帳。這篇從開元件、備 DB、建實例到用 API 登記一顆模型全走一次——最後在 API 上踩到一個會讓你的治理數據全部歸零、而且不會報錯的坑。"
feedback_question: "你們的模型台帳在哪？Excel、Confluence、還是有工具？出事時查得到「線上這顆是誰核准的」嗎？"
---

## 1. 這是什麼

**一份紀錄：線上這顆模型是什麼、哪來的、誰核准的、現在什麼狀態。**

OpenShift AI 有一個內建元件叫 Model Registry，它是這件事的工具化版本。

> **Java 類比**：像 Nexus／Artifactory 對 jar 的角色。
> 你的 jar 不會只躺在某台機器的資料夾裡，它有版本、有座標、有誰上傳的。
> 模型也該有。

---

## 2. 什麼時機需要它

不是「因為它是最佳實踐」。是因為**你答不出下面某一個問題**：

| 你被問到 | 沒有台帳的話 |
|---|---|
| 線上這顆模型是哪一次訓練出來的？ | 翻聊天記錄 |
| 誰核准它上線的？ | 「應該是我吧」 |
| 它上線前的評估數字是多少？ | 要重跑一次才知道 |
| 要換回上一版，上一版是哪一顆？ | 找不到 |

**如果這四題你都答得出來，你可能還不需要這個元件**——
一份大家都在維護的表格也能回答。

**需要工具的時機是：回答這些問題的成本開始高於維護工具的成本。**
通常發生在模型超過個位數、或者換版頻率超過每月一次的時候。

---

## 3. 怎麼用

四步。以下每一段我都跑過。

### 3-1　在 DSC 開元件

```bash
oc patch dsc default-dsc --type=merge \
  -p '{"spec":{"components":{"modelregistry":{"managementState":"Managed"}}}}'
```

約一分鐘後：

```bash
oc get dsc default-dsc -o jsonpath='{range .status.conditions[?(@.type=="ModelRegistryReady")]}{.status}{"\n"}{end}'
# True
```

⚠️ **但 `ModelRegistryReady=True` 不代表你可以登記模型了。**
這一步只是把 operator 和 UI 裝起來。真正的 registry 還沒建。

### 3-2　準備一個資料庫（要你自己給）

Registry 的中繼資料要存在關聯式資料庫。CR 的 spec 要求
`mysql`（必填 `database` / `host` / `username`）或 `postgres`（必填 `database`）。

**這是最容易卡住的一步**：官方文件把它當前置條件，但你要在
「元件已經 Ready」之後才會發現這件事。

lab 版（正式環境請用真的 DB，不要用 `emptyDir`）：

```yaml
apiVersion: v1
kind: Secret
metadata: { name: mr-db, namespace: odh-model-registries }
stringData:
  MYSQL_ROOT_PASSWORD: <pw>
  MYSQL_DATABASE: modelregistry
  MYSQL_USER: mruser
  MYSQL_PASSWORD: <pw>
```

（外加一個 `mysql:8.0` 的 Deployment 和 Service，就一般寫法。）

### 3-3　建 registry 實例 —— ⚠️ namespace 不能亂放

```yaml
apiVersion: modelregistry.opendatahub.io/v1beta1
kind: ModelRegistry
metadata:
  name: demo-registry
  namespace: odh-model-registries      # ← 這個不能改
spec:
  rest: {}
  mysql:
    host: mr-mysql.odh-model-registries.svc.cluster.local
    port: 3306
    database: modelregistry
    username: mruser
    passwordSecret: { name: mr-db, key: MYSQL_PASSWORD }
```

我一開始放在自己的 project 裡，被擋下來：

```
admission webhook "vmodelregistry.opendatahub.io" denied the request:
namespace must be odh-model-registries
```

那個 namespace 是 DSC 上設的，可以查：

```bash
oc get dsc default-dsc -o jsonpath='{.spec.components.modelregistry.registriesNamespace}'
# odh-model-registries
```

另外注意 **API 版本**：`v1alpha1` 送出去會回

```
Warning: Version v1alpha1 of the ModelRegistry API is deprecated ... Please use v1beta1 instead.
```

用 `v1beta1`，而且它的必填欄位少一個（`v1alpha1` 要 `grpc`+`rest`，`v1beta1` 只要 `rest`）。

建好之後：

```bash
oc get pods -n odh-model-registries
# demo-registry-7d657fc9d9-w82t8   2/2   Running    ← registry + kube-rbac-proxy
# mr-mysql-5fcc5bb656-zdnc5        1/1   Running
```

> ⚠️ 順帶一提：那個 `ModelRegistry` CR 的 `.status.conditions` 在我的環境
> **一直是空的**——pod 跑起來了，但 CR 沒有回報任何狀態。
> 所以**不要用 CR 的 status 判斷它好了沒，要去看 pod 和打 API。**

### 3-4　登記一顆模型

```bash
oc port-forward -n odh-model-registries svc/demo-registry 18443:8443 &
TOK=$(oc whoami -t)
B="https://localhost:18443/api/model_registry/v1alpha3"

# 建立一個「模型」
curl -sk -X POST "$B/registered_models" -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"name":"llm-scratch","description":"從零訓練的小 GPT","owner":"ryan"}'

# 建立一個「版本」，掛上指標
curl -sk -X POST "$B/model_versions" ...

# 掛上 artifact（模型檔在哪）
curl -sk -X POST "$B/model_versions/<id>/artifacts" -H ... \
  -d '{"artifactType":"model-artifact","name":"ckpt",
       "uri":"s3://models/llm/ckpt.pt","modelFormatName":"pytorch"}'
```

⚠️ **注意 API 路徑是 `v1alpha3`，不是 CR 的 `v1beta1`。**
這兩個版本號沒有關係。我試 `/api/model_registry/v1beta1/...` 得到 `404 page not found`。

---

## 4. ⭐ 它會怎麼咬你：登記成功，但值全是 0

這是這篇最重要的一段。

我登記模型版本時，把評估指標一起掛上去：

```json
"customProperties": {
  "test_bpc":          {"metadataType":"MetadataDoubleValue","doubleValue": 4.9946},
  "model_digest":      {"metadataType":"MetadataStringValue","stringValue":"sha256:4d694be…"},
  "data_quality_gate": {"metadataType":"MetadataBoolValue",  "boolValue": true}
}
```

**HTTP 200。條目建立成功。讀得回來。**

然後我把它讀出來看：

```json
"test_bpc":          {"metadataType":"MetadataDoubleValue","double_value": 0}
"model_digest":      {"metadataType":"MetadataStringValue","string_value": ""}
"data_quality_gate": {"metadataType":"MetadataBoolValue",  "bool_value": false}
```

**每一個值都是零值。**

原因：**API 吃的是 `snake_case`（`double_value`），我送的是 `camelCase`（`doubleValue`）。
它不認識那個欄位，就用零值填上——而且不報錯。**

同一個請求，只把欄位名改成 snake_case：

```json
"test_bpc":          {"double_value": 4.9946}
"model_digest":      {"string_value": "sha256:4d694be9342d"}
"data_quality_gate": {"bool_value": true}
```

全部正確存進去。

### 為什麼這個特別糟

想一下這個台帳條目長什麼樣：

- 模型名字：**對的**
- 版本名字：**對的**
- 建立時間、作者：**對的**
- artifact 的 S3 路徑：**對的**
- **而所有的治理數據——指標、digest、品質閘門——全是零值**

**它看起來完全正常。** 你在 UI 上會看到一筆整齊的紀錄。
只有當你去比對「這個 digest 跟線上那顆一樣嗎」的時候，
才會發現你在比對一個空字串。

而 `data_quality_gate` 從 `true` 變成 `false` 更危險：
**如果有任何流程讀這個欄位做決定，它會用一個假的 false 去做判斷。**

> **這是「假綠」的另一種形態，而且是最陰的一種：
> 寫入成功、讀得回來、格式正確、內容是空的。**

---

## 5. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| Model Registry | `ModelRegistryReady=True`、pod 2/2 | **登記一筆之後讀回來比對**：digest 不是空字串、指標不是 0 |

**驗收這個元件只有一個方法：登記一筆，讀回來，逐欄比對。**
不要看 UI 上有沒有東西——有東西不代表那東西是對的。

---

## 6. 什麼時候不需要它

**模型數量少、換版不頻繁的時候。** 一份維護中的表格能回答那四個問題就夠了。

而且注意這個元件的**真實成本**不是那兩個 pod，是：

- 你要**多維護一個資料庫**（正式環境要備份、要 HA、要納管）
- 要走 API 才能登記——**代表你的 pipeline 要多一段程式碼**
- 而如果沒有人把「登記」接進放行流程，**它只是一個沒人看的資料庫**

我自己的 lab 在這之前用的是最土的做法：一個 JSON 檔掛成 ConfigMap。
**能回答問題的爛台帳，勝過沒有台帳。**

---

## 7. 順帶：這個元件跟「治理」還差一段

裝了 Model Registry **不等於**你的模型受治理。

它是一個**紀錄**的地方，不是一個**擋事**的地方。
沒有任何東西阻止你把一顆沒登記的模型部署上線——
我實測過，服務會誠實地回 `"status": "UNREGISTERED"`，然後繼續服務。

（那件事我另外寫了一篇：
[三顆一樣的模型，一顆上線兩顆被擋](/2026/09/three-identical-models-one-shipped/)）

---

**你們的模型台帳在哪？Excel、Confluence、還是有工具？
出事時查得到「線上這顆是誰核准的」嗎？**

{% include lab-env.html %}
