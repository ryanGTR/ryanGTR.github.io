---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 25：GitOps——模型部署與平台設定怎麼寫進 git"
date: 2026-09-25 09:00:00 +0800
tags: [openshift-ai, odh, gitops, argocd, tutorial, ironman2026]
excerpt: "企業環境裡，oc apply 上 prod 這件事本身就不合格。這篇講模型部署怎麼走 Argo、git 裡該放什麼、哪些平台設定進得去，以及三個會讓 Argo 永遠 OutOfSync 的欄位。"
feedback_question: "你們模型上 prod 是誰按下去的？那筆紀錄現在留在哪裡？"
lab_env_note: "⚠️ **本篇是設計，不是實測。** 2026-09-21 對帳時確認：我這台 `oc get application -A` 回的是 `No resources found`——GitOps operator 裝著、`AppProject default` 在，但我沒有真的建過 Application。下面的輸出是預期值，不是我機器上跑出來的。"
---

## 這是什麼、解決什麼問題

Day 24 提到 2.x 到 3.x 沒有升級路徑，要重建。

**重建的時候，你記得原本設了什麼嗎？**

- DSC 上開了哪些元件？
- 有幾個 Hardware Profile，各是什麼參數？
- IDMS 上 mirror 了哪些 image？
- 那個 workbench image 為什麼釘在 3.5 不是 3.6？

**這些答案應該在 git 裡，不是在人的腦袋裡。**

---

## 什麼時候你會用到

- 有兩個以上的環境（dev / prod）要保持一致
- 平台要重建（升級、災難復原、換叢集）
- 要能回答「這個設定是誰、什麼時候、為什麼改的」
- **稽核問「變更管理怎麼做的」**

---

## 前置條件

- OpenShift GitOps（Argo CD）operator
- 一個 git repo

---

## 步驟一：分清楚哪些該進 git

**不是所有東西都適合。** 分三類：

### ✅ 一定要進（平台設定）

| 物件 | 為什麼 |
|---|---|
| `DataScienceCluster` | 開了哪些元件——**重建時最重要的一份** |
| `HardwareProfile` | 資源政策 |
| `ImageDigestMirrorSet` | 離線環境的命脈 |
| `Auth`（adminGroups） | 誰是管理員 |
| `ServingRuntime`（自訂的） | 你自己寫的那些 |
| RoleBinding | 誰能做什麼 |

### ⚠️ 可以進，但要小心（工作負載）

| 物件 | 注意 |
|---|---|
| **`InferenceService`** | **該進，而且是重點**——見步驟三 |
| `DSPA` | 密碼欄位要抽掉 |
| `Notebook`（workbench） | **通常不該進**，見下 |

**Workbench 一般不進 git**，因為它是使用者的個人工作環境，
生命週期短、會頻繁開關。**把它 GitOps 化只會讓使用者不能自己開。**

`InferenceService` 剛好相反——**它是這一篇真正的重點**，下一節整節在講。

### ❌ 不能進

| 物件 | 為什麼 |
|---|---|
| Secret（明文） | **不用解釋** |
| PVC 裡的資料 | 那是資料不是設定 |
| Model Registry 的內容 | 那是**紀錄**，不是設定——進了 git 會有兩份真相 |
| 執行紀錄、artifact | 同上 |

**最後兩列是最容易搞錯的。**
判準是：**「這東西是被宣告的，還是被產生的？」**
被產生的東西進 git，你會得到兩份互相矛盾的真相。

---

## 步驟二：接起來

repo 結構最簡單的形式：

```
platform/
├── dsc.yaml
├── hardware-profiles/
│   ├── default.yaml
│   └── gpu-small.yaml
├── mirrors/
│   └── idms.yaml
└── rbac/
    └── team-a.yaml
```

Argo Application：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ai-platform
  namespace: openshift-gitops
spec:
  source:
    repoURL: https://git.internal/platform.git
    path: platform
    targetRevision: main
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      selfHeal: false      # ← 一開始先關，見下
      prune: false         # ← 這個更要關
```

⚠️ **`prune: true` 會刪掉 git 裡沒有的東西。**
在平台層開這個，**一個 git 上的失誤會刪掉正在跑的服務**。
先關著，等你確定 git 裡是完整的再說。

**驗證這一步：**

```bash
oc get application -n openshift-gitops
# 做對的話會看到：
# NAME          SYNC STATUS   HEALTH STATUS
# ai-platform   Synced        Healthy
```

> 📌 **誠實標註**：上面那三行是**預期輸出，不是我機器上跑出來的**。
> 2026-09-21 我重新對帳這一系列時確認，我這台 lab 上：
>
> ```
> $ oc get application -A
> No resources found
> ```
>
> GitOps operator 裝了、`AppProject default` 也在，**但我沒有真的建過 Application**。
> **這篇跟 Day 18–22 那幾篇的性質不同**——那幾篇的數字是量出來的，這篇是設計。
> 你照著做遇到不一致，先相信你的叢集。

---

## 步驟三：⭐ 模型部署走 Argo——這才是主戲

**前面講的是平台設定。但企業環境裡，`oc apply` 上 prod 這件事本身就不合格。**

Day 19 講換版時給了三種換法，都是手動的。**在有變更管理的地方，還有第四種：改 git。**

### git 裡放什麼

```yaml
# models/fraud-detection/isvc.yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: fraud-detection
  labels:
    model-registry/version: "v3"
  annotations:
    model/sha256: "e3b0c44298fc1c14..."     # ← 閘門通過的那一顆
spec:
  predictor:
    model:
      modelFormat: { name: onnx }
      storageUri: s3://models/fraud/v3/     # ← 每版一個路徑
```

⚠️ **模型檔本身不進 git。** git 裡放的是**指標**——
「哪一版、在哪裡、digest 是多少」。權重留在 S3。

**所以換模型 = 改一行 `storageUri` + 一行 digest，開 PR。**

### 為什麼這件事值得做

因為它一次回答了 Day 30 的兩題：

| 稽核問 | 沒有 Argo | 有 Argo |
|---|---|---|
| Q3 誰核准上線的？ | ❌ 某人的 shell history | ✅ **PR 的 approver** |
| Q4 上線前做了哪些檢查？ | ❌ 散在各處 | ✅ PR 上的 CI 結果 |

**「誰按了 merge」就是核准紀錄**，而且它自帶時間、自帶審閱者、自帶討論串。
這是我知道最便宜的一個合規補法——**不用買東西，不用寫程式。**

### 三個實作要點

**① 每一版一個路徑，永遠不要覆蓋。**
`s3://models/fraud/v3/` 而不是 `s3://models/fraud/latest/`。
覆蓋的話 git 上看不出變化，**Argo 也不會知道模型換了**——
你會有一個顯示 Synced 但內容早就變了的 Application。

**② 資料科學家的自助部署會被打回去。**
`selfHeal: true` 之後，任何在 dashboard 上手動建的 ISvc 會被還原。
**這是想要的行為，但要先講**，不然他們會以為平台壞了。
折衷做法：dev namespace 不納管，prod 才納管。

**③ `prune` 在模型這一層特別危險。**
git 上砍掉一個檔案 = 線上服務消失。
**開之前先確認 git 裡是完整的**，而且下線要走 Day 19 的清單而不是刪檔案。

### ⚠️ 一條誠實的界線

**Argo 保證的是「叢集上的宣告 == git 上的宣告」。**

它**不保證**「跑起來的權重 == 你以為的那一版」——
因為權重不在 git 裡，它在 S3。有人換掉 S3 上的檔案，
**Argo 全綠，而服務已經變了。**

所以 Day 28 那條對帳照樣要做，**Argo 不會替你做**：

```bash
# git 上宣告的
yq '.metadata.annotations."model/sha256"' models/fraud/isvc.yaml
# 線上實際跑的
oc exec <pod> -c kserve-container -- sha256sum /mnt/models/model.bin
```

**兩個要相等。** 把這一行放進 CI，GitOps 才真的閉環。

---

## 步驟四：⭐ 三個會讓它永遠 OutOfSync 的欄位

**這是 GitOps 化 AI 平台最惱人的部分。**

有些欄位是叢集自己填的，你的 git 裡不會有——
**Argo 會一直說 OutOfSync，然後大家就開始忽略它。**

### ① `deploymentMode` 被改寫

Day 10 講過：你寫 `RawDeployment`，叢集存成 `Standard`。
**Argo 每次比對都會看到差異。**

### ② DSC 的 status 與 defaulter 填的欄位

DSC 有 mutating webhook 會補預設值，
你的 git 裡只寫了 `managementState`，叢集上多出一堆欄位。

### ③ 被 operator 加的 label / annotation

`opendatahub.io/managed`、各種 owner reference。

**解法是明確告訴 Argo 忽略這些：**

```yaml
spec:
  ignoreDifferences:
  - group: serving.kserve.io
    kind: InferenceService
    jsonPointers:
    - /metadata/annotations/serving.kserve.io~1deploymentMode
  - group: datasciencecluster.opendatahub.io
    kind: DataScienceCluster
    jsonPointers:
    - /status
```

> **一個永遠 OutOfSync 的 Argo，跟沒有 Argo 是一樣的**——
> 因為沒有人會再看它。這幾條 `ignoreDifferences` 是讓它保持有用的前提。

---

## 步驟五：Secret 怎麼辦

**三條路，選一條：**

| 做法 | 怎麼運作 | 適合 |
|---|---|---|
| **Sealed Secrets** | 加密後可以進 git，只有叢集解得開 | 沒有 vault 的環境 |
| **External Secrets** | git 裡只放「去哪拿」，值在 vault | **有 vault 的環境，建議** |
| 手動建 | git 裡完全沒有 Secret | 最簡單，但重建時會漏 |

**第三種是多數人的現況，而它的問題在重建時才會浮現**——
你照著 git apply 完，然後所有東西因為缺憑證起不來。

**至少要做的事：在 git 裡放一份「需要哪些 Secret」的清單**，
即使值不在裡面。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | Application Synced | `oc get application -n openshift-gitops` |
| 2 | **不是永遠 OutOfSync** | 步驟四 |
| 3 | 改 git 會生效 | 改一個值，看叢集跟上 |
| 4 | ⭐ **重建演練過** | 見下 |

第 4 項是唯一真的驗證：

> **開一個全新的 namespace（或叢集），只用 git 裡的東西 apply 一次。**
>
> 缺什麼，就是你 git 裡漏的。
>
> **這個演練會發現大量「當初手動做過但沒記下來」的事**——
> 而那些正是重建時會卡住你的東西。

---

## 常見問題

**Q：`selfHeal` 要開嗎？**
A：**平台層建議開**（防止有人手動改）。
**工作負載層看情況**——如果團隊需要在 UI 上調參數，
開了會被一直改回去，然後他們就會來罵你。

**Q：DSC 進 git 了，還能在 UI 上開關元件嗎？**
A：可以改，**但 `selfHeal` 會把它改回去**。
這是想要的行為——**元件開關是平台變更，該走 PR。**

**Q：多環境怎麼管？**
A：Kustomize overlay。base 放共通的，overlay 放各環境差異
（資源大小、mirror 位址、adminGroups）。
**不要用複製貼上的兩份 YAML**，它們一定會漂移。

**Q：這樣做值得嗎？只有一套環境。**
A：一套環境的話，價值在**重建**和**追溯**，不在同步。
Day 24 說了 3.x 沒有升級路徑——
**那天到來的時候，git 裡有沒有東西，差別非常大。**

---

**你們模型上 prod 是誰按下去的？那筆紀錄現在留在哪裡？**

{% include lab-env.html %}
