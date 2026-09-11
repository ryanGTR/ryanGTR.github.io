---
layout: post
title: "Day 10：InferenceService——把模型變成一個 API"
series: "OpenShift AI 入門 30 天"
date: 2026-09-10 09:00:00 +0800
tags: [openshift-ai, odh, kserve, inferenceservice, servingruntime, tutorial]
excerpt: "訓練完了，接下來要讓別人打得到。這篇講 KServe 的兩條部署路線、什麼時候該選哪一條，以及每一步怎麼確認它真的起來了。"
feedback_question: "你們模型上線是走 KServe，還是自己包成一般的 Deployment？為什麼？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上重跑的**（升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

你手上有一個訓練好的模型檔。要讓它變成一個別人打得到的 HTTP endpoint，
在純 k8s 上你得自己寫：Deployment、Service、把權重放進去的辦法、健康檢查、
之後還有版本切換跟流量分配。

**`InferenceService`（簡稱 ISvc）把這些收成一份 YAML。** 你只描述
「模型在哪、用什麼跑、要多少資源」，KServe 生出下面那一整包。

```bash
oc get isvc -n <你的 ns>
```
```
NAME          URL                                                             READY
llm-scratch   http://llm-scratch-predictor.llm-serve-demo.svc.cluster.local   True
```

---

## 什麼時候你會用到

**當「模型會換」的時候。**

如果一個模型上線後三年不動，包成普通 Deployment 也活得下去。
但只要出現這幾件事的任何一件，你就會想要 KServe：

- 模型每個月要換版本，而你不想每次都重 build image
- 要能回answer「現在線上跑的是哪一版權重」
- 要金絲雀（先切 10% 流量給新版）
- 要能縮到 0（模型很多、大部分很閒）

反過來，**只有一個模型、幾乎不動、團隊也沒人熟 KServe** 的話，
一份 Deployment 反而好維護。這不是丟臉的選擇。

---

## 前置條件

- ODH／RHOAI 裝好，`DataScienceCluster` 裡 `kserve` 是 `Managed`
- 模型檔放在 S3（或其他 KServe 支援的儲存），且 Connection 設好
- 知道你的模型是什麼格式（下一節會用到）

---

## 步驟一：先決定走哪一條路

這是整篇最重要的一個決定，而且文件上不太講清楚。

### 路線 A：用內建 runtime（推薦，如果格式對得上）

**`ServingRuntime` ＝「會跑某類模型格式的伺服器」的範本。**
你不用管怎麼載入、怎麼開 API，交給它。

ODH 內建的以 OpenShift `Template` 形式放著。**數它們要按 `kind` 過濾，不能用名字：**

```bash
oc get templates -n opendatahub -o json \
  | jq '[.items[] | select(.objects[0].kind=="ServingRuntime")] | length'
# 14
```

⚠️ 別用 `grep runtime-template` 去數——**`kserve-ovms` 和
`guardrails-detector-huggingface-serving-template` 的名字裡沒有 `runtime-template`，
會被漏掉（實測印出 12）**；反過來直接數 `oc get templates`（15 個）又會多算一個
`jobs-async-upload-s3-to-oci-template`，那是 Job 不是 runtime。

14 個裡面 **9 個是 vLLM 的硬體變體**（cpu／cpu-x86／cuda／gaudi／multinode／rocm／
spyre-ppc64le／spyre-s390x／spyre-x86），其餘是 MLServer（含 cuda 版）、
OpenVINO（`kserve-ovms`）、AutoGluon、guardrails detector。

**怎麼知道你的模型能不能用？** 看 runtime 宣告支援哪些格式：

```bash
oc get template kserve-ovms -n opendatahub -o json \
  | jq -r '.objects[0].spec.supportedModelFormats[] | "\(.name) \(.version)"'
```
```
openvino_ir opset13
onnx 1
tensorflow 1
tensorflow 2
paddle 2
pytorch 2
```

**格式對得上，你的 ISvc 就只要這樣：**

```yaml
spec:
  predictor:
    model:
      modelFormat: { name: onnx }
      storageUri: s3://models/my-model/
```

沒指定 runtime 也行——KServe 會照 `modelFormat` 去找
`autoSelect: true` 的 runtime 自動配。

### 路線 B：自帶容器

格式對不上（自己刻的模型、自訂前後處理、非標準推論邏輯）就走這條。
**你自己給 image，KServe 只負責把權重放進去、把外圍生出來。**

我的 lab 是這條，因為模型是自己刻的 GPT：

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: llm-scratch
  labels:
    opendatahub.io/dashboard: "true"      # ← dashboard 才看得到
spec:
  predictor:
    containers:
    - name: kserve-container              # ← 名字固定，不能改
      image: <你的 registry>/llm-serve:cpu
      env:
      - name: STORAGE_URI                 # ← 關鍵，見步驟二
        value: s3://models/llm/
      ports:
      - containerPort: 8000
      readinessProbe:
        httpGet: { path: /health, port: 8000 }
      resources:
        limits: { cpu: "2", memory: 3Gi }
```

⚠️ **容器名一定要叫 `kserve-container`。** 叫別的，KServe 認不出來，
它會把你的容器當 sidecar，然後在旁邊自己生一個空的。

---

## 步驟二：權重怎麼進去

**`STORAGE_URI` 這個環境變數是開關。**

你設了它，KServe 就會在 pod 裡插一個 init container 叫 `storage-initializer`，
它先跑，把 `s3://models/llm/` 底下的東西抓下來放到 `/mnt/models`，
然後才輪到你的容器啟動。

```bash
oc get pod <predictor-pod> -o jsonpath='{range .spec.initContainers[*]}{.name}{"\n"}{end}'
# storage-initializer
```

**所以你的程式要去 `/mnt/models` 讀模型，不是去 image 裡讀。**

> 這是整套機制的重點：**權重不進 image。**
> 換模型＝換 S3 上的檔案＋重啟 pod，不用重 build、不用重推 registry、
> 不用等 image scan。

沒有這個 init container，就代表 `STORAGE_URI` 沒生效——
**去檢查拼字**，KServe 不會警告你打錯了。

---

## 步驟三：部署，然後看它到底發生什麼事

```bash
oc apply -f isvc.yaml
oc get isvc -n <ns> -w
```

`READY` 變 `True` 之前，看 pod：

```bash
oc get pods -n <ns> -l serving.kserve.io/inferenceservice=llm-scratch
```

**READY 欄會是 `2/2`**，不是 1/1。兩個容器是：

| 容器 | 誰放的 | 做什麼 |
|---|---|---|
| `kserve-container` | 你 | 跑模型 |
| `kube-rbac-proxy` | KServe | 擋掉沒授權的請求 |

第一次部署卡住的話，**先看 init container 的 log**，
八成問題在那裡（憑證錯、bucket 名錯、endpoint 連不到）：

```bash
oc logs <pod> -c storage-initializer
```

---

## 步驟四：對外開一個入口

⚠️ **KServe 不會幫你建 Route。**

`oc get isvc` 那個 URL 是 `*.svc.cluster.local`——**只有叢集內部打得到**。
筆電上 `curl` 會直接不通，而這常被誤判成「服務沒起來」。

叢集內測（起一個一次性 pod）：

```bash
oc run t --rm -i --restart=Never -n <ns> --image=curlimages/curl -- \
  curl -s http://llm-scratch-predictor.<ns>.svc.cluster.local/health
```

⚠️ **第一次跑很可能回 `error: timed out waiting for the condition`。**
不是服務有問題，是 `curlimages/curl` 這顆 image 還沒在節點上，
拉它花掉的時間（我這裡 21 秒）比 `oc run` 等 pod 的耐心還長。
**再跑一次就會通**（第二次 17 秒，image 已快取）。
先 `oc run` 一顆 `-- sleep 300` 把 image 拉下來，也是同一招。

要從外面打，自己建 Route：

```bash
oc create route edge llm-play \
  --service=llm-scratch-predictor --port=80 -n <ns>
```

**⚠️ 這一步等於把模型公開了。** 上線環境別直接照抄——
至少要接 OAuth proxy 或放進 gateway。lab 可以，正式環境不行。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 | 值不值得信 |
|---|---|---|---|
| 1 | ISvc Ready | `oc get isvc` → `READY True` | ⚠️ **會過期，見下** |
| 2 | pod 2/2 Running | `oc get pods -l serving.kserve.io/inferenceservice=<name>` | ✅ |
| 3 | **權重真的抓下來了** | `oc logs <pod> -c storage-initializer` 有下載紀錄 | ✅ |
| 4 | **模型讀到的是哪一版權重** | 進 pod 對 `sha256sum /mnt/models/<檔名>` | ✅（別用時間戳） |
| 5 | 打得到 | 叢集內 curl `/health` | ✅ |

### 第 4 項：`ls -la` 的時間戳是假線索

直覺會想用 `ls -la /mnt/models` 對時間，但那個時間戳是
**`storage-initializer` 把檔案抄下來的時間，也就是 pod 啟動時間**——
每次重啟都會變新，跟模型是哪一版一點關係都沒有。我這次重啟後三個檔案
全部標同一分鐘，而檔案本身兩週沒動過。

**要對就對 checksum：**

```bash
oc exec -n <ns> <pod> -c kserve-container -- sha256sum /mnt/models/ckpt.pt
# 4d694be9342d73c08523a93565735334832df90bf4520c814315f96ffba8c835
```

這是這一整篇裡唯一能回答**「線上現在跑的是不是我以為的那一版」**的東西。
而它得你自己去對——**平台不會替你記**，這正是後面要有 model registry 的原因。

### 第 1 項：`READY True` 是快照，不是體檢

`oc get isvc` 的 READY 欄不是即時去問 pod 的，是 **KServe controller
上一次巡到的時候寫進 status 的值**。controller 自己不在的時候，那個值不會動。

叢集冷啟動就是這個場景。我把 CRC 開機後第一件事是查狀態：

```
$ oc get isvc -n llm-serve-demo
NAME           READY
iris-sklearn   True
llm-platform   True
llm-scratch    True

$ oc get pods -n llm-serve-demo | grep predictor
iris-sklearn-predictor-...   0/2   Init:0/1
llm-platform-predictor-...   0/2   Init:0/1
llm-scratch-predictor-...    0/2   Init:0/1
```

**三個都還在下載權重，三個都寫著 True**，這個狀態持續了一分鐘。

我把它做成可重現的：同時砍掉 predictor pod 和 kserve controller，兩秒取樣一次。
**pod 已經整個不存在的那 31 秒裡，`oc get isvc` 一路回 `True`**，
直到 controller 起來才翻成 `False`。

```
t+5 s   isvc=True   ctrl=ContainerCreating   pod=(不存在)
t+26s   isvc=True   ctrl=Running(0/1)        pod=(不存在)
t+31s   isvc=False  ctrl=Running(0/1)        pod=(不存在)
```

**因此：把 `oc get isvc` 當作「服務活著」的證據是不成立的**，
它只證明「controller 最後一次看到的時候是活的」。
要問現在，就得問一個不經過 controller 的東西——第 2 項（pod）或第 5 項（真的打一次）。

---

## 常見問題

**Q：annotation 我寫 `RawDeployment`，存進去變成 `Standard`？**
A：正常的。KServe 新版把 deployment mode 改了名字：
`RawDeployment` → `Standard`，`Serverless` → `Knative`，**舊名字仍然接受、會自動轉**。
叢集的預設值在這裡：

```bash
oc get cm inferenceservice-config -n opendatahub -o jsonpath='{.data.deploy}'
# {"defaultDeploymentMode": "RawDeployment"}
```

**⚠️ 所以你寫進 git 的 YAML 和叢集上讀回來的不會逐字相同**，
做 GitOps drift 檢查時別把這個當成漂移。

**Q：ODH 3.x 還需要裝 Knative／Service Mesh 嗎？**
A：預設不用了。3.x 走 Standard（Raw）模式，直接用原生 Deployment＋Service。
要「縮到 0」和流量切分，得自己先裝 **Serverless ＋ Service Mesh 兩個 operator**——
**代價是多兩套元件要維運**（這條我沒驗過，依 ODH 文件）。
2.x 時代的教學都預設 Serverless，看到那類文章要先確認版本。

**Q：改了 S3 上的模型檔，服務沒變。**
A：`storage-initializer` 只在 pod 啟動時跑一次。
`oc delete pod <predictor-pod>` 讓它重來。
**這也表示：你沒有辦法從服務本身看出權重是什麼時候抓的**——
要靠外面記，這正是要有 model registry 的原因。

**Q：`oc get servingruntimes` 是空的，正常嗎？**
A：剛裝好時是空的，正常。內建的是 `Template`（範本），**你從 dashboard 選用時才會
在那個專案裡實例化成 `ServingRuntime`**——所以部署過之後就查得到了
（我 lab 裡是 `llm-serve-demo/mlserver-runtime`，跟著 iris 那個模型一起生出來的）。
要提前建也行，把 template 裡的物件抽出來 apply 就是了。

---

**你們模型上線是走 KServe，還是自己包成一般的 Deployment？為什麼？**

{% include lab-env.html %}
