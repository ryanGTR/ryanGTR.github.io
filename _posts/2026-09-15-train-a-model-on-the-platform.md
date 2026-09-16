---
layout: post
title: "Day 15：在平台上真的訓練一顆模型"
series: "OpenShift AI 入門 30 天"
date: 2026-09-15 09:00:00 +0800
tags: [openshift-ai, odh, kubeflow, pipelines, kfp, training]
excerpt: "把一顆從零寫的小 GPT 丟上 OpenShift AI 訓練：五棒都在容器裡跑、產物落 S3、放行由閘門判定。含每一棒的實測秒數，以及這台平台訓不動的地方。"
feedback_question: "你們的訓練現在跑在哪裡——某個人的筆電、一台共用的 GPU 機器，還是已經在叢集上了？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇的 run 與重跑都是在自動升級後的 `3.6.0-ea.1` 上做的**（`kfp` 2.17.0；升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

平台裝起來了，不代表訓練跑得上去。中間隔著資源額度、映像檔、憑證，
還有一個跑完才會被問的問題：**這顆模型是哪份資料、哪組參數、在哪台機器訓出來的。**

這篇把一顆從零寫的小 GPT（1.38 M 參數）丟到 OpenShift AI 上，
用 Data Science Pipelines 串成五棒——**資料準備 → 訓練 → 評估 → 放行閘門 → 上線**。
每一棒是一個容器，產物落 S3，最後放不放行由閘門判定，不是人說了算。

目標不是訓練出好模型，是量出這台平台**跑得動什麼、跑不動什麼**，而且用秒數說。

---

## 什麼時候你會用到

- 訓練現在跑在某個人的筆電上，而你要把它搬到留得下紀錄的地方
- 有人會問「這顆模型怎麼來的」，你想有答案而不是靠回憶
- 同一個流程要重跑第二次以上（含「換個參數再跑一次」）

**什麼時候不需要**：還在探索期、每跑一次都要看中間結果決定下一步——workbench 就好（[Day 8]({{ '/2026/09/workbench-your-notebook-on-the-cluster/' | relative_url }})）。
另外，**如果你的訓練需要 GPU 而 lab 沒有卡，不要拿 lab 的秒數去估正式環境**，
最後一段就是在講這件事。

---

## 前置條件

- DSPA 起來、S3 接好（[Day 9]({{ '/2026/09/your-first-data-science-pipeline/' | relative_url }})、[Day 7]({{ '/2026/09/connect-your-storage/' | relative_url }})）
- 一個**私有 registry 裡的訓練 image**：程式碼與 PyTorch 都烤在裡面
- S3 憑證放 Secret，用 `use_secret_as_env` 注入，不要烤進 image
- 一點空著的 CPU 額度（步驟三會解釋為什麼特別提這個）
- 本機 `pip install kfp kfp-kubernetes`
  ——⚠️ **`kubernetes` 是另一個套件**，只裝 `kfp` 的話下面第一行就 `ImportError`

為什麼要自己 build image，而不用 `@dsl.component`：
lightweight component 執行時會 `pip install kfp`，**離線環境裝不了**。
`@dsl.container_component` 直接執行 image 內的程式，離線可行。

---

## 步驟一：把五棒接起來

```python
from kfp import dsl, compiler, kubernetes

IMAGE = "harbor.example/tools/llm-train@sha256:2ce7d45f..."   # 釘 digest，不釘 tag

def _spec(script, *args):
    return dsl.ContainerSpec(image=IMAGE, command=["bash", "-c", script, "bash"],
                             args=list(args))

@dsl.container_component
def train_model(run_id: str, max_iters: str):
    return _spec(TRAIN, run_id, max_iters)     # TRAIN 是一段 shell，見下

@dsl.pipeline(name="llm-lifecycle")
def llm_lifecycle(run_id: str = "run1", sample_mb: str = "2",
                  max_iters: str = "300", max_val_loss: str = "6.0"):
    p = prepare_data(run_id=run_id, sample_mb=sample_mb)
    t = train_model(run_id=run_id, max_iters=max_iters).after(p)
    e = evaluate_model(run_id=run_id).after(t)
    g = promotion_gate(run_id=run_id, max_val_loss=max_val_loss).after(e)
    pr = promote(run_id=run_id).after(g)       # 閘門擋下就不會執行到這裡
    for task in (p, t, e, g, pr):
        task.set_caching_options(False)
        kubernetes.use_secret_as_env(task, secret_name="minio-s3",
            secret_key_to_env={"AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
                               "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY"})
    t.set_cpu_request("1").set_cpu_limit("3") \
     .set_memory_request("2Gi").set_memory_limit("6Gi")
```

三個要點：

1. **image 釘 digest 不釘 tag。** tag 可以被覆蓋，覆蓋之後這次 run 就再也答不出
   「我跑的是哪一版程式碼」。容器裡沒有 `.git`，程式碼的身份只剩 image digest。
2. **大檔走 S3，步驟之間只傳 `run_id`。** 幾 MB 的權重不要塞進 KFP 的 artifact metadata，
   順序用 `.after()` 表達就好。
3. **訓練那一棒單獨給資源。** 其他四棒都很輕，只有它會吃滿。

---

## 步驟二：送一次 run

```python
from kfp.client import Client
c = Client(host="https://ds-pipeline-dspa-<ns>.apps-crc.testing",
           existing_token=<token>, verify_ssl=False)
c.create_run_from_pipeline_package("pipeline.yaml",
    arguments={"run_id": "day04-train", "sample_mb": "2",
               "max_iters": "300", "max_val_loss": "6.0"},
    experiment_name="llm-lifecycle-demo", enable_caching=False)
```

**驗證這一步：**

```bash
oc get workflow -n <ns>          # Argo 的執行實體，UI 上的 run 對應這個物件
oc get pods -n <ns> -w
```

---

## 步驟三：先看它排不排得進去

我這次的訓練那棒**卡在 Pending 一分半**，什麼都沒發生：

```
$ oc describe pod llm-lifecycle-...-impl-321104115 -n llm-serve-demo
Warning  FailedScheduling  73s  default-scheduler
  0/1 nodes are available: 1 Insufficient cpu.
```

```
$ oc describe node crc | grep -A4 "Allocated resources"
  Resource   Requests        Limits
  cpu        12361m (96%)    32060m (250%)
  memory     33250Mi (83%)   141506Mi (356%)
```

96% 的 CPU **request** 已經被佔走了。誰佔的：

| 佔用 | 元件 |
|---|---|
| 650m × 2 | odh-dashboard |
| 600m | 一個模型服務（llm-scratch） |
| 500m × 2 | notebook controller |
| 500m | 一個 workbench |
| 400m | 另一個模型服務 |
| 350m | ds-pipeline-dspa |
| 300m | 另一個 workbench |

**CPU 是被 request 佔掉的，不是被用掉的。**
上面這些東西當時全部閒著，額度照佔——排程器只看 request。

lab 上的解法是把用不到的先收掉：

```bash
oc scale statefulset my-workbench ryan-workbench -n <ns> --replicas=0
```

🔴 **關掉前先看 PVC 掛在哪。** [Day 8]({{ '/2026/09/workbench-your-notebook-on-the-cluster/' | relative_url }}) 量過：
我這兩個 workbench，一個 PVC 掛在 `/opt/app-root/src`，另一個掛在 `/opt/app-root/src/work`
——**後者的家目錄是 overlay，存在那裡的檔案會跟著 pod 消失**，
而 JupyterLab 一開就停在家目錄。先 `df -h /opt/app-root/src` 確認有沒有 mount。

釋出 800m 之後，那一棒**立刻**排進去了。
正式環境不會這樣處理（會用配額、佇列或另開節點），但**你要先知道自己撞的是 request，不是實際用量**——
看 `top` 會看到 CPU 很閒，然後想不通為什麼排不進去。

---

## 步驟四：每一棒實際花多久

同一條 pipeline，2 MB 語料、300 iters，全程 CPU：

| 節點 | 秒 |
|---|---|
| root-driver | 4 |
| prepare-data-driver | 4 |
| **prepare（執行）** | **72** |
| train-model-driver | 5 |
| **train（執行）** | **1208**（含約 90 秒排不進去的等待） |
| evaluate-model-driver | 3 |
| **evaluate（執行）** | **185** |
| promotion-gate-driver | 3 |
| gate（執行） | 7 |
| **總計** | **1554 秒（25 分 54 秒）** |

這條 pipeline 定義了五棒，但閘門擋下，**第五棒沒被執行**，所以只看得到四棒的時間。
四棒起了 9 個 pod：每棒一個 driver + 一個 executor，外加 root。
**這次的固定成本只有 4 秒**——但同一條 pipeline 上一次跑，光 root-driver 就 157 秒，
差別在 image 有沒有被 cache 過。第一次跑很慢是正常的，別把它算進基準。

產物（`s3://pipelines/runs/day04-train/`）：

```
ckpt.pt 5.5 MiB / train.bin 438 KiB / val.bin 54.8 KiB / test.bin 54.8 KiB
tokenizer.json 36.2 KiB / meta.json 232 B
data_report.json 605 B / data_quality_report.json 1.5 KiB / eval_report.json 143 B
```

評估結果：`val_loss 7.2233`／`test_loss 7.1313`／`perplexity 1250.47`／`params_M 1.378`。

---

## 步驟五：閘門判什麼

```
=== promotion gate ===
  候選  18a094fa2d54  val_loss= 7.2233  test_loss= 7.1313
  基準  6957bbaf0491  test_loss= 8.393
  code_image  harbor.example/tools/llm-train@sha256:2ce7d45f...
  data_quality_gate  False
  ✗ 擋下： val_loss 7.2233 超過門檻 6.0
  ✗ 擋下： 資料品質 gate 未通過
```

兩個理由，缺一個都不夠：**loss 沒到門檻**，而且**資料品質報表本身沒過**。
閘門 `exit 1`，整條 workflow 因此顯示 **Failed**，上線那一棒沒有被執行。

這是設計行為，不是壞掉。**要讓「不合格」長得像失敗**，
不然它就會長得像成功，然後被當成成功。

---

## 🔴 隔 11 天，我把同一條 pipeline 原封不動再跑一次

參數一樣、image 的 digest 一樣。結果：

**訓練是可重現的。** 兩次的 `ckpt.pt`、`train.bin`、`tokenizer.json`
**sha256 完全相同**——同一份資料、同一顆模型，一個位元組都沒差。

**但評估不是：**

| | 第一次 | 第二次 |
|---|---|---|
| val_loss | 7.2233 | **7.2279** |
| test_loss | 7.1313 | **7.1130** |
| perplexity | 1250.47 | **1227.88** |

同一顆模型（digest 都是 `18a094fa2d54`），量出來的數字不一樣。
根因在評估程式裡：它**隨機抽 200 個 batch 算平均，而且沒有設種子**。

```python
ix = torch.randint(len(data) - block_size, (batch_size,))   # 每次都不同
```

**閘門拿去跟門檻 `6.0` 比的，就是這個帶雜訊的數字。**
現在離門檻遠所以看不出來；哪天模型落在 6.0 附近，**同一顆模型會一次過、一次不過**。

兩條路：評估固定種子，或先量出這個數字的波動範圍再決定門檻留多少餘裕。
**不量就設門檻，等於拿雜訊當判準。**

順帶，總時間 1554 → **1707 秒**：這次先清出了 CPU、完全沒排隊，反而慢 10%。
**秒數本身也在浮動**，拿它估算要留餘裕。


---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 五棒都建出 pod | `oc get pods \| grep <workflow 名>` |
| 2 | 訓練那棒真的排進去了 | pod 狀態從 Pending 變 Running，不是一直 Pending |
| 3 | **產物真的進 S3** | 去 bucket 底下看檔案在不在、大小合不合理 |
| 4 | **ckpt 的時間戳是這次的** | 不是上一次留下來的舊檔 |
| 5 | 閘門有輸出判定理由 | log 裡看得到「候選／基準／擋下的原因」 |
| 6 | 不合格時 workflow 是紅的 | 綠色代表放行，不能兩種情況都綠 |

第 3、4 項不能用第 1 項代替。UI 綠燈只代表容器 exit code 是 0，
一個什麼都沒做就結束的步驟也是綠的。

---

## 常見問題

**Q：我設了 300 步，為什麼評估報表寫 `train_iter: 200`？**
A：`eval_interval` 預設 200，所以整趟只在第 0 步和第 200 步算過 val loss，
而存檔的規則是「best val」。**第 300 步從來沒有被評估過，也就永遠不會被存下來。**
調大 `max_iters` 卻沒調 `eval_interval`，後面那 100 步就是白跑的。

**Q：為什麼同一份語料，準備那棒說「讀入 1 篇文件」，品質報表說「對 88 篇跑檢測」？**
A：兩支程式對「一篇文件」的定義不同——一支把整個檔案當一篇，
另一支用連續三個換行去切。切完的 88 篇裡，**七項檢測有三項沒過**：

```
symbol_spam      21 篇  23.86%  門檻 1.0%  ✗
high_repetition  21 篇  23.86%  門檻 1.0%  ✗
too_short        26 篇  29.55%  門檻 5.0%  ✗
```

這條規則有在擋，但它擋的東西**對切法很敏感**。同一份 2 MB 語料，只換分隔符：

| `--doc_sep` | 文件數 | symbol_spam | high_repetition | too_short |
|---|---|---|---|---|
| 三個換行 | 88 | 23.86% | 23.86% | 29.55% |
| 兩個換行 | 5,523 | **1.30%** | 10.65% | 34.71% |
| 一個換行 | 15,628 | **36.66%** | 44.59% | 75.37% |

同一份資料，同一條規則，`symbol_spam` 的比例差了 28 倍。
門檻設在 1% 或 5% 之前，要先確定分母是什麼——**否則你調的是切法，不是品質。**

**Q：這台機器能不能真的拿來訓練？**
A：把數字攤開比較清楚：

| | 這次在平台上 | 專案原本那顆模型 |
|---|---|---|
| 語料 | 2 MB（取樣） | 104 MB |
| 迭代 | 300（實際存下第 200 步） | 2000（專案預設） |
| 裝置 | CPU | 筆電上的 NVIDIA GPU |
| 權重檔 | 5.5 MiB | 33 MB |
| 訓練耗時 | 20 分 8 秒 | — |

CPU 上每步 **3.7–4.3 秒**（兩次實測的區間）。跑滿 2000 步約 2–2.4 小時——
但語料差 50 倍、模型也大一號，**所以這是下限，不是估計值**。

兩邊的 loss 不要直接比：tokenizer 不同，vocabulary 不同，loss 的單位就不同。

結論是這台平台現在的角色是**跑流程**，不是**跑訓練**：
流程的每一站都能在上面走完並留下證據，訓練本身還在筆電的 GPU 上。
這個界線要自己量過才知道在哪裡，而量它只需要一次 25 分鐘的 run。

**Q：閘門放行之後，模型怎麼真的換上去？**
A：這條 pipeline 的第五棒就是在做那件事，但**平台不會自動幫你接**——
那一棒是自己寫的。這是最容易被跳過的缺口，寫法與三個坑在 [Day 28]({{ '/2026/09/gates-before-production/' | relative_url }})。

---

**你們的訓練現在跑在哪裡——某個人的筆電、一台共用的 GPU 機器，還是已經在叢集上了？**

{% include lab-env.html %}
