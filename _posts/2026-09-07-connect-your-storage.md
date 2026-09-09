---
layout: post
title: "Day 7：把儲存接上——叢集裡的東西怎麼讀到你的資料"
series: "OpenShift AI 入門 30 天"
date: 2026-09-07 09:00:00 +0800
tags: [openshift-ai, odh, s3, connection, minio, tutorial]
excerpt: "workbench 要讀訓練資料、pipeline 要存產物、模型服務要抓權重——都靠這一份設定。這篇是怎麼建、怎麼給不同的東西用、以及怎麼確認它真的能用。"
feedback_question: "你們的訓練資料和模型放哪？S3、PVC，還是 NFS？"
---

## 這是什麼、解決什麼問題

你的資料在叢集外面——在 NAS、在物件儲存、在某台機器上。
而 workbench、pipeline、模型服務都在叢集裡面。

**Connection 就是中間那份「怎麼連過去」的設定**：位址加憑證。

在 OpenShift AI 裡它**不是一個 CRD**，是一個**貼了特定 label 的 Secret**：

```bash
oc get crd | grep -i connection
# （沒有東西）
```

知道這件事很重要，因為它決定了你怎麼建、怎麼查、怎麼除錯。

---

## 什麼時候你會用到

**第一次要讓叢集裡的東西讀到叢集外的資料時。** 通常依序發生：

1. workbench 要讀訓練資料
2. pipeline 要存 checkpoint（pod 會消失，產物不能只留在裡面）
3. 模型服務要抓權重——**因為權重不進 image**

第 3 點通常是逼你走到這一步的那個：模型換一次就重 build 一次 image，很快受不了。

---

## 前置條件

- 一個 S3 相容的儲存（AWS S3、MinIO、Ceph RGW 都可以）
- 那個儲存的 endpoint、access key、secret key、bucket 名稱
- 叢集連得到那個位址（**離線環境要先確認防火牆**）

---

## 步驟一：看有哪些連線型別

ODH 內建三種（3.5 和 3.6 都是這三種）：

```bash
oc get cm -n opendatahub -l opendatahub.io/connection-type=true
# oci-v1    ← OCI registry
# s3        ← S3 相容物件儲存
# uri-v1    ← 單一 URI
```

`s3` 這型要填哪些欄位，定義就在那個 ConfigMap 裡：

```bash
oc get cm s3 -n opendatahub -o jsonpath='{.data.fields}' \
  | jq -r '.[] | "\(.envVar)\trequired=\(.required)"'
```
```
AWS_ACCESS_KEY_ID       required=true
AWS_SECRET_ACCESS_KEY   required=true
AWS_S3_ENDPOINT         required=true
AWS_DEFAULT_REGION      required=false
AWS_S3_BUCKET           required=false
```

**三個必填。** 這比翻文件快，而且**不會過期**——定義就在你的叢集上。

---

## 步驟二：建一個

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: minio-connection
  namespace: llm-serve-demo
  labels:
    opendatahub.io/dashboard: "true"          # ← 沒有這個，dashboard 看不到
    opendatahub.io/managed: "true"
  annotations:
    opendatahub.io/connection-type-ref: s3    # ← 指向上面那個型別
    openshift.io/display-name: "MinIO（模型與 pipeline 產物）"
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: <key>
  AWS_SECRET_ACCESS_KEY: <secret>
  AWS_S3_ENDPOINT: http://minio.example:9000
  AWS_DEFAULT_REGION: us-east-1
  AWS_S3_BUCKET: models
```

也可以在 dashboard 上按「Create connection」用表單填，結果一樣。

**驗證這一步：**

```bash
oc get secret -n <ns> -l opendatahub.io/dashboard=true
# minio-connection
```

⚠️ **這只證明「建出來了」，不證明「能用」。** 真正的驗證在步驟四。

---

## 步驟三：給不同的東西用

同一份 Connection，三種消費方式不一樣：

### workbench：整包注入環境變數

```yaml
spec:
  template:
    spec:
      containers:
      - name: my-workbench
        envFrom:
        - secretRef: { name: minio-connection }   # ← 一行搞定
```

進去之後直接讀環境變數：

```python
import os, boto3
s3 = boto3.client("s3",
        endpoint_url=os.environ["AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
```

### pipeline：用 kfp 的 helper 注入

```python
from kfp import kubernetes

kubernetes.use_secret_as_env(
    task, secret_name="minio-connection",
    secret_key_to_env={"AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
                       "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY"})
```

**憑證不要進 image。** image 會進 registry、會被複製、會被掃描工具展開——
任何進了 image layer 的東西都要當成已經公開。

### 模型服務（KServe）：走 annotation，而且要掛在 ServiceAccount 上

⚠️ **KServe 用的是另一組慣例**——不是 label，是**貼在 Secret 上的 annotation**：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: minio-s3
  namespace: llm-serve-demo
  annotations:
    serving.kserve.io/s3-endpoint: minio.example:9000     # ← 不含 http://
    serving.kserve.io/s3-usehttps: "0"
    serving.kserve.io/s3-region: us-east-1
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: <key>
  AWS_SECRET_ACCESS_KEY: <secret>
```

**但光有這個 Secret 沒用。** KServe 不會去掃 namespace 裡的 Secret，
它是從 `InferenceService` 用的那個 **ServiceAccount** 上去找：

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: llm-sa
secrets:
  - name: minio-s3          # ← 這一行才是接線
```

```yaml
# InferenceService
spec:
  predictor:
    serviceAccountName: llm-sa                 # ← 指到上面那個 SA
    model:
      storageUri: s3://models/sklearn-demo/    # ← 要抓的路徑
```

**這條鏈少一環就抓不到權重**，而錯誤訊息只會說連不到 endpoint，
不會告訴你是 SA 沒掛 Secret。串起來是：

```
InferenceService → serviceAccountName → ServiceAccount.secrets → 有 annotation 的 Secret
```

> **所以同一個叢集上，S3 設定有三套並存的慣例**：
> dashboard 讀 **label**、KServe 讀 **annotation + ServiceAccount**、你的程式讀 **data key**。
>
> 我自己的 lab 最後是**開兩份 Secret**：`minio-connection`（給 dashboard 和 workbench，
> 五個欄位齊全）、`minio-s3`（給 KServe，只有 access key 兩個欄位 + annotation；
> Day 9 的 pipeline server 也是指到這一份）。
> 一份 Secret 要同時滿足三套當然做得到——貼齊 label、annotation、五個 key，再掛上 SA——
> **但拆兩份的好處是給 KServe 的那份只帶它需要的兩個欄位。**
> 代價是盤點時要記得有兩份，這正是 Day 6 講的
> 「Connections 頁不是憑證的唯一來源」。

---

## 步驟四：⭐ 確認它真的能用

**這一步不能省。** dashboard 顯示一份 connection 存在，
只代表**格式正確**——label 有、必填欄位填了。
**它不會拿那組憑證去連一次。**

我自己就踩過：憑證填錯了，dashboard 上一切正常，
直到在 workbench 裡真的去用才拿到 `SignatureDoesNotMatch`。

**最快的驗證方式**——在 workbench 裡跑三行：

```python
import os, boto3
s3 = boto3.client("s3", endpoint_url=os.environ["AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
print([o["Key"] for o in s3.list_objects_v2(Bucket=os.environ["AWS_S3_BUCKET"]).get("Contents", [])][:5])
```

**列得出東西才算設好。**

沒有 workbench 的話，用一個一次性的 pod 也行。

⚠️ **`oc run` 沒有 `--env-from`**（只有 `--env`，要一個一個列，
等於把金鑰打在指令列上留進 shell 紀錄）。整包注入要用 `--overrides`：

```bash
oc run s3check --rm -i --restart=Never -n <ns> --image=quay.io/minio/mc \
  --overrides='{"spec":{"containers":[{"name":"s3check","image":"quay.io/minio/mc",
    "command":["sh","-c","mc alias set t $AWS_S3_ENDPOINT $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY >/dev/null && mc ls t/$AWS_S3_BUCKET"],
    "envFrom":[{"secretRef":{"name":"minio-connection"}}]}]}}'
```

跑起來會直接把 bucket 內容列出來：

```
[2026-08-30 14:34:03 UTC] 2.8KiB STANDARD registry.json
[2026-09-05 09:20:19 UTC]     0B llm/
[2026-09-05 09:20:19 UTC]     0B sklearn-demo/
pod "s3check" deleted
```

**憑證從頭到尾只存在於 Secret 和 pod 裡，沒有進過你的指令列。**

---

## 怎麼確認做對了

| | 檢查 | 指令 |
|---|---|---|
| 1 | Secret 建出來了 | `oc get secret -l opendatahub.io/dashboard=true` |
| 2 | dashboard 看得到 | 打開 Connections 分頁 |
| 3 | **憑證真的能連** | 上面那三行 python |
| 4 | **誰在用它** | Connections 頁的 `Connected resources` 欄 |

第 4 項容易被忽略但很有用：**如果那一欄是 `--`，代表沒有東西在用這份 connection。**
而如果同時你的 pipeline 又在跑，那就表示**它用的是另一份你在 UI 上看不到的憑證**。

---

## 常見問題

**Q：pipeline 明明在用 S3，dashboard 上卻沒有 connection。**
A：那份 Secret 沒貼 `opendatahub.io/dashboard` label。
**執行期讀的是 Secret 的內容，dashboard 讀的是 label**——兩邊看的是同一個物件的不同部位。
盤點的時候要看實際掛進 pod 的：

```bash
oc get pods -n <ns> -o json | jq -r '
  .items[].spec.containers[].envFrom[]?.secretRef.name,
  .items[].spec.volumes[]?.secret.secretName
  | select(. != null)' | sort -u
```

⚠️ 這行也抓不到 **KServe 走 ServiceAccount 那條路**的憑證——
那份 Secret 不會出現在 pod spec 裡。SA 那邊要另外查：

```bash
oc get sa -n <ns> -o json | jq -r '.items[] | select(.secrets) |
  "\(.metadata.name) -> \(.secrets[].name)"'
```

**Q：`SignatureDoesNotMatch`。**
A：帳號密碼錯。注意 `AWS_S3_ENDPOINT` 有沒有多寫或少寫 `http://`——
**boto3 要完整 URL，KServe 的 annotation 不要 scheme**。

**Q：改了 Secret，但服務還是用舊的。**
A：環境變數是 pod 啟動時注入的，**改 Secret 不會自動生效**，要重建 pod：
`oc delete pod -l <你的 selector>`。

**Q：可以用 PVC 取代嗎？**
A：可以，資料很大或本來就在企業儲存上時更合適。
但 PVC 掛進來的東西**不受平台版本控制**——別人改了那個檔案，
你的訓練結果就變了，而且沒有紀錄。

---

**你們的訓練資料和模型放哪？S3、PVC，還是 NFS？**

{% include lab-env.html %}
