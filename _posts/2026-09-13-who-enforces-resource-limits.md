---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 13：資源怎麼分出去——Hardware Profile 寫規則，誰來執行？"
date: 2026-09-13 09:00:00 +0800
tags: [openshift-ai, odh, hardware-profile, resourcequota, gpu, tutorial, ironman2026]
excerpt: "誰能要多少 CPU、記憶體、GPU，由 Hardware Profile 決定——這句話只對了一半。這篇把上限實際撞一次，看它到底擋不擋，以及真正在擋的是誰。"
feedback_question: "你們叢集上的資源上限，有人實際撞過一次確認它會擋嗎？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上重跑的**（升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

**Hardware Profile 是平台用來定義「一份工作可以要多少資源」的物件**——
CPU、記憶體、加速器，各自的最小值、預設值、上限。
使用者在 dashboard 上選 workbench 大小時，選單裡的選項就是這些 profile。

❖❖❖

這篇原本只打算講「怎麼設」。發文前我照自己寫在文末的檢查表去驗，
**結果那個上限根本不擋**。於是它變成兩個問題：

**規則寫在哪裡，以及誰真的在執行它——而這兩個不是同一個東西。**

⚠️ **2.x 叫 Accelerator Profile，3.x 改名並擴大了範圍**（不只加速器）。
**任何文件裡出現「Accelerator Profile」，那是 2.x 的材料。**

---

## 什麼時候你會用到

- GPU 要分給多個團隊，需要規則
- 要避免有人開一個 workbench 就吃掉半個節點
- 要讓不同團隊有不同的資源上限
- **要在畫面上給使用者「看得懂的選項」**（而不是要他們填數字）

---

## ⚠️ 先講這篇的限制

**我的 CRC 叢集裡沒有 GPU**（CRC 是 VM，筆電上的卡沒有 passthrough 進去）。

所以這篇分兩半：**規則與執行那條線（步驟一到三）全部是在這個叢集上撞出來的，
你沒有 GPU 也做得完**；步驟四的 GPU 訊號我是在宿主機驗的，**當方法看，別當配置抄**。

（同樣的理由，[lab repo](https://github.com/ryanGTR/openshift-ai-30days)
裡**沒有** GPU 相關的 manifest。）

---

## 前置條件

- ODH／RHOAI 裝好
- 有 GPU 的話：**NVIDIA GPU Operator 或 AMD 對應的 operator 已裝好**，
  而且 `oc describe node` 上看得到 `nvidia.com/gpu` 這類資源

⚠️ **順序不能反。** 節點上沒有那個資源，Hardware Profile 寫了也沒用——
pod 會 Pending，而畫面上不會說原因。

先確認節點認得卡：

```bash
oc get nodes -o json | jq -r '.items[] | .metadata.name,
  (.status.allocatable | with_entries(select(.key|test("gpu"))))'
```

---

## 步驟一：看預設的長什麼樣

```bash
oc get hardwareprofile -A
# NAMESPACE     NAME              AGE
# opendatahub   default-profile   5d

oc get hardwareprofile default-profile -n opendatahub -o jsonpath='{.spec}' | jq
```
```json
{"identifiers":[
  {"identifier":"cpu",    "displayName":"CPU",    "resourceType":"CPU",
   "minCount":1,    "defaultCount":2,    "maxCount":4},
  {"identifier":"memory", "displayName":"Memory", "resourceType":"Memory",
   "minCount":"2Gi","defaultCount":"4Gi","maxCount":"8Gi"}]}
```

⚠️ **`displayName` 每個 identifier 都要有，它是必填的**——
下一節那份 YAML 我第一次就是漏了它被打回來。

**三個數字各有各的意思：**

| 欄位 | 意思 |
|---|---|
| `minCount` | 使用者最少要拿這麼多（也是 requests 的下限） |
| `defaultCount` | 沒選的話給這個 |
| `maxCount` | **上限。這是真正在做治理的那個數字** |

---

## 步驟二：加一個帶 GPU 的 profile

```yaml
apiVersion: infrastructure.opendatahub.io/v1     # ← 不是 v1alpha1，見下
kind: HardwareProfile
metadata:
  name: gpu-small
  namespace: opendatahub
spec:
  displayName: "GPU Small（1 卡）"
  identifiers:
  - identifier: cpu
    displayName: CPU                 # ← 必填，漏了會被 API server 打回來
    resourceType: CPU
    minCount: 2
    defaultCount: 4
    maxCount: 8
  - identifier: memory
    displayName: Memory
    resourceType: Memory
    minCount: "8Gi"
    defaultCount: "16Gi"
    maxCount: "32Gi"
  - identifier: nvidia.com/gpu       # ← 節點上的資源名稱要一模一樣
    displayName: GPU
    resourceType: Accelerator
    minCount: 1
    defaultCount: 1
    maxCount: 1
```

漏掉 `displayName` 的話，錯誤長這樣（**不會告訴你是哪個欄位語意上該填什麼**）：

```
* spec.identifiers[1].displayName: Required value
* spec.identifiers[2].displayName: Required value
```

⚠️ **關於 `apiVersion`**：網路上（和我原本寫的）多半是 `v1alpha1`。
它還能用，但 API server 會回一句警告：

```
Warning: infrastructure.opendatahub.io/v1alpha1 is deprecated;
please use infrastructure.opendatahub.io/v1
```

**而且存回來會變成 `v1`。** 你寫進 git 的和讀出來的又對不起來了——
跟 Day 10 那個 `RawDeployment` → `Standard` 是同一件事。

⭐ **我的叢集裡沒有 GPU，但這個物件照樣建得起來。**
`HardwareProfile` 只是一份數字的宣告，它不驗證節點上有沒有那個資源。
**這是分開的兩件事**——真正會出問題的是用它的時候（pod Pending）。

**驗證這一步：**

```bash
oc get hardwareprofile -n opendatahub
```

然後去 dashboard 建 workbench，選單裡應該多一個「GPU Small」。
**沒出現就是 namespace 錯了**——profile 要放在平台的 namespace。

---

## 步驟三：⭐ 那個 `maxCount` 到底會不會擋？

寫這篇初稿時，我在最後的檢查表裡放了一句話：

> 第 5 項很多人不做，但**沒被驗過的上限等於沒有上限**。

發文前對帳，我照自己寫的去驗了。**結果是：上限確實不存在。**

❖❖❖

`default-profile` 的上限是 **4 CPU / 8Gi**。我送一個
**16 CPU / 64Gi** 的 workbench 進去：

```bash
oc apply -f oversize-notebook.yaml
# notebook.kubeflow.org/d13-oversize created
```

**建起來了。** 而且資源原封不動存進去：

```bash
oc get notebook d13-oversize -o jsonpath='{.spec.template.spec.containers[0].resources}'
```
```json
{"limits":{"cpu":"16","memory":"64Gi"},"requests":{"cpu":"16","memory":"64Gi"}}
```

沒有被拒絕、沒有被改寫、沒有警告。

### 為什麼

叢集上確實有 webhook 在管 notebook，我一個一個看：

```bash
oc get mutatingwebhookconfigurations -o json | jq -r '.items[]
  | .metadata.name as $n | .webhooks[] | "\($n)\t\(.name)"' | grep -i hardware
```
```
workbenches-operator-...   hardwareprofile-notebook-injector.opendatahub.io
```

它對**所有** notebook 的 CREATE／UPDATE 生效（`objectSelector` 空、
`failurePolicy: Fail`）。也就是說——**我那份 16 CPU 的 YAML 確實經過它，
而它放行了。**

因為它是 **mutating**（注入預設值），不是 validating（擋）。

那 validating 的呢？有一個，但：

```
dashboard-hardwareprofile-validator.opendatahub.io
  apiGroups: [dashboard.opendatahub.io]     ← 注意這裡
  resources: [hardwareprofiles]
```

**它管的是 `dashboard.opendatahub.io` 這個 group 底下的 hardwareprofiles。**
而我們用的是 `infrastructure.opendatahub.io`。去問叢集那個 group 有什麼：

```bash
oc api-resources --api-group=dashboard.opendatahub.io
# NAME              KIND
# odhapplications   OdhApplication
# odhdocuments      OdhDocument
```

**沒有 hardwareprofiles。** 這個 validator 指著一個已經不存在的資源
（旁邊那個 `dashboard-acceleratorprofile-validator` 更明顯，
`acceleratorprofiles` 這個 CRD 在 3.x 根本已經拿掉了）。

## 因此：`maxCount` 是選單範圍，不是護欄

它決定 dashboard 上那個下拉選單能拉到多大。
**任何繞過 dashboard 的路徑——`oc apply`、GitOps、pipeline——它都不在。**

要真的擋，得用 k8s 自己的東西。**這兩個我都實際撞過一次**：

### `ResourceQuota`：管一個 namespace 的總量

```bash
oc create quota team-quota -n <ns> --hard=limits.cpu=2,limits.memory=4Gi
```

送一個要 4 CPU 的 pod 進去：

```
Error from server (Forbidden): pods "too-big" is forbidden: exceeded quota: team-quota,
  requested: limits.cpu=4,limits.memory=8Gi,
  used: limits.cpu=0,limits.memory=0,
  limited: limits.cpu=2,limits.memory=4Gi
```

**這才是「會擋」長什麼樣子**：當場、明確、告訴你要多少、用了多少、上限多少。
跟上一節那個一聲不吭就收下 16 CPU 的對比很清楚。

### ⚠️ 但透過 Deployment 送，你在原地看不到任何錯誤

同樣的 4 CPU，包在 Deployment 裡：

```bash
oc apply -f too-big-deploy.yaml
# deployment.apps/too-big-deploy created      ← 成功
oc get deploy too-big-deploy
# NAME             READY   AVAILABLE
# too-big-deploy   0/1     0                  ← 永遠不會好
oc get pods
# No resources found                          ← 一個 pod 都沒有
```

錯誤在 ReplicaSet 的 events 裡，而且**它會一直重試**：

```
Warning  FailedCreate  replicaset/too-big-deploy-84dcb4f947
  Error creating: pods "...-blmtq" is forbidden: exceeded quota: team-quota, ...
Warning  FailedCreate  (combined from similar events): ... "...-gpfmf" is forbidden ...
```

**workbench、InferenceService、pipeline 走的都是這條路**——
使用者只會看到「一直起不來」，不會看到原因。
`oc get events` 要成為反射動作。

### `LimitRange`：管單一 container，順便給預設值

```yaml
spec:
  limits:
  - type: Container
    max:            { cpu: "1",    memory: 1Gi }
    default:        { cpu: "200m", memory: 256Mi }
    defaultRequest: { cpu: "100m", memory: 128Mi }
```

超過 `max` 的當場被拒（**即使還在 quota 額度內**）：

```
Error from server (Forbidden): pods "over-limitrange" is forbidden:
  [maximum cpu usage per Container is 1, but limit is 2,
   maximum memory usage per Container is 1Gi, but limit is 2Gi]
```

而完全沒寫 `resources` 的 pod，會被塞進預設值——我送了一個空的進去，讀回來是：

```json
{"limits":{"cpu":"200m","memory":"256Mi"},
 "requests":{"cpu":"100m","memory":"128Mi"}}
```

**這很重要**：沒有 `LimitRange` 的話，沒寫 requests 的 pod 在排程器眼中是「不佔資源」，
於是節點會被塞爆。

## 三個機制，只有兩個在做事

| | 管什麼 | 會擋嗎 | 錯誤看得到嗎 |
|---|---|---|---|
| `HardwareProfile` | dashboard 選單的範圍 | ❌ | — |
| `LimitRange` | 單一 container 的上下限＋預設值 | ✅ | 直接建 pod 時當場看到 |
| `ResourceQuota` | 一個 namespace 的總量 | ✅ | 同上；**經過 controller 就只在 events** |

**平台給了你一個地方寫下規則，執行規則的人要自己從 k8s 那邊接過來。**

---

## 步驟四：GPU 的那個上限，有第二個「其實沒在擋」

前面談的是「誰不讓你要太多」。GPU 還有一個對稱的問題：
**你要到了，但沒在用。**

原因是這一行，幾乎每個 PyTorch 服務都有：

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
```

**它不會失敗，它會安靜地退回 CPU。** 驅動版本不合、CUDA runtime 缺、
容器沒掛到 device——全都走同一條路。而在 k8s 那一層，
`nvidia.com/gpu: 1` 照樣成立，**那張卡照樣被這個 pod 佔住不給別人用**。

於是你同時得到「GPU 被消耗」和「GPU 沒被使用」——最貴的一種失敗。

### 三個訊號，只有一個能分辨

| 你看到 | 證明了什麼 |
|---|---|
| pod 有 `nvidia.com/gpu: 1` | **只證明排程器分給它了** |
| pod 裡 `nvidia-smi` 看得到卡 | **只證明容器看得到裝置** |
| `nvidia-smi --query-compute-apps` **列出你的 process** | ✅ 真的在算 |

前兩個是絕大多數人拿來驗收的訊號，**而它們在退回 CPU 的情況下都顯示正常**。

```bash
oc exec <pod> -- nvidia-smi \
  --query-compute-apps=pid,process_name,used_memory --format=csv
```

⚠️ **這條我是在筆電宿主機上驗的，不是在 OpenShift pod 裡**（這台 CRC 沒有卡）。
同一份程式只改 `CUDA_VISIBLE_DEVICES=""`，兩組的差別是
**延遲 606ms vs 1520ms，而 compute-apps 一邊列得出 pid、一邊完全是空的**。
**方法可以搬，數字不要搬**——量級陷阱與完整對照在
[這篇]({{ '/2026/09/pod-got-the-gpu-but-model-ran-on-cpu/' | relative_url }})。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 節點認得卡 | `oc describe node \| grep nvidia.com/gpu` |
| 2 | profile 出現在選單 | dashboard 建 workbench 時看 |
| 3 | pod 拿到卡 | `oc get pod <p> -o jsonpath='{...resources.limits}'` |
| 4 | **process 真的在卡上** | `nvidia-smi --query-compute-apps` |
| 5 | 上限真的在擋 | **故意要超過 maxCount，看它拒絕**——見步驟三，`HardwareProfile` 這關**不會**擋 |
| 6 | **真的有東西在擋** | 建 `ResourceQuota`／`LimitRange` 之後再送一次超量請求，**要看到 `Forbidden`** |
| 7 | **經過 controller 的路徑也擋得到** | 同樣的超量請求包進 Deployment 送，去 `oc get events` 確認 |

第 5 項很多人不做。**我做了，答案是它不擋**——所以才有第 6、7 項。

**護欄要驗過它會擋，才算存在。**
這篇是最乾淨的例子：我在初稿裡寫下這句話，然後被自己的話抓到。

---

## 常見問題

**Q：一張卡可以給兩個 pod 用嗎？**
A：預設不行，`nvidia.com/gpu` 是整數資源。
要共用得用 **MIG**（A100 以上切成多個實例）或 **time-slicing**
（GPU Operator 設定），兩者都要另外設，**而且效能特性完全不同**。
評估時這是要問清楚的一題。

**Q：pod 一直 Pending。**
A：`oc describe pod` 看 Events。常見是 `Insufficient nvidia.com/gpu`
（卡被別人佔了）或節點根本沒回報那個資源（GPU Operator 沒裝好）。

**Q：CPU 上跑 LLM 可行嗎？**
A：小模型可以（我 lab 就是），**但吞吐量差一到兩個數量級**。
拿來學習和驗證流程完全夠，**拿來評估效能會得到錯的結論**。

---

**你們叢集上的資源上限，有人實際撞過一次確認它會擋嗎？**

{% include lab-env.html %}
