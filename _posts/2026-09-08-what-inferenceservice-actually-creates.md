---
layout: post
title: "InferenceService 到底幫你生了什麼"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-08 09:00:00 +0800
tags: [kserve, openshift-ai, rhoai, inferenceservice, tutorial]
excerpt: "41 行 YAML，換來一個 Deployment、一個 Service、一個自動塞進去的 init container，和三個容器。這篇把它拆開看，並說明哪一樣它不會幫你生。"
feedback_question: "你們模型上線是用 KServe、自己寫 Deployment，還是廠商的方案？為什麼選那個？"
---

在 OpenShift AI 上讓一個模型變成可以打的 HTTP 端點，你要寫的東西是這個：

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: llm-scratch
  namespace: llm-serve-demo
  annotations:
    serving.kserve.io/deploymentMode: RawDeployment
spec:
  predictor:
    serviceAccountName: llm-sa
    containers:
    - name: kserve-container
      image: <你的 registry>/tools/llm-serve:cpu
      ports:
      - containerPort: 8000
      env:
      - name: STORAGE_URI              # ← 這一行是關鍵
        value: s3://models/llm/
      - name: ARTIFACTS
        value: /mnt/models
      readinessProbe:
        httpGet: { path: /health, port: 8000 }
        initialDelaySeconds: 5
        periodSeconds: 10
      resources:
        requests: { cpu: "500m", memory: 1Gi }
        limits:   { cpu: "2",    memory: 3Gi }
```

**41 行。** 這篇是關於這 41 行換來了什麼。

---

## 生出來的東西

`oc apply` 之後，去找「誰的 owner 是 InferenceService」：

```bash
oc get deployment,service -n llm-serve-demo -o json | \
  jq -r '.items[] | select(.metadata.ownerReferences[]?.kind=="InferenceService")
         | "\(.kind)/\(.metadata.name)"'
```
```
Deployment/llm-scratch-predictor
Service/llm-scratch-predictor
```

**就兩個。** 一個 Deployment、一個 ClusterIP Service。

在 3.x 的 RawDeployment 模式下，KServe 沒有幫你做什麼魔法——
它就是把你的容器包成一個標準的 k8s 部署。這是好事：
**排錯回到你熟悉的 `oc get deploy/pod/svc`，不用先學 Knative。**

---

## 但 pod 裡不只你的容器

```bash
oc get pod -l serving.kserve.io/inferenceservice=llm-scratch \
   -o jsonpath='{range .items[0].spec.initContainers[*]}init: {.name}{"\n"}{end}{range .items[0].spec.containers[*]}container: {.name}{"\n"}{end}'
```
```
init: storage-initializer
container: kserve-container      ← 你的
container: kube-rbac-proxy       ← ODH 加的
```

**你寫了一個容器，跑起來是三個。**

### `storage-initializer`：這是 `STORAGE_URI` 的作用

你在 YAML 裡寫了 `STORAGE_URI: s3://models/llm/`，
KServe 就會在你的容器**啟動前**塞一個 init container 進去，
把 S3 上的東西下載到 `/mnt/models`。

所以你的應用程式只要讀本地路徑就好（我用 `ARTIFACTS=/mnt/models` 告訴它去哪讀），
**完全不用知道 S3 的存在**，也不用在程式裡放任何 credential。

這個設計的重點是：**模型權重不進 image。**

- image 是不可變的、要簽章的、要掃描的、要走版本流程的
- 模型是會換的、可能一天換三次的

把它們綁在一起，等於每次換模型都要重 build 一次 image。分開之後，
**同一個 image 可以服務不同的模型，靠環境變數指到不同的 S3 路徑。**

> **Java 類比**：像你的 jar 不會把 `application.yml` 打包進去，
> 而是在啟動時從 config server 拉。同樣的道理，只是這裡拉的是幾百 MB 的權重。

### `kube-rbac-proxy`：ODH 幫你加的認證層

這個不是 KServe 給的，是 OpenShift AI 加的 sidecar，
用叢集的 RBAC 保護 metrics 端點。你沒要求，它自己來。

---

## 它**不會**幫你生的：對外入口

這是 3.x 最容易踩的一點。

```bash
oc get isvc llm-scratch -o jsonpath='{.status.address.url}'
# http://llm-scratch-predictor.llm-serve-demo.svc.cluster.local
```

**`.svc.cluster.local`——那是叢集內部位址。** 從叢集外面打不到。

在 2.x 的 Serverless 模式下，Knative 會幫你處理對外路由。
**3.x 的 RawDeployment 不會。** 你得自己開 Route：

```bash
oc create route edge llm-play \
  --service=llm-scratch-predictor --port=8000 -n llm-serve-demo
```

我的叢集上這個 Route 是這樣來的——它**沒有 ownerReference**，
因為它不是 KServe 生的，是我自己建的：

```bash
oc get route llm-play -o jsonpath='to={.spec.to.name}  host={.spec.host}'
# to=llm-scratch-predictor  host=llm-play-llm-serve-demo.apps-crc.testing
```

> ⚠️ **「模型上線了」和「模型打得到」在 3.x 是兩件事。**
> 驗收時要分開確認——`oc get isvc` 顯示 `READY=True` 只代表前者。

（另外 `.status.components.predictor.url` 會給你一個
`...example.com` 的假網址，那是 KServe 的預設 domain 沒設定，
**不要拿它去打**。）

---

## 最小可用流程

從零到能打，四步：

```bash
# 1. 模型放上 S3（我用 MinIO）
mc cp ckpt.pt tokenizer.json myminio/models/llm/

# 2. image 推上 registry（模型不在裡面）
podman push <registry>/tools/llm-serve:cpu

# 3. 建 InferenceService（就是上面那 41 行）
oc apply -f isvc-llm.yaml

# 4. 開對外入口 ← 這步不能省
oc create route edge llm-play --service=llm-scratch-predictor --port=8000
```

驗證：

```bash
oc get isvc llm-scratch          # READY 要是 True
curl -sk https://llm-play-....apps-crc.testing/health
```

---

## 常見卡點速查

| 症狀 | 先看哪裡 |
|---|---|
| `Init:Error` | S3 通不通、`STORAGE_URI` 對不對、bucket 有沒有東西 |
| `READY=False` 但 pod Running | readinessProbe 的 path / port 對不對 |
| 打不到 | 有沒有開 Route；port 是不是你以為的那個 |
| `oc exec ... curl` 說沒有 curl | serving image 通常是 minimal，用 `oc port-forward` |

---

**你們模型上線是用 KServe、自己寫 Deployment，還是廠商的方案？為什麼選那個？**

{% include lab-env.html %}
