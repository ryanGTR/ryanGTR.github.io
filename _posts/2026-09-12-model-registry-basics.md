---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 12：Model Registry——模型的戶口名簿"
date: 2026-09-12 09:00:00 +0800
tags: [openshift-ai, odh, model-registry, governance, tutorial, ironman2026]
excerpt: "你可以完全不用它就把模型上線——這正是問題。當有人問「線上跑的是哪一版」，你需要一個地方能回答。"
feedback_question: "被問「線上跑的是哪個模型版本」，你們現在去哪裡查？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上重跑的**（升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

**Model Registry 是模型的清單：哪些模型存在、每個有哪些版本、
每個版本在哪、誰註冊的、現在是什麼狀態。**

它不參與推論，不影響效能。**你可以完全不裝它，模型照樣上線。**

而這正是要講它的原因：**因為它不是必經之路，所以很容易被跳過**，
然後半年後有人問「線上這個模型是哪來的」，你只能去翻 S3 的檔案時間。

---

## 什麼時候你會用到

具體一點，出現以下任何一件事就該有：

- 模型超過三個
- 同一個模型有兩個以上版本在跑（例如 A/B）
- **有人會問「線上是哪版」**（稽核、法遵、事故調查）
- 換版之後要能回滾到指定版本

**什麼時候不需要**：一個模型、一個版本、一個人維護、不受監理。
那用 git tag 加一份 README 也行——**重點是有個地方能回答，不是一定要用這個工具**。

---

## 前置條件

- DSC 裡 `modelregistry` 是 `Managed`
- **一個 MySQL 資料庫**（它把資料存在這裡）

```yaml
spec:
  components:
    modelregistry:
      managementState: Managed
      registriesNamespace: odh-model-registries    # ← registry 實例放這裡
```

⚠️ **`registriesNamespace` 這個欄位很重要。** registry 實例只能建在
這個 namespace 裡，建在別的地方 operator 不會理你——
**而且不會有錯誤訊息**，CR 就只是一直不 Ready。

---

## 步驟一：建一個 registry 實例

```yaml
apiVersion: modelregistry.opendatahub.io/v1beta1
kind: ModelRegistry
metadata:
  name: demo-registry
  namespace: odh-model-registries
spec:
  mysql:
    host: mr-mysql.odh-model-registries.svc.cluster.local
    port: 3306
    database: modelregistry
    username: mruser
    passwordSecret:
      name: mr-db
      key: MYSQL_PASSWORD
  rest:
    port: 8080
    serviceRoute: disabled
  grpc:
    port: 9090
  kubeRBACProxy:
    port: 8443
    routePort: 443
    serviceRoute: enabled        # ← 對外走這個
```

**驗證這一步：**

```bash
oc get modelregistries.modelregistry.opendatahub.io -n odh-model-registries
# NAME            AVAILABLE   AGE
# demo-registry   True        12d

oc get pods -n odh-model-registries
# demo-registry-...   2/2   Running
# mr-mysql-...        1/1   Running
```

⚠️ **注意 CRD 有兩個同名的**：
`modelregistries.components.platform.opendatahub.io`（元件的狀態）和
`modelregistries.modelregistry.opendatahub.io`（你的 registry 實例）。
`oc get modelregistry` 會拿到前者。**要查實例得寫全名。**

---

## 步驟二：認識三層資料模型

這是用它之前必須先搞懂的：

```
RegisteredModel          「詐欺偵測模型」——一個概念上的模型
   └─ ModelVersion       「v1-production」「v2-snakecase」——具體版本
        └─ ModelArtifact 「s3://models/fraud/v1/」——實際的檔案位置
```

| 層 | 回答什麼問題 |
|---|---|
| RegisteredModel | 我們有哪些模型？ |
| ModelVersion | 這個模型有哪幾版？現在哪版在線上？ |
| ModelArtifact | 那一版的檔案在哪？ |

**大部分人只建到第二層就停了**，然後 registry 就變成一份沒有連結的清單。
**第三層才是它跟真實世界的接點。**

⚠️ 我自己的 lab 就是活教材。發文前對帳，我把兩個版本底下的 artifact 列出來：

```
version 2 (v1-production): ckpt -> s3://models/llm/ckpt.pt
version 3 (v2-snakecase):  （沒有 artifact）
```

**v2 那一版在 registry 裡查得到名字、查不到檔案。**
它看起來被「登記」了，實際上沒有。這種紀錄的價值是負的——
它讓你以為有人在管。

---

## 步驟三：註冊一個模型

REST API（v1alpha3）：

```bash
TOKEN=$(oc whoami -t)
BASE=https://<你的 route>/api/model_registry/v1alpha3

# 1. 建 RegisteredModel
curl -sk -X POST "$BASE/registered_models" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"llm-scratch","description":"自刻的小 GPT"}'

# 2. 建 ModelVersion
curl -sk -X POST "$BASE/model_versions" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"v1-production","registeredModelId":"1","author":"ryan"}'
```

查回來：

```bash
curl -sk -H "Authorization: Bearer $TOKEN" "$BASE/registered_models?pageSize=10" \
  | jq -r '.items[] | "\(.name) | id \(.id) | \(.state)"'
```
```
llm-scratch | id 1 | LIVE
```

```bash
curl -sk -H "Authorization: Bearer $TOKEN" "$BASE/model_versions?pageSize=10" \
  | jq -r '.items[] | "\(.name) | model \(.registeredModelId) | \(.author)"'
```
```
v1-production | model 1 | ryan
v2-snakecase  | model 1 | ryan
```

Python SDK 也有（`pip install model-registry`），日常用比 curl 順。

---

## 步驟四：⭐ 註冊完之後才是重點

**registry 有紀錄 ≠ 線上跑的是那一版。**

這兩件事之間沒有任何自動的連結。KServe 不會去問 registry，
registry 也不知道 KServe 在跑什麼。**是你要把它們對起來。**

最小可行的做法——**部署時把版本寫進 ISvc 的 label**：

```yaml
metadata:
  labels:
    model-registry/registered-model: "llm-scratch"
    model-registry/version: "v1-production"
```

然後這個問題就有答案了：

```bash
oc get isvc -A -L model-registry/registered-model,model-registry/version
```

**這不是平台給你的功能，是你要自己加的約定。**
但沒有它，registry 就只是一份沒人維護的清單。

### ⚠️ 加這個 label 會重啟你的模型服務

這件事我是在寫下一篇的時候撞到的，值得回頭補在這裡。

**KServe 會把 ISvc 上的 label 傳播到 predictor 的 pod template。**
pod template 一變就是一次 rolling update——你只是加了一行註記，
**模型服務會換一次 pod。**

我的 lab 上更難看：節點 CPU 已經吃到 96%，
rolling update 的 `maxSurge` 在單副本上會進位成 1，
**新 pod 要多一份資源才排得進去，於是它 Pending 了 22 分鐘**，
最後 `ProgressDeadlineExceeded`。

而在那 22 分鐘裡：

```
$ oc get isvc llm-scratch
NAME          READY
llm-scratch   False        ← 說壞了

$ curl .../health
{"status":"ok","model_loaded":true,...}   ← 其實一直好好的
```

**舊 pod 從頭到尾在服務，一次都沒斷。** 是 `READY` 這一欄在說謊——
Day 10 那條（pod 不存在時它說 `True`）的另一面。**兩個方向都會騙你。**

**所以：把 label 加上去要挑時間，當成一次部署處理**，
不要在星期五下午順手 `oc label` 一下。
（我最後是把 label 拿掉讓它回到舊的 replicaset，30 秒就收斂了。）

### 但這樣還是回答不了「跑的是哪一版」

把 label 加上去之後我做了一次完整對帳，三個來源攤開來看：

```
1. registry 說 v1-production 的檔案是:  s3://models/llm/ckpt.pt
2. ISvc 的 storageUri 是:               s3://models/llm/
3. pod 裡實際檔案的 sha256:             4d694be9342d73c0...ba8c835
```

兩個問題浮出來。

**第一，粒度對不起來。** registry 記的是單一檔案，KServe 要的是一個目錄前綴。
你沒辦法直接字串比對——**這是第 4 項檢查實際做下去會撞到的第一件事。**

**第二，也是真正的問題：registry 記的是「檔案在哪」，不是「檔案是什麼」。**
`ModelArtifact` 的欄位就這些：

```json
{ "name": "ckpt", "uri": "s3://models/llm/ckpt.pt",
  "modelFormatName": "pytorch", "customProperties": {} }
```

**沒有 checksum、沒有 digest。** 也就是說，S3 上那個路徑的內容被人換掉，
registry 上什麼都不會變——它還是理直氣壯地告訴你「v1-production 在那裡」。

## 因此：自己把 checksum 補進去

`customProperties` 是唯一的地方（我實跑過，可用）：

```bash
curl -sk -X PATCH "$BASE/model_artifacts/1" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"customProperties":{"sha256":{"metadataType":"MetadataStringValue",
       "string_value":"4d694be9342d73c0...ba8c835"}}}'
```

配上 Day 10 那條 `sha256sum /mnt/models/<檔名>`，
**「線上跑的是不是我以為的那一版」這個問題才第一次真的有答案。**

平台把位置記給你了，**內容要你自己記**。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | registry Ready | `oc get modelregistries.modelregistry...` |
| 2 | 兩個 pod Running | `oc get pods -n odh-model-registries` |
| 3 | API 查得到 | 上面那個 curl |
| 4 | **artifact 指到真實位置** | 拿 registry 上的 URI 去 S3 對，檔案在不在 |
| 5 | **線上的 ISvc 對得回來** | 上面那個 `oc get isvc -L` |
| 6 | **內容也對得上** | registry 的 `customProperties.sha256` vs pod 裡的 `sha256sum` |

第 4、5 項是 registry 有沒有價值的分水嶺。**只做到 3 的 registry 沒有用。**
而第 6 項平台不給你，**位置對得上不代表內容沒被換過。**

---

## 常見問題

**Q：內建的 MySQL 能上正式環境嗎？**
A：不行。它沒有備份、沒有 HA。
**registry 掛了不影響線上推論**（KServe 不依賴它），
但你的紀錄會不見——而那是稽核唯一的憑據。
正式環境要接企業資料庫。

**Q：我用 camelCase 送欄位，值默默不見了。**
A：這是我實際撞到的行為。**API 對不認識的欄位不報錯，直接忽略**，
你會拿到一個看起來成功、但少了資料的物件。

發文前對帳時我又撞到它的另一面，而且更難看。
我試著 PATCH 一個不存在的 `checksum` 欄位，**回應是整個物件的欄位全部變 null**：

```json
{"name": null, "uri": null, "checksum": null}
```

看起來像資料被我清空了。**去 GET 回來看，資料庫其實一個字都沒動。**
所以這個 API 的回應在兩個方向上都不能信：
**送對了它不一定告訴你，送錯了它也不一定告訴你。**

**每次寫入之後 GET 回來對一次**，這是唯一可靠的做法。
細節寫在[這篇]({{ '/2026/09/model-registry-silently-dropped-my-values/' | relative_url }})。

**Q：跟 MLflow 比呢？**
A：MLflow 的 registry 綁在 MLflow 的 tracking 上，功能多（含實驗追蹤）。
這個比較輕、跟 OpenShift 的權限整合。
**如果你們已經在用 MLflow，不需要為了「平台有」而換。**

**Q：可以強制「沒註冊的模型不准上線」嗎？**
A：**平台沒有這個機制。** 要做得自己寫 admission webhook 或在 pipeline 裡擋。
這是 Day 28 的主題——**平台給的是能力，不是已經在擋的機制**。

---

**被問「線上跑的是哪個模型版本」，你們現在去哪裡查？**

{% include lab-env.html %}
