---
layout: post
title: "Workbench 被 webhook 擋住了——四個指令查到根因，以及一個我答不出來的問題"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-14 09:00:00 +0800
tags: [openshift-ai, odh, workbench, jupyter, webhook, troubleshooting]
excerpt: "兩個 admission webhook 擋住 Notebook 建立。這篇是查根因的過程、一個撐得過重啟的修法、以及一個我查不出答案的矛盾——同一個叢集上有一個五天前建的 Notebook 好好地跑著。"
feedback_question: "你們的 workbench 是走 OpenShift AI 的 dashboard，還是自己開 Jupyter？"
---

## 1. 這是什麼

**Workbench 就是一個跑在叢集裡的 Jupyter（或 VS Code / RStudio）。**

在 OpenShift AI 裡它是一個 `kubeflow.org/v1` 的 `Notebook` 自訂資源——
dashboard 上點「建立 workbench」，實際做的就是建一個 `Notebook` CR。

> **Java 類比**：像把每個人的 IDE 搬進伺服器。
> 差別不是「線上版 IDE」，是**環境由平台定義**——
> 用哪個 image、掛哪個 PVC、連哪個 S3，都是宣告出來的。

---

## 2. 什麼時機需要它

不是「因為要寫 Python」。是當你聽到這句話的時候：

> **「在我電腦上跑得動啊。」**

Workbench 解決的是**環境一致性**，不是「提供 Jupyter」。
具體來說，當你開始遇到這些：

- 每個人的 pandas 版本不一樣，同一份 notebook 結果不同
- 資料在 S3，但每個人用自己的 credential，離職就斷
- 訓練要 GPU，但 GPU 在機房，筆電沒有
- 稽核問「這份分析是在哪台機器上跑的」，沒人答得出來

**如果你的團隊只有一個人、資料在本機、也不需要 GPU——你還不需要它。**

---

## 3. 怎麼用（以及它為什麼不能用）

正常流程是：DSC 開 `workbenches` → dashboard 上點建立 → 選 image、選規格 → 開。

我試了。**建不出來。**

```bash
oc apply -f notebook.yaml
```
```
Error from server (InternalError): failed calling webhook
"connection-notebook.opendatahub.io": the server could not find the requested resource
```

---

## 4. ⭐ 四個指令查到根因

這一段是這篇真正的內容，而且**這個手法可以用在任何 operator 上**。

### 第一步：webhook 指向誰

```bash
oc get mutatingwebhookconfiguration <name> -o jsonpath='{range .webhooks[*]}{.name} → svc={.clientConfig.service.namespace}/{.clientConfig.service.name}{.clientConfig.service.path} failurePolicy={.failurePolicy}{"\n"}{end}'
```
```
connection-notebook.opendatahub.io →
  svc=openshift-operators/opendatahub-operator-controller-manager-service/platform-connection-notebook
  failurePolicy=Fail
```

### 第二步：那個 service 活著嗎

```bash
oc get endpoints opendatahub-operator-controller-manager-service -n openshift-operators
# 10.217.0.62  10.217.0.63  10.217.0.76      ← 三個 endpoint，活得好好的
```

**service 是好的。** 所以問題不在網路，在**路徑**。

### 第三步：operator 實際註冊了哪些路徑

```bash
oc logs -n openshift-operators deploy/opendatahub-operator-controller-manager --tail=400 \
  | grep -oE '"path":"[^"]*"' | sort -u
```
```
/convert
/mutate-datasciencecluster-v1
/mutate-datasciencecluster-v2
/mutate-hardware-profile
/mutate-prometheus-monitors
/validate-dashboard-acceleratorprofile
/validate-dashboard-hardwareprofile
/validate-datasciencecluster-v1
/validate-datasciencecluster-v2
/validate-dscinitialization-v1
/validate-dscinitialization-v2
```

**十一個。而 `/platform-connection-notebook` 不在裡面。**

### 第四步：那 webhook 是誰註冊的

```bash
oc get csv opendatahub-operator.v3.5.0 -n openshift-operators \
  -o jsonpath='{range .spec.webhookdefinitions[*]}{.generateName} path={.webhookPath}{"\n"}{end}'
```
```
connection-notebook.opendatahub.io  path=/platform-connection-notebook    ← 宣告了
...（其餘 12 個）
```

**CSV 宣告 13 個，operator 只實作 11 個。**
OLM 忠實地照 CSV 建立了 webhook 設定，指向一個不存在的路徑，
而 `failurePolicy: Fail` ——**於是叢集上所有 Notebook 都建不出來。**

> **這個比對手法值得記下來**：
> `CSV 宣告的 webhook path` vs `operator log 註冊的 path`。
> 兩邊對不上，就是 operator 打包時漏掉東西。

---

## 5. 第二道牆，以及一個關於 `failurePolicy` 的誤解

繞過第一個之後，第二個 webhook 接著擋：

```
admission webhook "hardwareprofile-notebook-injector.opendatahub.io"
denied the request: unexpected kind: Notebook
```

這個 webhook 的路徑（`/mutate-hardware-profile`）**是有實作的**，
但它的 handler 不認得 `Notebook` 這個 kind——而 OLM 把它註冊給了
`kubeflow.org/v1 notebooks` 的 CREATE 與 UPDATE。

我第一個念頭是「那把 `failurePolicy` 改成 `Ignore` 就好」。

**沒有用。**

> **`failurePolicy: Ignore` 只管「叫不到 webhook」，不管「叫到了但它說不行」。**

第一個 webhook 是 404（叫不到）→ `Ignore` 有效。
第二個是**成功回應了一個拒絕**（`allowed: false`）→ 那是一個正常的 admission 回應，
`failurePolicy` 根本不參與。

這個區別在排錯時很值錢：**看到 `denied the request:` 就知道 `failurePolicy` 救不了你**，
要改的是 webhook 的 `rules` 或 `objectSelector`。

---

## 6. ⭐ 修法：改 CSV，不要改生成出來的 webhook

我先試了直覺的做法，**兩個都失敗**，過程本身有價值：

### ✗ 失敗一：改 `failurePolicy` 為 `Ignore`

```bash
oc patch mutatingwebhookconfiguration <w> --type=json \
  -p '[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]'
```

沒有用。**`Ignore` 只管「叫不到 webhook」，不管「叫到了但它說不行」。**

第一個 webhook 是 404（叫不到）→ `Ignore` 有效。
第二個是**成功回應了一個拒絕**（`allowed: false`）——那是正常的 admission 回應，
`failurePolicy` 根本不參與。

> 排錯判準：**看到 `denied the request:` 就知道 `failurePolicy` 救不了你。**

### ✗ 失敗二：直接改（或刪掉）生成出來的 webhook

```bash
oc delete mutatingwebhookconfiguration connection-notebook.opendatahub.io-77jb6
# deleted
oc get mutatingwebhookconfiguration | grep connection-notebook
# connection-notebook.opendatahub.io-sxp2g   1s      ← 一秒後就回來了（新後綴）
```

**OLM 在一秒內重建它。** 改 `rules` 也一樣，會被 reconcile 回去。

因為那些 `MutatingWebhookConfiguration` 是 **OLM 從 CSV 生出來的產物**，
不是真相來源。改產物沒有用。

### ✅ 成功：改 CSV 的 `webhookdefinitions`

真相在 CSV 裡：

```bash
oc get csv opendatahub-operator.v3.5.0 -n openshift-operators \
  -o jsonpath='{range .spec.webhookdefinitions[*]}{.generateName} path={.webhookPath}{"\n"}{end}'
```

找到那個 index（我這裡是 `[10]`），把它的 `rules` 清空——
**因為它的 handler 明確說「不接受 Notebook」，那它本來就不該被註冊給 notebooks**：

```bash
oc patch csv opendatahub-operator.v3.5.0 -n openshift-operators --type=json \
  -p '[{"op":"replace","path":"/spec/webhookdefinitions/10/rules","value":[]}]'
```

約 20 秒後，OLM 把生成的 webhook 同步成空 rules。然後：

```bash
oc apply -f notebook.yaml
# notebook.kubeflow.org/fix-test created        ← 不需要任何 bypass label
```

**Jupyter 跑起來了：**

![Jupyter 在叢集裡跑起來](/assets/img/rhoai/workbench-jupyter.png)

### ✅ 而且它撐得過 operator 重啟

這是我最在意的一項——workaround 最怕的就是「重啟就沒了」：

```bash
oc rollout restart deploy/opendatahub-operator-controller-manager -n openshift-operators
# 重啟後：
#   CSV[10] rules=[]                            ← 還在
#   生成的 webhook rules=                        ← 還在
#   oc apply notebook → created                  ← 還能建
#   WorkbenchesReady = True ReconcileSuccess     ← 綠了
```

> ⚠️ **但它撐不過 operator 升級。** CSV 是某個版本的產物，
> 升到 3.6 會產生新的 CSV，這個修改會消失。
> **這是「撐到官方修好為止」的解法，不是永久的。**
> 所以要追 [#3792](https://github.com/opendatahub-io/opendatahub-operator/pull/3792)。

### ⚠️ 你放棄了什麼

那個 webhook 的正職是**幫 Notebook 注入 Hardware Profile 的資源設定**。
清空 rules 之後，那個注入不會發生——**如果你靠 Hardware Profile 配 GPU，
就得自己在 Notebook 的 spec 裡寫 resources。**

我的 lab 沒有 GPU，所以這個代價對我是零。**你的環境要自己評估。**

---

## 6.5　⚠️ 一個我答不出來的矛盾

寫到這裡我發現一件事，而且它讓上面的結論必須加限定。

**同一個叢集上，有一個五天前建立的 Notebook，到現在還好好地跑著：**

```bash
oc get notebook -n llm-serve-demo
# my-workbench     41m      ← 我今天修完之後建的
# ryan-workbench   5d6h     ← 8/24 建的，一直 Running
```

而那個擋我的 webhook，**是 8/24 02:51 建立的**——
比 `ryan-workbench`（8/24 03:49）**早了 58 分鐘**。

**也就是說：webhook 存在的情況下，那個 Notebook 建成功了。**

我試著找出差異，測了三種寫法：

| 測試 | 結果 |
|---|---|
| A：我原本的寫法（`inject-oauth: false`） | ❌ 被擋 |
| B：完全照 `ryan-workbench` 的 annotations 與 label | ❌ 被擋 |
| C：加上 `hardware-profile-name` 空值 | ❌ 被擋 |

**三種一模一樣地被擋。** 所以差別不在 Notebook 的內容。

我也去查 webhook 的 `managedFields` 想看它何時被改過——**那個欄位是空的，查不到歷史**。

### 所以誠實的結論是

**我不知道中間改變了什麼。**

可能是 operator 在某次 reconcile 時改了 webhook 的 rules，
可能是我今天啟用其他元件時觸發了什麼，也可能是別的。
**我沒有證據，所以我不猜。**

⭐ **但這個「查不出來」本身有一個很實用的推論：**

> **這個阻擋不是從一開始就存在的。也就是說——「我們測的時候可以用」
> 不代表下週還可以用。**

而那正是驗收最該防的那種東西：**一個會在你不注意的時候出現、
而且沒有留下任何變更紀錄的行為改變。**

所以我在下面的驗收建議裡，把「當著我的面建一個」改成了
「**當著我的面建一個，而且下次來的時候再建一個**」。

---

## 7. 這是已知 bug，有 JIRA 編號

我本來以為是自己環境的問題。去上游翻，找到這個：

**[opendatahub-operator #3792](https://github.com/opendatahub-io/opendatahub-operator/pull/3792)**
`fix(webhook): prevent silent denial on notebook create/update fallthrough`

> Fixes **RHOAIENG-56113**：notebook mutating webhook 的 `Handle` 函式，
> 在 Create/Update 路徑掉出去而沒設定明確回應時，會回一個零值的
> `admission.Response`（`Allowed=false`），**靜默拒絕合法的 notebook 請求**。

**開於 2026-07-11，至今仍是 open——修正還沒合併進任何版本。**

而那個 `RHOAIENG-` 開頭的編號是 Red Hat 的內部追蹤號，代表**商用產品線也在追這件事**。

另外兩個相關的：
[#3882](https://github.com/opendatahub-io/opendatahub-operator/pull/3882)（已合併）Notebook webhook 的 OLM 清理；
[#2425](https://github.com/opendatahub-io/opendatahub-operator/pull/2425)（2025-09 已合併）
「unsupported kind 在 HWProfile 應該仍允許建立」——**跟我第二個症狀一樣，但一年前就修過了**。
所以我看到的要嘛是回歸、要嘛是另一條 code path，這點我沒有證據分辨。

---

## 8. 順帶：兩個一起出現的干擾訊息

DSC 報 Workbench 沒就緒時，訊息是這樣：

```
deployment notebook-controller-deployment is scaled to zero.
Warning: 2 ImageStream tag(s) failed to import:
  jupyter-rocm-minimal:3.6 (... "quay.io/opendatahub/...:3.6_ea1-v1.47" not found)
```

**兩件事都是真的，但都不是 webhook 的問題。** 而且它們各自也值得記：

- **notebook controller 預設 `replicas=0`**，要自己 scale 起來
- **內建的 imagestream 指向不存在的 tag**（`3.6` 的 tag 在 quay.io 上 not found，`3.5` 正常）
  ——這對**離線環境**特別要命：你會照著一份含有不存在 image 的清單去鏡像

而真正擋住我的第三件事，訊息裡一個字都沒提：**CPU 不夠**，
notebook pod `Pending` 在 `Insufficient cpu`。那要 `oc describe pod` 才看得到。

---

## 9. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| Workbench | `WorkbenchesReady=True`、controller pod Running | **真的建一個 Notebook 出來，而且它 Running** |

**驗收這一項只有一個方法：建一個，開起來，在裡面跑一行 code。**
看 `WorkbenchesReady` 沒有用——我的叢集在 controller 全部 1/1 的狀態下，
仍然一個 Notebook 都建不出來。

---

## 10. 給驗收的一句話

> **「請當著我的面，從 dashboard 建一個 workbench 並開啟它。」**

不要接受截圖，不要接受「這個功能是標配」。這個 bug 的特性是：
**所有元件都顯示正常，只有真的去建的時候才會失敗。**

---

**你們的 workbench 是走 OpenShift AI 的 dashboard，還是自己開 Jupyter？**

{% include lab-env.html %}
