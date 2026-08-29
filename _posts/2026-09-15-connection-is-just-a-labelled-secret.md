---
layout: post
title: "Connection 其實是一個貼了 label 的 Secret"
date: 2026-09-15 09:00:00 +0800
tags: [openshift-ai, odh, s3, connection, minio, tutorial]
excerpt: "OpenShift AI 的「Connection」不是 CRD。這篇講它到底是什麼、怎麼建，以及為什麼你的 pipeline 明明在用 S3，dashboard 上卻顯示「沒有 connection」。"
feedback_question: "你們的模型和訓練資料放在哪？S3、PVC、還是 NFS？當初為什麼選那個？"
---

## 1. 這是什麼

**一份「怎麼連到那個儲存體」的憑證與位址。**

在 OpenShift AI 裡它叫 **Connection**（2.x 叫 Data Connection）。
你的 workbench 要讀訓練資料、pipeline 要存產物、KServe 要抓模型權重——
都靠它。

而它**不是一個 CRD**：

```bash
oc get crd | grep -i connection
# （沒有東西）
```

**它就是一個 Secret，只是貼了特定的 label。**

> **Java 類比**：像 Spring 的 `DataSource` 設定。
> 差別在這裡的「設定」是叢集資源，可以被 RBAC 管、被掛進不同的 pod，
> 而不是躺在每個人的 `application.yml` 裡。

---

## 2. 什麼時機需要它

**第一次要讓叢集裡的東西讀到叢集外的資料時。**

具體是這三個時刻，通常會依序發生：

1. workbench 要讀訓練資料——資料不在容器裡
2. pipeline 要存 checkpoint——產物不能只留在 pod 裡（pod 會消失）
3. KServe 要抓模型權重——**權重不進 image**

**如果你的資料還小到可以塞進 image 或 PVC，你可以先不用。**
但第 3 點通常會逼你走到這一步：模型換一次就重 build 一次 image，很快就受不了。

---

## 3. 怎麼用

### 先看有哪些型別

ODH 3.5 內建三種：

```bash
oc get cm -n opendatahub -l opendatahub.io/connection-type=true
# oci-v1    ← OCI registry
# s3        ← S3 相容物件儲存
# uri-v1    ← 單一 URI
```

`s3` 這型要哪些欄位，定義就在那個 ConfigMap 裡：

```bash
oc get cm s3 -n opendatahub -o jsonpath='{.data.fields}' | jq -r '.[] | "\(.envVar)\trequired=\(.required)"'
```
```
AWS_ACCESS_KEY_ID       required=true
AWS_SECRET_ACCESS_KEY   required=true
AWS_S3_ENDPOINT         required=true
AWS_DEFAULT_REGION      required=false
AWS_S3_BUCKET           required=false
```

**三個必填。** 這比看文件快——**欄位定義就在叢集上，不會過期。**

### 建一個

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: minio-connection
  namespace: llm-serve-demo
  labels:
    opendatahub.io/dashboard: "true"        # ← 沒有這個，UI 看不到
    opendatahub.io/managed: "true"
  annotations:
    opendatahub.io/connection-type-ref: s3  # ← 指向上面那個型別
    openshift.io/display-name: "MinIO（模型與 pipeline 產物）"
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: <key>
  AWS_SECRET_ACCESS_KEY: <secret>
  AWS_S3_ENDPOINT: http://<host>:9000
  AWS_DEFAULT_REGION: us-east-1
  AWS_S3_BUCKET: models
```

`oc apply` 之後，dashboard 上就看得到了：

![Connections 頁面](/assets/img/rhoai/connections.png)

（順帶一提，**顯示名稱放中文沒問題**——Secret 的 annotation 存在 etcd，
不經過任何資料庫，所以不會有字元集問題。）

---

## 4. ⭐ 它會怎麼咬你：能用的不一定看得到

看上面那張圖：**只有一個 connection。**

而我的 pipeline 正在用另一個叫 `minio-s3` 的 Secret，跑得好好的。
它為什麼不在列表上？

```bash
oc get secret minio-s3 -n llm-serve-demo -o jsonpath='{.metadata.labels}'
# （空的）
```

**沒有 label。**

於是出現這個狀況：

| | pipeline 用不用得到 | dashboard 看不看得到 |
|---|---|---|
| `minio-s3`（沒 label） | ✅ 一直在用 | ❌ 完全不出現 |
| `minio-connection`（有 label） | ✅ 可以用 | ✅ 看得到 |

**「Connection」是一個 UI 概念，不是執行期概念。**

- **執行期**（pipeline、KServe）讀的是 Secret 的**內容**——`AWS_ACCESS_KEY_ID` 那些 key
- **dashboard** 讀的是 Secret 的 **label**

兩邊看的是同一個物件的不同部位。

### 這在實務上會怎樣

**一個新接手的人打開 dashboard，看到「這個 project 沒有 connection」，
於是他建了一個新的。** 然後你有兩份憑證，其中一份沒人知道還在被用。

或者反過來：他看到列表上有一個 connection，就以為 pipeline 用的是那個——
去改了它的密碼，結果什麼都沒發生（因為 pipeline 讀的是另一個）。

**盤點的時候不要只看 dashboard，要看 Secret：**

```bash
# UI 看得到的
oc get secret -n <ns> -l opendatahub.io/dashboard=true

# 實際被掛進 pod 的（這個才是真相）
oc get pods -n <ns> -o json | jq -r '
  .items[].spec.containers[].envFrom[]?.secretRef.name,
  .items[].spec.volumes[]?.secret.secretName' | sort -u
```

---

## 4.5　⭐ 更糟的：UI 上正常，憑證是錯的

上面講的是「能用但看不到」。**還有反過來的：看得到，但不能用。**

我建的那個 `minio-connection`，label 齊全、欄位完整、
dashboard 上顯示得漂漂亮亮——**而裡面的帳號密碼是錯的。**

（我當初隨手填了 `minioadmin`，而那台 MinIO 的實際憑證不是那組。）

直到我在 workbench 裡真的去用它：

```python
s3 = boto3.client("s3", endpoint_url=os.environ["AWS_S3_ENDPOINT"],
                  aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
s3.list_objects_v2(Bucket="models")
```
```
ClientError: An error occurred (SignatureDoesNotMatch) when calling
the ListObjectsV2 operation
```

**dashboard 從頭到尾沒有任何提示。**

因為它檢查的是**格式**——label 對不對、`connection-type-ref` 有沒有、
必填欄位填了沒。**它不會拿那組憑證去連一次看看。**

> 這跟前面那個問題是同一個形狀的兩面：
> **UI 看的是 label 和欄位，執行期看的是內容。**
> 一份 Connection 可以「格式完全正確」而「內容完全錯誤」，
> 而只有第二種會讓你的 pipeline 半夜失敗。

### 所以驗收要加一條

**不要接受「connection 已經設好了」這句話。**

要求對方**當著你的面用它連一次**——最簡單的方式是在 workbench 裡跑三行：

```python
import os, boto3
s3 = boto3.client("s3", endpoint_url=os.environ["AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
print([o["Key"] for o in s3.list_objects_v2(Bucket="<bucket>").get("Contents", [])][:5])
```

**列得出東西才算設好。**

---

## 5. 還有第三套寫法（KServe 的）

更混亂一點：**KServe 抓模型時用的又是另一組慣例**——annotation：

```bash
oc get secret minio-s3 -n llm-serve-demo -o jsonpath='{.metadata.annotations}'
```
```json
{"serving.kserve.io/s3-endpoint":"100.117.49.79:9000",
 "serving.kserve.io/s3-region":"us-east-1",
 "serving.kserve.io/s3-usehttps":"0",
 "serving.kserve.io/s3-useanoncredential":"false"}
```

所以同一個叢集上，S3 設定有**三套並存的慣例**：

| 誰在讀 | 讀什麼 |
|---|---|
| dashboard | label `opendatahub.io/dashboard` + annotation `connection-type-ref` |
| KServe | annotation `serving.kserve.io/s3-*` |
| 你的 pipeline 程式碼 | Secret 的 data key（`AWS_ACCESS_KEY_ID` 等） |

**一份 Secret 要同時滿足三套，才會「到處都正常」。**
只滿足其中一套的話，它在某些地方能用、某些地方消失——而且不會有錯誤訊息。

---

## 6. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| Connection | dashboard 上看得到 | **實際被掛進 pod 的 Secret 名字，跟 dashboard 上顯示的是同一個** |

驗收方式就是上面那兩個指令的輸出要對得起來。對不起來，代表有影子憑證。

---

## 7. 什麼時候不需要它

- 資料小到可以進 image 或 PVC
- 只有一個人用，credential 放在自己的 workbench 裡

但注意第二種的代價：**那個人離職，或他的 token 過期，東西就停了**，
而且沒有人知道那份 credential 存在。

**Connection 真正的價值不是「連得到 S3」——你不用它也連得到。
是「這份憑證是叢集資源，可以被 RBAC 管、被輪替、被稽核」。**

---

**你們的模型和訓練資料放在哪？S3、PVC，還是 NFS？當初為什麼選那個？**

{% include lab-env.html %}
