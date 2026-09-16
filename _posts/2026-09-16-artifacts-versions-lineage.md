---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 16：產物、版本與血緣——這個模型是哪來的"
date: 2026-09-16 09:00:00 +0800
tags: [openshift-ai, odh, mlmd, lineage, artifacts, governance, tutorial, ironman2026]
excerpt: "「這個模型是哪份資料訓出來的」——這題被問到的時候通常已經來不及了。這篇講平台自動記了什麼、沒記什麼、以及你要自己補哪一段。"
feedback_question: "如果現在有人問你們線上模型的訓練資料是哪一份，你要花多久才查得到？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上實跑的**（`kfp` 2.17.0；升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

**血緣（lineage）＝ 這個東西是從哪些東西產生的。**

聽起來抽象，但它會以很具體的形式找上你：

- 資料被發現有問題，**哪些模型受影響？**
- 模型行為變了，**是換了資料還是換了參數？**
- 稽核問：**這個上線的模型，訓練資料的來源與授權是什麼？**

這三題都沒辦法事後補。**要嘛當時記了，要嘛就是查不到。**

---

## 什麼時候你會用到

- 受監理的行業（金融、醫療）——**這是必答題不是加分題**
- 模型出事要做事故調查
- 多人協作，模型不是你訓的
- 要重現三個月前的結果

---

## 步驟一：平台自動記了什麼

用 KFP v2 的 `Input`／`Output` 型別時，**血緣是自動的**：

```python
@dsl.component
def train(data: dsl.Input[dsl.Dataset], model: dsl.Output[dsl.Model]):
    ...
```

背後有一個叫 **MLMD**（ML Metadata）的資料庫在記。
DSPA 起來時就有：

```bash
oc get pods -n <ns> | grep metadata
# ds-pipeline-metadata-envoy-dspa-...   2/2   Running
# ds-pipeline-metadata-grpc-dspa-...    1/1   Running
```

它記的是：**哪個 run、跑了哪些 step、每個 step 吃了哪些 artifact、
產了哪些 artifact、放在 S3 的哪裡。**

在 dashboard 的 **Develop & train → Pipelines → Artifacts** 看得到，點一個 artifact 可以往回追。

⚠️ 前提是真的用了 `Input`／`Output`。步驟間只傳字串、檔案自己搬去 S3 的話，
MLMD 裡**只有 log，沒有資料血緣**——我的 lab 就是這樣。

---

## 步驟二：⭐ 平台沒記什麼

**這一節比上一節重要。**

| 你以為記了 | 實際上 |
|---|---|
| 訓練資料的**內容** | ❌ 只記了路徑。**檔案被覆蓋，紀錄不會知道** |
| 程式碼版本 | ❌ 除非你自己把 git commit 傳進去 |
| 超參數 | ⚠️ pipeline 參數有記，**component 裡寫死的沒有** |
| 環境（套件版本） | ❌ 完全沒有 |
| **誰按的執行** | ❌ 資料庫只記 ServiceAccount（`pipeline-runner-dspa`），**沒有使用者欄位** |

**第一列是最危險的。** 我實際試了一次：跑完 pipeline，直接把 S3 上那份資料覆蓋掉。

| | S3 上的檔案 | MLMD 裡那筆 artifact |
|---|---|---|
| 覆蓋前 | sha256 `46836171…`，修改時間 `03:07:41` | 路徑不變，最後更新 `03:07:15` |
| 覆蓋後 | sha256 **`50693468…`**，修改時間 **`03:09:51`** | 路徑不變，最後更新 **`03:07:15`** |

**MLMD 一個欄位都沒動。你的血緣鏈在這裡有一個洞。**

---

## 步驟三：把洞補起來

### 補法一：用內容算身分，不用路徑

**把資料和模型的 sha256 記進 metadata：**

```python
@dsl.component
def train(data: dsl.Input[dsl.Dataset], model: dsl.Output[dsl.Model]):
    import hashlib

    def digest(path):          # ← 一定要寫在元件函式「裡面」
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ... 訓練 ...
    model.metadata["data_sha256"] = digest(data.path)
    model.metadata["model_sha256"] = digest(model.path)
```

🔴 **`digest` 不能定義在元件外面。** lightweight component 只會把函式本體打包進容器，
外面的東西不會跟著走。編譯不會擋，**要到叢集上才炸**：

```
    data_digest = digest(data.path)
                  ^^^^^^
NameError: name 'digest' is not defined
```

**這一步把「路徑相同」升級成「內容相同」。**
上面那次覆蓋，就是靠 `data_sha256` 對不上才抓出來的。
這是 container image 用 digest 而不是 tag 的同一個道理。

### 補法二：把環境釘住

```python
@dsl.component(base_image="registry.example/ml-base@sha256:abc...")
def train(...):
```

**用 digest 不用 tag。** `packages_to_install=["pandas"]` 會編成 `pip install 'pandas'`——
沒寫版本就是每次裝當下最新的，
**這表示同一份程式碼在不同時間跑會得到不同結果**，
而血緣紀錄看不出差別。

### 補法三：把 git commit 傳進去

```python
@dsl.component
def train(data: dsl.Input[dsl.Dataset], model: dsl.Output[dsl.Model],
          git_sha: str):
    ...
    model.metadata["git_sha"] = git_sha

@dsl.pipeline
def p(git_sha: str = "unknown"):
    train(data=..., git_sha=git_sha)
```

🔴 直覺寫法 `train(...).set_env_variable("GIT_SHA", git_sha)` **編譯就失敗**
（`TypeError: bad argument type`）——環境變數只收字面字串，收不了 pipeline 參數。
**當元件參數傳進去**才行。

觸發時帶進來，**pipeline 參數會被記進 MLMD**（實跑後資料庫裡查得到 `git_sha`）。

---

## 步驟四：串到上線那一端

血緣鏈到 artifact 就斷了——**因為 KServe 不讀 MLMD**。
InferenceService 只有一個 `storageUri: s3://...`，沒有任何欄位指回 pipeline。

最小可行的接法，部署時把身分寫進去：

```yaml
metadata:
  labels:
    model-registry/version: "v3"
  annotations:
    model/sha256: "e3b0c44298fc1c14..."
    pipeline/run-id: "a1b2c3"
```

然後這題就有答案了：

```bash
oc get isvc -A -o custom-columns=\
'NS:.metadata.namespace,NAME:.metadata.name,SHA:.metadata.annotations.model/sha256'
```

**⚠️ 這是約定，不是機制。** 沒有東西會檢查那串 sha256 是不是真的。
要它可信，得有人（或 CI）在部署時算一次、比一次。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | Artifacts 頁有東西 | dashboard → Develop & train → Pipelines → Artifacts |
| 2 | 往回追得到輸入 | 點 artifact 看它的 producer |
| 3 | **artifact 的 metadata 有 digest** | 你自己加的那幾個欄位 |
| 4 | **線上 ISvc 的 digest 對得回 artifact** | 上面那個 `oc get isvc` |
| 5 | ⭐ **實際做一次回溯演練** | 見下 |

第 5 項是唯一能證明前四項有用的檢查：

> **隨機挑一個線上的模型，計時，看你多久能講出：
> 哪份資料、哪組參數、哪個 commit、誰跑的。**

**十分鐘之內答不出來，你的血緣鏈就是有洞。**
而這個演練最好在稽核問之前自己先做。

---

## 常見問題

**Q：MLMD 的資料存在哪？會不會不見？**
A：存在 DSPA 的 MariaDB 裡，底下有 PVC——pod 重啟不會掉，
**PVC 或 namespace 被刪才會沒**，而 DSPA 沒有備份設定可開。
正式環境要接外部資料庫並納入備份。**這件事很容易被漏掉**，
因為它平常不影響任何功能。

**Q：可以不用 pipeline 只用 workbench 嗎？**
A：可以，**但那樣完全沒有自動血緣**。
notebook 跑出來的模型，除了你自己記，沒有任何紀錄。
**這是 pipeline 相對 notebook 最實在的價值**，
比「可以排程」重要得多。

**Q：sha256 算大檔案很慢吧？**
A：不慢。上面那段程式在我筆電上算 500 MB **約 0.3 秒**（pod 有 CPU 限制會慢些）。
**跟訓練時間比可以忽略**，而它換來的是「這是不是同一個檔案」的確定答案。

**Q：這些事有工具能一次做完嗎？**
A：MLflow、DVC、Weights & Biases 各自涵蓋一部分。
**但沒有一個能替你決定「要記什麼」**——
而那個決定來自你要回答什麼問題，不是來自工具。

---

**如果現在有人問你們線上模型的訓練資料是哪一份，你要花多久才查得到？**

{% include lab-env.html %}
