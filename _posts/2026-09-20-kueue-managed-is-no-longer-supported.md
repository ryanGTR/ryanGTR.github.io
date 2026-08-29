---
layout: post
title: "Kueue：CRD 說可以，webhook 說不行"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-20 09:00:00 +0800
tags: [openshift-ai, odh, kueue, crd, webhook, troubleshooting]
excerpt: "把 kueue 設成 Managed 被拒絕，但 CRD 的 enum 裡明明還列著 Managed。這篇是兩層驗證不一致的實例，以及它對「照文件做」的意義。"
feedback_question: "你們有在用 Kueue 或其他排隊機制嗎？還是靠 namespace quota 硬擋？"
---

## 1. 這是什麼

**Kueue 是 Kubernetes 的工作排隊器。**

一般的 k8s 排程是「有資源就跑，沒資源就 Pending」。
Kueue 多了一層：**先排隊，輪到你而且資源夠了才送進去跑**。

> **Java 類比**：`ThreadPoolExecutor` 的那個 queue。
> 沒有它，每個任務都直接搶執行緒；有了它，任務先排隊，
> 而你可以決定誰先誰後、每個團隊最多能佔多少。

在 AI 平台上這件事特別重要，因為訓練工作**又大又長**——
一個沒排隊機制的叢集，先送出的大任務會把 GPU 全部佔住，
後面的人只能等，而且不知道要等多久。

---

## 2. 什麼時機需要它

**當「誰能用 GPU」開始需要規則的時候。**

具體的訊號：

- 有人抱怨「我的訓練排不進去」，而你查不出是誰佔住的
- 兩個團隊共用一批 GPU，開始為了先後順序吵架
- 有人一次送十個任務把卡佔滿，其他人整天做不了事

**如果你只有一個團隊、GPU 也只有一兩張——`ResourceQuota` 就夠了，
不需要 Kueue。** 排隊機制的價值在「多方競用」，不在「資源不夠」。

---

## 3. ⭐ 怎麼用：以及它為什麼一開始就擋你

照直覺，開一個元件就是把它設成 `Managed`：

```bash
oc patch dsc default-dsc --type=merge \
  -p '{"spec":{"components":{"kueue":{"managementState":"Managed"}}}}'
```

```
Error from server (Forbidden): admission webhook
"datasciencecluster-v2-validator.opendatahub.io" denied the request:
Managed is no longer supported as a managementState
```

**「不再支援」——但它沒告訴你該用什麼。**

而更混亂的是，去看 CRD 的 schema：

```bash
oc get crd datascienceclusters.datasciencecluster.opendatahub.io -o json | \
  jq -r '.spec.versions[] | select(.name=="v2") |
         .schema.openAPIV3Schema.properties.spec.properties.components.properties
         | to_entries[] | "\(.key)\t\(.value.properties.managementState.enum)"'
```

```
kueue          ["Managed","Unmanaged","Removed"]     ← Managed 還在 enum 裡
kserve         ["Managed","Removed"]
aipipelines    ["Managed","Removed"]
...
```

**CRD 的 enum 裡明明列著 `Managed`。**

所以你會遇到這個狀況：**照 schema 寫是合法的，但 webhook 會擋。**

### 正解是 `Unmanaged`

```bash
oc patch dsc default-dsc --type=merge \
  -p '{"spec":{"components":{"kueue":{"managementState":"Unmanaged"}}}}'
# patched
```

`Unmanaged` 的意思是：**「我要用 Kueue，但 operator 不負責裝它，我自己裝。」**

在 RHOAI 上，你要另外裝 **RHBOK**（Red Hat Build of Kueue）這個獨立 operator。

設完之後：

```
KueueReady   False   PreConditionFailed: pre-conditions not met
```

**這是正確的狀態**——它在說「你選了 Unmanaged，但我還沒看到你裝的那個 Kueue」。

---

## 4. ⭐ 這件事真正的教訓：兩層驗證會不一致

Kubernetes 的資源驗證有兩層：

| 層 | 誰做 | 依據 |
|---|---|---|
| **① Schema 驗證** | API server | CRD 的 OpenAPI schema（`enum`、`required`、型別） |
| **② Admission webhook** | operator 自己寫的程式 | 任意邏輯 |

**第二層可以拒絕第一層允許的東西**，而且**兩層可能不同步**——
CRD schema 是宣告在 YAML 裡的，webhook 是程式碼，
改了程式碼忘了更新 schema，就會出現這種情況。

### 對你的實際影響

**你不能只靠 `oc explain` 或 CRD schema 來決定怎麼寫 YAML。**

```bash
oc explain dsc.spec.components.kueue.managementState
# 會告訴你可以填 Managed —— 而那是錯的
```

**唯一可靠的驗證是 `--dry-run=server`：**

```bash
oc apply -f my-dsc.yaml --dry-run=server
```

`--dry-run=server` 會**真的送到 API server 跑完整個 admission 流程**
（包含所有 webhook），只是不寫進 etcd。

⚠️ 注意不要用 `--dry-run=client`——那個只做本機的 schema 檢查，
**完全不會碰到 webhook**，也就抓不到這個問題。

> 這一條可以直接寫進你的變更流程：
> **所有要進正式環境的 YAML，先在測試叢集 `--dry-run=server` 過一次。**
> 那比人工 review 有效得多，因為它跑的是真正會擋你的那段程式。

---

## 5. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| Kueue | `KueueReady=True` | **送一個超過配額的任務，它真的排隊而不是直接跑** |

第二欄跟這個系列其他篇是同一個模式：**排隊機制的價值在它會擋東西。
沒擋過任何東西的排隊器，跟沒有排隊器一樣。**

---

## 6. 什麼時候不需要它

- 單一團隊、GPU 不需要搶
- 用 `ResourceQuota` 就能表達你的規則（例如「這個 namespace 最多兩張卡」）

**Kueue 解決的是「順序」與「公平」，不是「上限」。**
如果你要的只是上限，`ResourceQuota` 更簡單而且不用多裝一個 operator。

---

## 7. 我沒驗過的部分

我**沒有實際裝 RHBOK 也沒有跑過排隊**——我的叢集只有一個節點、
沒有 GPU、CPU 還已經用到 99%，跑不了有意義的排隊實驗。

所以這篇能給你的是：**怎麼開、為什麼被擋、正解是什麼**，
以及那個兩層驗證的教訓。**排隊行為本身我沒有證據。**

---

**你們有在用 Kueue 或其他排隊機制嗎？還是靠 namespace quota 硬擋？**

{% include lab-env.html %}
