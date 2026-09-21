---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 21：權限——誰能做什麼"
date: 2026-09-21 09:00:00 +0800
tags: [openshift-ai, odh, rbac, security, tutorial, ironman2026]
excerpt: "用 kubeadmin 測，你永遠不會發現權限問題。這篇講 OpenShift AI 的權限分兩層、怎麼給、以及怎麼在使用者踩到之前先自己踩一次。"
feedback_question: "你們的資料科學家有 cluster-admin 嗎？如果有，是因為需要還是因為麻煩？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。RBAC 的行為兩版一致，但 `aggregate-to-edit` 角色的數量會隨你裝了哪些 operator 而不同。"
---

## 這是什麼、解決什麼問題

**前面十九天，我都是用 `kubeadmin` 做的。**

那是最快的做法，也是最會出事的做法——**因為管理員看不到權限問題。**
你測得一切正常，使用者一進來就卡住，而畫面上的訊息往往不是「你沒權限」。

這篇講權限怎麼分、怎麼給，以及**怎麼在使用者踩到之前自己先踩一次。**

---

## 什麼時候你會用到

- 要把平台開放給第一個真實使用者
- 多個團隊共用一個叢集
- 資安要求「最小權限」
- **有人跟你要 cluster-admin**（先別給，看完這篇）

---

## 步驟一：兩層權限，先分清楚

OpenShift AI 的權限是**兩層疊起來**的：

| 層 | 管什麼 | 設在哪 |
|---|---|---|
| **平台層** | 誰是平台管理員、誰能用這個平台 | ODH 的 `Auth` CR |
| **專案層** | 在某個 project 裡能做什麼 | 標準 k8s RBAC |

平台層長這樣：

```bash
oc get auth -o jsonpath='{.items[0].spec}' | jq   # Auth 是 cluster-scoped，不用 -A
```
```json
{
  "adminGroups": ["odh-admins"],
  "allowedGroups": ["system:authenticated"]
}
```

**`allowedGroups` 是「誰能進來」**，預設是所有登入的人。
**`adminGroups` 是「誰能改平台設定」**（notebook image、hardware profile 這些）。

⚠️ **多數企業會想改 `allowedGroups`。** 預設值等於全公司都看得到入口，
雖然進去之後沒有 project 也做不了事，**但入口本身可能就是要控管的**。

---

## 步驟二：專案層用標準 RBAC

好消息是：**這裡沒有新東西要學。** `admin` / `edit` / `view` 三個內建角色直接可用。

```bash
oc adm policy add-role-to-user edit alice -n team-a
```

三個角色在 AI 平台上的意思：

| 角色 | 能做什麼 |
|---|---|
| `view` | 看得到 workbench、pipeline、模型，**什麼都不能改** |
| `edit` | 建 workbench、跑 pipeline、部署模型 |
| `admin` | 加上管理這個 project 的成員 |

**大部分資料科學家需要的是 `edit`，而且只在自己團隊的 namespace 裡。**

**驗證這一步**——用 `can-i`，不要用猜的：

```bash
oc auth can-i create inferenceservices -n team-a --as=alice
# yes

oc auth can-i create inferenceservices -n team-b --as=alice
# no
```

`--as=` 可以模擬任何使用者，**這是本篇最有用的一個指令**。

> 小提醒：`--as=` **只模擬使用者，不會帶入他的 group**。
> 真實使用者登入後會多帶 `system:authenticated`，所以嚴格講 `--as=` 會**略為低估**權限。
> 本篇實測這個差別只影響 `projectrequests` 的 create 和 `useroauthaccesstokens` 兩項，
> 不影響下面的結論；要完全比照真實使用者，加上
> `--as-group=system:authenticated --as-group=system:authenticated:oauth`。

### ⚠️ 但 `can-i` 會騙你——我實測到了

我建了一個真帳號 `alice`，只給 `team-a` 的 `edit`，**切到那個 project**，
然後問它能不能讀平台設定：

```bash
oc project team-a          # ← 這行不是裝飾，等一下就知道為什麼
oc auth can-i get datascienceclusters --as=alice
```
```
Warning: resource 'datascienceclusters' is not namespace scoped in group 'datasciencecluster.opendatahub.io'

yes
```

**然後真的去讀：**

```bash
oc get dsc --as=alice
```
```
Error from server (Forbidden): datascienceclusters.datasciencecluster.opendatahub.io
is forbidden: User "alice" cannot list resource "datascienceclusters"
in API group "datasciencecluster.opendatahub.io" at the cluster scope
```

**`can-i` 說 yes，實際是 Forbidden。**

### 先講那個 `Warning`

上面那段輸出**不是乾淨的一個 `yes`**，第一行有警告，而且它就是答案：

```
Warning: resource 'datascienceclusters' is not namespace scoped ...
                                        ↑ 這裡
```

**`Warning` 在上、`yes` 在下，中間還空一行**——眼睛會直接跳到結論，
我自己第一次就是這樣跳過去的。

> **看到這行 Warning，`can-i` 的答案就不能信。**
> 準確講，`can-i` 不是騙你，是**它提醒過你，但提醒長得不像警報**。

### 再講那個 `oc project team-a`

`can-i` 沒加 `-n` 的時候，用的是**你 kubeconfig 當前的 project**。同一個叢集、
同一個 alice，只差當前 project：

| 當前 project | `oc auth can-i get datascienceclusters --as=alice` |
|---|---|
| `default`（或空的） | **no** |
| `team-a` | **yes** |

所以差在有沒有 namespace 上下文：

| 指令 | 回答 | 真相 |
|---|---|---|
| `can-i get dsc -n team-a` | **yes** | ❌ 錯的 |
| `can-i get dsc -n team-b`（alice 沒權限的 project） | no | ✅ 對的 |
| `can-i get dsc --all-namespaces` | no | ✅ 對的 |
| 真的 `oc get dsc` | — | **Forbidden** |

⭐ **這跟你給多大的角色無關。** 我另外開了乾淨的 `team-c`，
給 `carol` 的是**唯讀的 `view`**，結果一模一樣：`can-i` 回 yes、真的讀還是 Forbidden。

### 為什麼會這樣

原因是 **`DataScienceCluster` 是 cluster-scoped 的**，
而 namespaced 的 `edit` 角色裡卻有它的權限：

```bash
oc auth can-i --list -n team-a --as=alice | grep datasciencecluster
# datascienceclusters...  [create update patch delete get list watch]
```

清單上寫得很大方，**但那個授權掛在 namespace 層，而資源在 cluster 層——
所以它什麼也不是。**

**這不是 OpenShift AI 特有的毛病。** 那個角色不是平台放的，是 **OLM 裝 operator 時自動生的**：
每個 CRD 都會配三個 ClusterRole，各自帶 `aggregate-to-edit` / `-view` / `-admin` label，
自動併進內建的三個角色。

```bash
oc get clusterrole datascienceclusters.datasciencecluster.opendatahub.io-v1-edit \
  -o jsonpath='{.metadata.labels}'
```
```json
{
  "olm.managed": "true",
  "olm.opgroup.permissions/aggregate-to-a6b88a9c877aa83b-edit": "true",
  "rbac.authorization.k8s.io/aggregate-to-edit": "true"
}
```

最後那個 `aggregate-to-edit` 就是它併進 `edit` 的原因；
這個 ClusterRole 的 owner 是 **CRD 自己**，不是 ODH。

我這台叢集上，**光 ODH 的 CRD 就產了 18 個這種角色，全叢集有 93 個**。
換句話說，**這個假象在任何裝了 operator 的叢集上都可能出現**，不是 AI 平台的專利。

### ⚠️ 但也別因此草木皆兵

我原本在這裡寫「cluster-scoped 的資源都要小心」，**實測後發現這句話太寬了**。
同一個 alice，四個資源的結果：

| 資源 | cluster-scoped？ | `can-i -n team-a` | 真的執行 | 會騙你？ |
|---|---|---|---|---|
| `DataScienceCluster` | ✅ | **yes** | Forbidden | ✅ **會** |
| `HardwareProfile` | ❌ **其實是 namespaced** | yes | 成功 | ❌ 不會 |
| `ImageDigestMirrorSet` | ✅ | **no** | Forbidden | ❌ 不會 |
| `ClusterServingRuntime` | ✅ | **no** | Forbidden | ❌ 不會 |

**cluster-scoped 不是充分條件。** IDMS 和 ClusterServingRuntime 也是 cluster-scoped，
`can-i` 對它們回答完全正確——因為 OLM **沒有**幫它們產 aggregate 角色。

> **兩個條件同時成立才會出事：cluster-scoped ∧ 有 `aggregate-to-edit` 角色。**

要判斷某個資源屬不屬於這一類，一行就夠：

```bash
oc get clusterrole -l rbac.authorization.k8s.io/aggregate-to-edit=true -o name \
  | grep '/datascienceclusters\.'
```

有輸出就代表這個資源被聚合進 `edit` 了；再配上 `oc api-resources` 看它是不是
cluster-scoped，兩個都中才需要真的執行一次。

> **`can-i` 只是快篩，不是答案。**
> 看到 `not namespace scoped` 的 Warning，或資源同時符合上面兩個條件，
> **就真的執行一次**，或至少加 `--all-namespaces`。

**這條我原本沒寫。** 上面那句「用 `can-i` 不要用猜的」現在要補一句：
**用 `can-i` 快篩，用真的執行確認。**

---

## 步驟三：⭐ 兩個容易漏的東西

### 漏掉一：dashboard label

Day 6 講過，但這裡要再提一次，因為它和權限混在一起最難查：

```bash
oc label namespace team-a opendatahub.io/dashboard=true
```

**權限給對了、label 沒貼，使用者一樣看不到那個 project。**
而症狀完全一樣：「我進去什麼都沒有」。

**排查順序**：先 `can-i` 確認權限，再看 label。

### 漏掉二：ServiceAccount 的權限

**使用者的權限和他跑起來的東西的權限是兩回事。**

Pipeline 是用 SA 跑的，不是用你的身分：

```bash
oc get pods -n team-a -o custom-columns='NAME:.metadata.name,SA:.spec.serviceAccountName'
```

**使用者有權限、SA 沒有，pipeline 會失敗**，
而錯誤訊息出現在 pod log 裡不是 UI 上。

---

## 步驟四：權限不足時看到什麼

**這是我覺得最該事先知道的一件事。**

k8s 的 RBAC 拒絕會回 403 加一段清楚的訊息。
**但你多半看不到那段訊息**，因為中間隔了好幾層：

| 你看到的 | 實際上是 |
|---|---|
| 列表是空的 | 沒有 list 權限（**不是沒有東西**） |
| 按鈕不見了 | UI 依權限隱藏 |
| 「載入失敗」 | API 回了 403，UI 沒顯示細節 |
| pipeline 卡在 Pending | SA 不能建 pod |

**「空的」和「沒權限」在畫面上長得一模一樣**，這是最花時間的一種。

遇到任何一個，**先跑 `can-i`**：

```bash
oc auth can-i --list -n team-a --as=alice | head -20
```

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 使用者進得去 dashboard | 用他的帳號登入 |
| 2 | 看得到自己的 project | label + 權限都要對 |
| 3 | **看不到別人的 project** | ⭐ 這比第 2 項重要 |
| 4 | 能建 workbench | `can-i create notebooks` |
| 5 | pipeline 跑得起來 | SA 的權限 |
| 6 | ⭐ **用真的帳號走一次** | 見下 |
| 7 | **cluster-scoped 的權限真的執行過** | ⚠️ 不能只信 `can-i` |

第 6 項：

> **建一個測試帳號，權限照真實使用者給，然後用它把 Day 14 那條路走一次。**
>
> 這是唯一能發現權限問題的方法。用 `kubeadmin` 測一百次都不會遇到。

第 3 項也要真的測。**「應該看不到」和「確認看不到」是兩件事**，
而在多團隊共用的叢集上，這是資安會查的第一項。

---

## 常見問題

**Q：資料科學家要 cluster-admin 才能工作嗎？**
A：不用。**在自己 namespace 有 `edit` 就能做完 Day 14 那整條路。**
會覺得需要 cluster-admin，通常是因為前面漏了 label 或 SA 權限，
**權限被誤診成不夠，其實是設錯地方。**

**Q：怎麼讓每個團隊只看到自己的東西？**
A：一個團隊一個 namespace，各自給 `edit`。
**這是 OpenShift 上最自然的隔離單位**，
也是為什麼「Data Science Project」就是 namespace。

**Q：GPU 的權限怎麼控？**
A：權限控不了 GPU 用量，**那是配額不是權限**（`ResourceQuota`）。
兩者要一起設：權限管「能不能建」，配額管「能建多少」。

**Q：稽核要看什麼？**
A：至少三樣：誰有 `adminGroups`、每個 namespace 有哪些人、
**以及誰有 cluster-admin**。最後一項可以這樣列：

```bash
oc get clusterrolebinding -o json | jq -r '
  .items[] | select(.roleRef.name=="cluster-admin")
  | .metadata.name as $b
  | (.subjects[]? | select(.kind!="ServiceAccount") | "\($b)\t\(.kind)/\(.name)")'
```

```
cluster-admin     Group/system:masters
cluster-admins    Group/system:cluster-admins
cluster-admins    User/system:admin
kubeadmin         User/kubeadmin
```

⚠️ **那個 `select(.kind!="ServiceAccount")` 不能省。** 不濾掉的話，
輸出裡會混進十幾個 operator 自己的 SA，而且 binding 名和 subject 名會交錯印出來
**分不出誰是誰**——稽核要問的是「哪些**人**有」，不是「有幾筆 binding」。

---

**你們的資料科學家有 cluster-admin 嗎？如果有，是因為需要還是因為麻煩？**

{% include lab-env.html %}
