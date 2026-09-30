---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 24：版本策略——3.x 與 2.x，以及沒有升級路徑這件事"
date: 2026-09-24 09:00:00 +0800
tags: [openshift-ai, odh, rhoai, version, migration, tutorial, ironman2026]
excerpt: "網路上大部分教學是 2.x 的，照做會卡。這篇給一個三十秒的判斷法，列出實際改了什麼，以及一個評估時必須先知道的事實。"
feedback_question: "你們現在跑的是 2.x 還是 3.x？如果是 2.x，有升級計畫嗎？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。"
---

## 這是什麼、解決什麼問題

你 Google「RHOAI 部署模型」，找到一篇寫得很好的文章，照著做——**卡住。**

不是你的問題，是**那篇文章是 2.x 的**。

而且它不會告訴你。文章上通常沒寫版本，指令看起來也很正常，
**只是那個欄位在 3.x 已經不存在了**。

這篇給你一個判斷法，和一份改動清單。

---

## 什麼時候你會用到

- **每次要照著網路上的教學做之前**（真的，每次）
- 手上是 2.x 要評估要不要升
- 要判斷手上的文件還能不能用

---

## 步驟一：三十秒判斷一篇教學是哪個版本

**看到以下任何一項，那是 2.x 的材料：**

| 訊號 | 3.x 的情況 |
|---|---|
| 要你裝 **Serverless / Knative** | 預設不需要（只有要縮到 0 才裝） |
| 要你裝 **Service Mesh / Istio** | 預設不需要（同上，跟 Serverless 一起） |
| 提到 **ModelMesh** | **已移除**，不在元件清單裡 |
| 提到 **Accelerator Profile** | 改名成 Hardware Profile |
| DSC 裡寫 `datasciencepipelines` | 改名成 `aipipelines` |
| route 叫 `rhods-dashboard` / `odh-dashboard` | 走 `data-science-gateway` |
| 用 `kfp` v1 的 `ContainerOp` | KFP v2，寫法完全不同 |

⭐ **第一列「不需要 Serverless」不用引用文件，CRD 自己就寫著**：

```bash
oc explain dsc.spec.components.kserve
# Only RawDeployment mode is supported.
```

**驗證你自己的叢集是哪一種**——最快的一行：

```bash
oc get csv -A --no-headers | awk '{print $2}' | sort -u
```

我的 lab：

```
cert-manager-operator.v1.20.0
opendatahub-operator.v3.5.0
openshift-gitops-operator.v1.21.3
openshift-pipelines-operator-rh.v1.23.2
```

**沒有 Serverless、沒有 Service Mesh，有 cert-manager。** 這就是 3.x 的樣子。

（2.x 那邊會看到 `serverless-operator`、`servicemeshoperator`、`authorino-operator`。）

---

## 步驟二：實際改了什麼

### 模型服務：從 Serverless 變成原生

| | 2.x 預設 | 3.x 預設 |
|---|---|---|
| 部署模式 | Serverless（Knative） | **Standard**（原生 Deployment） |
| 依賴 | Knative + Istio + Authorino | **cert-manager** |
| 縮到 0 | ✅ 內建 | ❌ 預設沒有——要自己先裝 Serverless ＋ Service Mesh 兩個 operator（依 ODH 文件；我沒驗過） |
| 流量切分 | Knative 管 | KServe 的 `canaryTrafficPercent` |

**這是最大的一個改變。** 好處是少維運兩套龐大的元件，
代價是**「縮到 0」不再是預設能力**。

⚠️ **值也改了名**：你寫 `deploymentMode: RawDeployment`，
存進去會變成 `Standard`（舊名字仍然接受、自動轉換）。
**這會讓 GitOps 的 drift 檢查誤報**，Day 25 會處理。

### 元件改名

```bash
oc explain dsc.spec.components     # ← 這是唯一不會過期的清單
# 我這台列出 17 個元件
```

`datasciencepipelines` → `aipipelines`，
`modelmeshserving` 整個不見了。

**照舊 YAML apply 會被拒絕**，訊息只說有個不認識的欄位。

### 有些元件變成 Unmanaged

我的 lab 上 `kueue` 是 `Unmanaged`，狀態是：

```
KueueReady  False  |  PreConditionFailed
```

**不是壞掉，是這個版本不再由 operator 託管。**
要用得自己裝。這類變化在 release note 裡，**但很容易被跳過**。

---

## 步驟三：⭐ 評估時必須先知道的一件事

**2.x 到 3.x 沒有原地升級路徑。**

這表示現有的 2.x 環境要上 3.x，是**重建**不是升級：
新叢集或新安裝、把東西搬過去、再切換。

**而且沒有 rollback。**

這件事對評估的影響比技術差異大得多：

| 你的處境 | 建議 |
|---|---|
| 全新導入 | **直接 3.x**，沒有理由從 2.x 開始 |
| 已有 2.x 在跑 | 排一個獨立的遷移專案，**不要當成版本升級處理** |
| PoC 階段 | 3.x，**但要知道你學的東西跟公司現有的 2.x 不一樣** |

最後一列是我自己的處境，**值得特別提**：
如果你在 3.x 上做 PoC，而公司環境是 2.x，
**你的 PoC 結論有一部分不能直接套用**——
特別是模型服務那一段，兩個版本的架構完全不同。

---

## 怎麼確認做對了

| | 檢查 | 指令 |
|---|---|---|
| 1 | 你的版本 | `oc get csv -A \| grep -iE 'opendatahub\|rhods'` |
| 2 | 有沒有 2.x 的依賴 | `oc get csv -A \| grep -icE 'serverless\|servicemesh'` → 應為 0 |
| 3 | 元件名稱對 | `oc explain dsc.spec.components` |
| 4 | 部署模式 | `oc get cm inferenceservice-config -n opendatahub -o jsonpath='{.data.deploy}'` |
| 5 | ⭐ **文件版本對得上** | 見下 |

第 5 項：**你手上那份安裝文件，拿第 3 項的輸出對一次。**

我在寫這系列時發現，**ODH 官網的快速安裝頁到現在還寫著
「使用 kserve 需要先裝 Serverless 和 Service Mesh 兩個 operator」**，
而我的 3.5 叢集上一個都沒有，KServe 跑得好好的。

**官網也會落後。** 唯一不會騙你的是你自己的叢集。

---

## 常見問題

**Q：該用哪個版本寫新的東西？**
A：3.x。**但把版本寫進你的文件裡**——
你今天寫的教學，一年後也會變成別人踩的坑。

**Q：ODH 版本跟 RHOAI 版本怎麼對？**
A：ODH 是上游，版本號相近但不是一對一。
**功能上 ODH 通常領先**，穩定性和支援 RHOAI 較好。
用 ODH 學、用 RHOAI 上正式環境是合理的組合。

**Q：3.x 還會再大改嗎？**
A：我不知道，**這個我沒有內部消息**。
但 2.x→3.x 這次的改動幅度說明了一件事：
**把平台設定寫進 GitOps 是划算的**——
重建的時候，你至少知道原本設了什麼。

**Q：我的 2.x 環境還能撐多久？**
A：看支援生命週期，去查官方的 lifecycle 頁面。
**這個一定要查最新的，不要相信任何文章裡的日期**——包括這篇。

---

**你們現在跑的是 2.x 還是 3.x？如果是 2.x，有升級計畫嗎？**

{% include lab-env.html %}
