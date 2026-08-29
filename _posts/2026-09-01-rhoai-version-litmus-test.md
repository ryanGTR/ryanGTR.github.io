---
layout: post
title: "一張版本試紙：怎麼三分鐘看出對方手上的文件是 2.x 的"
date: 2026-09-01 09:00:00 +0800
tags: [openshift-ai, rhoai, odh, kserve, acceptance, version-migration]
excerpt: "五個 oc 指令，每個都會直接告訴你這套 OpenShift AI 是 2.x 還是 3.x。附一個我今天才發現的坑：你寫 RawDeployment，叢集會靜默改寫成 Standard。"
feedback_question: "你們驗收平台時，有沒有一組「不用信對方口頭」的指令清單？都放哪些？"
---

上一篇講了 [3.x 和 2.x 的差異](/2026/08/rhoai-3x-why-your-tutorial-breaks/)。
這篇是它的實用版：**五個指令，每個都會直接吐出一個只有某一版才有的答案。**

用途有兩個：確認你自己裝的是哪一版；以及**確認對方給你的文件是哪一版寫的**。

> 全部在 CRC 4.22.7 + ODH 3.5.0 上跑過（2026-08-29）。
> ODH 是 RHOAI 的上游開源版，元件同源，但**商用版的 namespace 與部分名稱不同**，
> 我在最後標了哪些不能直接照抄。

---

## 一、GPU 設定檔叫什麼

```bash
oc get crd | grep -iE "acceleratorprofile|hardwareprofile"
```

| 你看到 | 代表 |
|---|---|
| `hardwareprofiles.infrastructure.opendatahub.io` | **3.x** |
| `acceleratorprofiles.*` | 2.x |

我的叢集只有前者，**`acceleratorprofiles` 一個都沒有**。

**這是最好用的一條**：任何文件、簡報、SOP 裡出現「Accelerator Profile」，
那份材料就是 2.x 的。不用再往下讀。

---

## 二、Pipelines 這個元件叫什麼

```bash
oc get dsc default-dsc -o jsonpath='{.spec.components}' | python3 -m json.tool | grep -oE '"[a-z]+":' | tr -d '":'
```

我的叢集列出來的是：

```
aipipelines  dashboard  feastoperator  kserve  kueue
llamastackoperator  modelregistry  ray  trainingoperator  trustyai  workbenches
```

| 你看到 | 代表 |
|---|---|
| **`aipipelines`** | **3.x** |
| `datasciencepipelines` | 2.x |

同樣是改名，但改的是 DSC 裡的 key——**照 2.x 寫的 DSC YAML 在 3.x 上會被拒絕**。

---

## 三、Knative 和 Service Mesh 還在不在

```bash
oc get crd | grep -icE "knative|servicemesh|maistra"
```

| 你看到 | 代表 |
|---|---|
| **`0`** | **3.x**（模型上線改走原生 Deployment） |
| 幾十個 | 2.x |

我的叢集是 **0**。

這條的實際意義在**離線鏡像清單**：照 2.x 的清單去鏡像，
會多鏡好幾 GB 用不到的 Service Mesh，同時**缺 cert-manager**——
而缺了它 KServe 起不來。

---

## 四、DataScienceCluster 的 API 版本

```bash
oc get dsc default-dsc -o jsonpath='{.apiVersion}'
```

| 你看到 | 代表 |
|---|---|
| **`datasciencecluster.opendatahub.io/v2`** | **3.x** |
| `.../v1` | 2.x |

補一個更細的：`v1` 在 3.x 上其實還 served，但 **storage version 是 v2**：

```bash
oc get crd datascienceclusters.datasciencecluster.opendatahub.io \
   -o jsonpath='{range .spec.versions[*]}{.name}{" storage="}{.storage}{"\n"}{end}'
# v1  storage=false
# v2  storage=true
```

意思是：你可以用 v1 送出去，但**存下來的是 v2**。所以你 apply 的東西
跟 `oc get` 回來的東西可能長得不一樣——這正好接到下一條。

---

## 五、⚠️ 你寫 `RawDeployment`，叢集會存成 `Standard`

這一條是我今天才踩到的，而且我沒在任何文件上看過。

我的 `InferenceService` YAML 是這樣寫的（照 3.x 的做法）：

```yaml
metadata:
  annotations:
    serving.kserve.io/deploymentMode: RawDeployment
```

`oc apply` 成功，服務也正常。但去看叢集上實際存的：

```bash
oc get isvc llm-scratch -o jsonpath='{.metadata.annotations.serving\.kserve\.io/deploymentMode}'
# Standard      ← 不是 RawDeployment
```

**controller 把值改寫了。** 而且不是我記錯——`last-applied-configuration`
裡完整保留著我送出去的 `"RawDeployment"`：

```bash
oc get isvc llm-scratch -o jsonpath='{.metadata.annotations.kubectl\.kubernetes\.io/last-applied-configuration}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["metadata"]["annotations"])'
# {'serving.kserve.io/deploymentMode': 'RawDeployment'}
```

**這會咬到你的地方**：如果你寫了任何腳本或政策去檢查
「annotation 是不是 `RawDeployment`」——**它永遠不會相等**，
你會得到一個永遠亮著的假警報，或者更糟，一個永遠通不過的 gate。

我沒查到這個改名的官方說明，所以標成**〔實測，未找到文件佐證〕**。
如果你知道出處，留言告訴我。

---

## 整張表

| # | 指令 | 3.x 的答案 |
|---|---|---|
| 1 | `oc get crd \| grep -i profile` | `hardwareprofiles`（沒有 `acceleratorprofiles`） |
| 2 | DSC 的 components key | `aipipelines`（不是 `datasciencepipelines`） |
| 3 | `oc get crd \| grep -ic knative` | `0` |
| 4 | `oc get dsc -o jsonpath='{.apiVersion}'` | `.../v2` |
| 5 | ISvc 的 `deploymentMode` | 存下來是 `Standard`，不是你寫的 `RawDeployment` |

**前四條可以拿去對別人的文件，第五條只能自己驗**——
因為它是「你寫的」和「存下來的」不一致，看文件看不出來。

---

## 不能直接照抄的部分

我的環境是 **ODH**（上游開源版），不是 RHOAI 商用版。已知會不同的：

- **namespace**：我是 `opendatahub`，RHOAI 是 `redhat-ods-applications` 那一套
- **管理者群組**：RHOAI 用 `rhods-admins`，我這邊沒有
- CRD 的 group 名（`*.opendatahub.io`）在商用版可能帶 `redhat` 字樣

**指令的邏輯可以照用，字串要自己對一次。**

---

**你們驗收平台的時候，有沒有一組「不用信對方口頭」的指令清單？都放哪些？**

{% include lab-env.html %}
