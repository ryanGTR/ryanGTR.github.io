---
layout: post
title: "私有 registry：為什麼 image 跟模型要分開放"
date: 2026-09-22 09:00:00 +0800
tags: [harbor, registry, openshift-ai, supply-chain, offline, tutorial]
excerpt: "同一份東西，image 進 registry、模型進 S3。這個分法不是潔癖，是因為兩者的生命週期完全不同——以及它怎麼決定你的放行流程長什麼樣。"
feedback_question: "你們的 image 放哪？有沒有「待審」和「已放行」分開的機制？"
---

## 1. 這是什麼

**一個放容器 image 的地方，而且是你自己的。**

我 lab 用 Harbor（開源、可自架）。企業常見的還有 JFrog Artifactory、
Nexus、或雲端的 ECR/ACR/GAR。

![Harbor projects](/assets/img/rhoai/harbor-projects.png)

> **Java 類比**：Nexus 對 jar 的角色。差別在 image 更大、更難掃，
> 而且它是**執行環境**不只是相依——jar 有問題你重打包，
> image 有問題可能是底層 OS 的 CVE。

---

## 2. 什麼時機需要它

**第一個時機**：你的叢集連不到外網。這時候不是「要不要」，是「沒有就裝不起來」。

**第二個時機**（更常見）：你需要回答這三題其中一題——

- 「線上跑的這顆 image 是誰建的、什麼時候？」
- 「它掃過 CVE 了嗎？」
- 「誰核准它進正式環境的？」

公開 registry 一題都答不了。

---

## 3. ⭐ 為什麼 image 跟模型不能放同一個地方

這是這篇的重點，而且它會決定你後面整個流程的形狀。

| | 容器 image | 模型權重 |
|---|---|---|
| 放哪 | **registry**（Harbor） | **S3**（MinIO） |
| 多久換一次 | 幾週～幾個月 | **可能一天三次** |
| 誰產的 | CI/CD | 訓練 pipeline |
| 要不要簽章 | **要** | 看你 |
| 要不要掃 CVE | **要** | 掃不了（那不是程式） |
| 改一次的成本 | 重 build + 重掃 + 重簽 | 上傳一個檔案 |

**綁在一起會怎樣**：每次換模型，你要重跑一次完整的 image 建置、
CVE 掃描、簽章、以及所有相關的簽核。**換一個權重檔要走一次發版流程。**

分開之後，**同一個 image 可以服務不同的模型**，
靠環境變數（`STORAGE_URI`）指到不同的 S3 路徑。

> 這就是 KServe 的 `storage-initializer` 存在的理由——
> 它讓 image 保持不可變，而模型可以換。

---

## 4. 怎麼用：兩個 project 撐起一個放行流程

我的 Harbor 有這幾個 project：

```
tools       ← 平台自己要用的 image（serving runtime、buildah…）
odh         ← 鏡進來的上游 image
demo        ← 已放行
demo-tmp    ← 待審
```

⭐ **`demo-tmp` 和 `demo` 分開，是整個放行機制的關鍵。**

流程是這樣：

```
build 完 → 推到 demo-tmp（待審）
           ↓  掃描、簽章、人工檢查
        管理員手動觸發 replication
           ↓
         demo（已放行）→ 正式環境只認這裡
```

**正式環境的叢集只被允許從 `demo` 拉。** 所以「放行」這個動作
在技術上就是「**把 image 從 demo-tmp 複製到 demo**」。

### 為什麼這比「加一個 tag」好

常見的做法是用 tag 表示狀態：`myapp:staging` → `myapp:prod`。

**問題是 tag 可以被覆寫。** 今天的 `prod` 和上週的 `prod` 可能是不同的東西，
而且沒有紀錄。

用兩個 project 的話：

- 進到 `demo` 的東西，**digest 不變**——它就是被審過的那一顆
- replication 的動作**有紀錄**，誰按的、什麼時候
- 而且權限可以分開：**開發者能推 `demo-tmp`，但不能推 `demo`**

> 我實測過這件事：從 `demo-tmp` replicate 到 `demo` 之後，
> **digest 完全相同，一顆 image 三個 tag**。
> 那證明放行沒有改變成品本身——**這正是稽核要的**。

---

## 5. 一律用 digest，不要用 tag

```bash
# ✗ tag 會漂移
image: myregistry/tools/llm-serve:cpu

# ✓ digest 是內容的雜湊，改了就是不同一顆
image: myregistry/tools/llm-serve@sha256:4ed07b551253fef4f01...
```

我 lab 那顆 serving image：

```
tools/llm-serve   digest=sha256:4ed07b551253fef4f01…   tags=['cpu']   314MB
```

**`cpu` 這個 tag 明天可能指向別的東西，那個 digest 不會。**

⚠️ 而且前面提過：**IDMS（離線鏡像的來源改寫）是用 digest 比對的**。
你的 pod spec 如果寫 tag，IDMS 不會生效——那是一個典型的「設了但沒作用」。

---

## 6. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| 私有 registry | image 推得上去、拉得下來 | **拿線上跑的 digest，反查得到它是誰建的、掃過沒、誰放行的** |

第二欄是唯一有意義的驗收：**給我一個 digest，你能不能回答那三個問題。**

如果答案是「要去問某某」，那你有的是一個檔案伺服器，不是 registry。

---

## 7. 什麼時候不需要它

- 完全用公開 image、而且不需要回答上面那三題
- 雲端環境、直接用託管的 registry（那也是私有 registry，只是別人管）

**但如果你在企業內網、或有稽核要求，這一項沒有選擇。**

---

**你們的 image 放哪？有沒有「待審」和「已放行」分開的機制？**

{% include lab-env.html %}
