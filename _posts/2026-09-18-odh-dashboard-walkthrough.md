---
layout: post
title: "ODH Dashboard 實際長怎樣：一個 project 從頭走到尾"
date: 2026-09-18 09:00:00 +0800
tags: [openshift-ai, odh, dashboard, dspa, pipelines, tutorial, screenshots]
excerpt: "前幾篇都在講 YAML 和指令。這篇改用畫面走一次：project 怎麼建、pipeline 上傳在哪、run 從哪裡看、產出物在哪一頁——以及每一頁上哪個欄位是真的要看的。"
feedback_question: "你們的人比較常用 dashboard 還是 oc？如果是 dashboard，卡在哪一頁最多？"
---

前面幾篇我都在給 YAML 和 `oc` 指令。但真實情況是：
**你的資料科學家不會用 `oc`，他們會打開那個網頁。**

這篇把 ODH dashboard 從頭走一次，每張圖標出「這一頁真正要看的是哪一欄」。

> 環境：CRC 4.22.7 + ODH 3.5.0。
> ⚠️ 商用版 RHOAI 的畫面**大致相同但不完全一樣**（配色、部分選單名稱、namespace）。

---

## 進入點：dashboard 在哪

```bash
oc get route -n openshift-ingress | grep -i "rh-ai\|dashboard"
# rh-ai   rh-ai.apps-crc.testing
```

⚠️ **3.x 的 route 名稱換過。** 2.x 是 `odh-dashboard`／`rhods-dashboard`，
我這裡是 `rh-ai`（走 `data-science-gateway`）。
照舊文件找 route 會找不到——**直接列出來看比較快**。

![ODH dashboard 首頁](/assets/img/rhoai/10-odh-home.png)

左邊那排就是全部功能：**Projects／AI hub／Develop & train／Applications／Settings**。

---

## 第一站：Projects

![Projects 列表](/assets/img/rhoai/15-odh-projects.png)

**「Project」就是一個 OpenShift namespace**，只是多貼了一個 label
讓 dashboard 認得。所以：

```bash
oc get project llm-serve-demo          # 同一個東西
```

⚠️ **你用 `oc new-project` 建的 namespace，dashboard 預設看不到。**
要它出現，namespace 需要 `opendatahub.io/dashboard: "true"` 這個 label——
這跟前一篇講的 Connection 是同一個機制:**UI 靠 label 認東西**。

點進一個 project，上面那排分頁就是全部工作：

```
Overview │ Workbenches │ Pipelines │ Deployments │ Cluster storage │ Connections │ Roles │ Permissions │ Settings
```

**這九個分頁基本上就是整個平台的操作面。** 後面每一站都在這裡面。

---

## 第二站：Connections（先設這個）

![Connections](/assets/img/rhoai/41-odh-connections.png)

⭐ **這一頁要先做，因為後面每一站都要用它。**

Workbench 要讀資料、pipeline 要存產物、模型服務要抓權重——都靠這裡的 S3 憑證。

**這一頁真正要看的欄位是右邊兩個**：

| 欄位 | 意思 |
|---|---|
| Model serving compatibility | 這份 connection 能不能給模型服務用 |
| **Connected resources** | **誰在用它** |

我這張圖上 `Connected resources` 是 `--`——**沒有任何東西在用它**。
而我的 pipeline 確實在跑，用的是另一份沒貼 label 的 Secret。

**所以這一欄是「影子憑證」的偵測器**：
UI 上有一份沒人用的 connection，同時有東西在用你看不到的憑證。

---

## 第三站：Pipelines

上傳一份編譯好的 pipeline YAML，它會出現在這裡：

![Pipeline definitions](/assets/img/rhoai/13-odh-pipeline-defs.png)

這一頁是**定義**（藍圖），不是執行。定義可以有多個版本——
我這裡有 `llm-lifecycle` 和 `v2-gate-registry` 兩個版本。

⚠️ 上傳之前 project 要先有 **pipeline server**（那就是 DSPA）。
沒有的話這一頁會叫你先建一個，而建立時**要選一個 S3 connection**——
所以順序是 Connections → Pipeline server → 上傳 pipeline。

---

## 第四站：Runs ← 最常看的一頁

![Runs 列表](/assets/img/rhoai/11-odh-pipeline-runs.png)

**這是日常最常打開的頁面。** 每一列是一次執行。

我特別要指出一件事：**看那個 `Status` 欄，四個 Failed 一個 Complete。**

而那個 Complete 的說明欄寫著「唯一變因是門檻 6.0→8.0。模型沒變好，是標準降低了。」
——那是我自己當初填的 run 描述。

⭐ **這一頁最被低估的功能是「描述」欄。**
它是自由文字，而且**會跟著這次執行永久留著**。
如果你不寫，三個月後你看到的就只是 `run3`、`run4`，
完全不知道那次改了什麼。

> **實用建議**：每次建 run 的時候，在描述裡寫「**這次唯一的變因是什麼**」。
> 這一個習慣讓上面那張圖從「五次執行紀錄」變成「一份實驗日誌」。

點進一次 run 看細節：

![run4 詳情](/assets/img/rhoai/16-run4-succeeded.png)

上面有四個分頁：**Graph／Details／Input parameters／Pipeline spec**。

- **Graph**：四棒的執行圖，每格的勾或叉。點任一格可以看那一步的 log
- **Input parameters** ← **驗收時最該看的一頁**。它列出這次執行實際用的參數值。
  上面那個「門檻被改成 8.0」就是在這裡看到的
- **Pipeline spec**：這次跑的是哪一版定義

---

## 第五站：Artifacts（產出物與 lineage）

![Artifacts](/assets/img/rhoai/14-odh-artifacts.png)

每一步產出的東西都會登記在這裡：資料集、模型檔、評估報告。

**這一頁的價值在於它把「產出物」和「哪一次 run」綁起來**——
也就是 lineage 的一半。

⚠️ 另一半（**用了哪一版程式碼**）不會自己出現。
容器裡沒有 git 可以問，你得在編 pipeline 時把 image digest 帶進去。

---

## 第六站：Deployments（模型服務）

![Model serving](/assets/img/rhoai/12-odh-model-serving.png)

上線的模型在這裡。這一頁背後就是 `InferenceService`。

⚠️ 但**這一頁只告訴你「部署狀態」**。它不會告訴你：

- 這顆模型是哪一次 run 產的
- 它的評估數字是多少
- 誰核准它上線的

**那三個問題要去問服務本身**（我的服務有一個 `/model` 端點回答這些），
或者去看台帳。dashboard 這一頁不管治理。

---

## 每一頁真正要看的欄位（速查）

| 頁面 | 大家在看 | ⭐ 真正該看的 |
|---|---|---|
| Projects | 有幾個 project | 有沒有 project **不在列表上**（沒貼 label） |
| Connections | 有沒有設 | **Connected resources**（誰在用） |
| Pipelines | 有幾個定義 | 版本，以及**上次改是什麼時候** |
| **Runs** | 綠的還紅的 | **Input parameters**：這次的參數是什麼 |
| Artifacts | 有東西 | 產出物**連得回哪一次 run** |
| Deployments | READY | **模型的身分**（要另外問服務） |

**右邊那一欄，沒有一個是 dashboard 會主動提醒你的。**

---

## 給驗收的用法

如果你是去驗收別人交付的平台，這條路線可以當腳本走：

1. **Projects** —— 「請建一個新 project」（驗權限與 quota）
2. **Connections** —— 「請設一份 S3 connection」（驗憑證管理）
3. **Workbenches** —— 「請建一個 workbench 並開啟」（驗 image、儲存、排程）
4. **Pipelines** —— 「請上傳並跑一次」（驗 DSPA、S3、執行環境）
5. **Runs** —— 「請給我看 Input parameters」（驗參數是否可追）
6. **Deployments** —— 「請部署並打一發推論」（驗端到端）

**六步，每一步都要當著你的面做，不接受截圖。**
理由我在前面幾篇寫過：這個平台上有太多東西是「顯示正常但實際壞掉」的。

---

**你們的人比較常用 dashboard 還是 `oc`？如果是 dashboard，卡在哪一頁最多？**

{% include lab-env.html %}
