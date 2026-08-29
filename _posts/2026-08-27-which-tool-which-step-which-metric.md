---
layout: post
title: "哪一步、用什麼工具、看什麼指標"
series: "OpenShift AI 實戰紀錄"
date: 2026-08-27 14:00:00 +0800
tags: [openshift-ai, rhoai, mlops, metrics, acceptance, tutorial]
excerpt: "七個流程步驟，各對應哪個工具，以及那一步的關鍵指標——而指標分成「跑完了」和「做對了」兩欄，因為它們是完全不同的兩件事。附官方教學用幾個工具的對照。"
feedback_question: "你們驗收一個 MLOps 平台時，最右邊那欄（做對了）問得出幾條？"
---

[上一篇](/2026/08/mlops-tool-map/)講了 OpenShift AI 上這些角色各是誰。
這篇是實際要用的那張表：**流程的每一步，對應哪個工具，以及那一步該看什麼。**

## 指標要分成兩欄

因為它們是完全不同的兩件事：

- **「跑完了」**：狀態變綠了。這個平台會主動告訴你
- **「做對了」**：這一步真的達成目的了。**這個要你自己去問**

| # | 流程這一步 | 用什麼工具 | 「跑完了」的訊號 | ⭐「做對了」的指標 |
|---|---|---|---|---|
| 1 | **資料準備** | pipeline `prepare` 棒、S3 | task 綠了 | 品質過濾丟掉幾筆、去重丟掉幾筆、vocab 多大；**這份資料有沒有身分證**（來源／日期／sha256／處理腳本版本） |
| 2 | **訓練** | pipeline `train` 棒、GPU | task 綠了、有 checkpoint | `best_iter` vs `max_iters`（**相等代表可能還沒收斂，遠小於代表步數過頭**）；**訓練是不是真的用到 GPU** |
| 3 | **評估** | pipeline `evaluate` 棒 | 有 `eval_report.json` | 有沒有**獨立的 test split**（不是拿 val 當成績）；換過 tokenizer 的話要用 **BPC** 不是 raw loss |
| 4 | **放行** | promotion gate、台帳 | run `SUCCEEDED` | **門檻是誰定的、有沒有依據**；gate **紅過幾次**、擋下過什麼 |
| 5 | **上線** | KServe ISvc、S3、Route | `ISvc READY=True` | 端點**實際回得出東西**；`/model` **答得出自己是誰**（digest + 狀態） |
| 6 | **監控** | Prometheus + Grafana | pod Running、target `up` | **面板真的有資料**；drift 指標**有沒有最小樣本數保護** |
| 7 | **換版／回滾** | 台帳、image digest | 新版上線了 | 能不能用 digest **反查**是哪一次 run 產的；**回滾一次要多久** |

### 每一列的「做對了」為什麼是那一個

**1. 資料**：`prepare` 綠了只代表檔案生出來了。真正的問題是
「這份資料哪來的、什麼時候抓的、被誰處理過」——**模型會把資料的一切學進去，
包括你不知道自己收了什麼**。沒有 dataset card，出事時你查不到源頭。

**2. 訓練**：`best_iter` 這個數字很好用。我的一組實驗裡，設定 300 步而
`best_iter` 全部停在 200——**代表 300 步已經過頭，加訓練長度不會改善，瓶頸在資料量**。
至於 GPU，pod 要到卡不等於模型算在卡上，那件事要另外驗。

**3. 評估**：最常見的錯是拿 val 當最終成績——但 val 是你調參時看過的，
它已經被汙染了。另一個是換 tokenizer 之後直接比 loss，
那兩個數字**單位不同，不能比大小**。

**4. 放行**：這一列最關鍵，而且最容易造假。**「gate 從來沒紅過」不是好消息，
是你根本沒在檢查。** 我遇過三顆能力幾乎相同的模型，一顆上線兩顆被擋——
決定的不是模型，是門檻參數從 6.0 被改成 8.0。

**5. 上線**：`READY=True` 只代表 pod 起來了。我有一顆模型 `READY=True`、
推論打得通，但問它 `/model` 回 **`UNREGISTERED`**——
**一顆答不出自己憑什麼上線的模型，照樣在對外服務。**

**6. 監控**：pod Running、target up，而**九個面板全是空的**——
因為 datasource 少填一個欄位。檢查全在資料層，壞在呈現層。

**7. 回滾**：這一列平常不會有人看，但它是「你有沒有真的在治理」的唯一硬指標。
**「換回上一版要多久」這個數字，比任何架構圖都誠實。**

### 用法

把左邊四欄當作**導入的順序**，把最右邊那欄當作**驗收的提問稿**。

對方交付一站，你就問那一站最右邊的問題。
如果對方只答得出中間那欄（「跑完了、綠了」），**那一站就還沒完成**。

---

## 對照：官方教學用幾個，實際上線要幾個

紅帽有一份標準的端到端教學——[Fraud detection example](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_cloud_service/1/html/openshift_ai_tutorial_-_fraud_detection_example/implementing-pipelines)，
七章走完一個完整流程。那是我看過最好的入門材料，**建議你先跑一次**。

它用到的工具：

| 官方教學 | 章節 |
|---|---|
| Workbench（JupyterLab） | ch3 |
| Cluster storage（PVC） | ch2 |
| Data connection（S3） | ch2 |
| Model server | ch4 |
| Pipelines | ch5 |
| Ray / Kueue（分散式訓練） | ch6 |

**五到六個。** 而我的 lab 用到十個左右。差的是這些：

| 我有而官方教學沒有 | 為什麼教學不寫 |
|---|---|
| **私有 registry** | 教學直接用公開 image——真實企業（尤其離線環境）不行 |
| **監控**（Prometheus / Grafana） | 整份教學沒有這一塊 |
| **台帳 / promotion gate / lineage** | 教學到「模型部署成功」就結束了 |
| **離線／鏡像清單** | 教學假設連得到外網 |

**這不是官方教學的缺點，是它的範圍。** 它的目的是**教你怎麼用這個產品**，
而不是**教你怎麼經營一個上線的模型服務**。

⭐ 但有一件事值得注意：

> **官方教學在 ch4「部署測試模型」就算成功了。**
> 而那正好是前面那張表裡「上線」那一列的**「跑完了」那一欄**。

換句話說：**跑完官方教學，你會走到我這個系列的起點。**
後面那些——它憑什麼上線、它現在健不健康、壞了怎麼換回去——
是產品文件結構上不會回答的問題，因為那不是產品的功能，是你的流程。

> ⚠️ **查證限度**：`docs.redhat.com` 擋自動抓取，
> 上面的章節結構我是從公開的章節標題整理的，不是逐頁讀完的。
> 章節名稱與數量會隨版本變動，**以你手上那一版為準**。

---

## 你不需要全部

第一次做的話，**最小組合是四個**：

```
OpenShift + ODH operator（開 kserve）
  ↓
S3（模型放哪）
  ↓
InferenceService（模型變服務）
  ↓
一個 Route（讓外面打得到）
```

這四個就能讓模型上線。其餘的是為了回答不同的問題：

| 你開始在意 | 才需要加 |
|---|---|
| 「這模型怎麼來的？」 | Pipelines（DSPA） |
| 「它現在健康嗎？」 | Prometheus + Grafana |
| 「這顆憑什麼上線？」 | 台帳 + promotion gate |
| 「image 從哪來、掃過沒？」 | 私有 registry（Harbor） |

**不要一開始就全開。** 在 `DataScienceCluster` 裡每開一個元件，
離線鏡像清單就長一截，相依也可能多一個 operator——
而你可能根本不會用到它。

我的 lab 只開了四個：`kserve`、`aipipelines`、`dashboard`、`workbenches`。
其餘（`ray`、`kueue`、`trustyai`、`feast`、`modelregistry`、`aigateway`…）
全部是 `Removed`。

---

## 一句話總結

**每一個角色都對應一個「出事時你會被問到的問題」。**

如果你現在答不出某個問題，那個位置就是你缺的角色；
如果你裝了某個東西但想不出它回答什麼問題，那它大概可以先關掉。

---

**你們驗收一個 MLOps 平台時，最右邊那欄——「做對了」——問得出幾條？**

{% include lab-env.html %}
