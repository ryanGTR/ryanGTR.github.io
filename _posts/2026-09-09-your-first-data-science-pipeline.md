---
layout: post
title: "Day 9：第一條 Data Science Pipeline"
series: "OpenShift AI 入門 30 天"
date: 2026-09-09 09:00:00 +0800
tags: [openshift-ai, odh, kubeflow, pipelines, dspa, kfp, tutorial]
excerpt: "notebook 跑得動不代表下個月還跑得動。這篇講 DSPA 是什麼、怎麼開起來、怎麼把一段 Python 變成一條會留下紀錄的 pipeline。"
feedback_question: "你們的訓練流程現在是 notebook、shell script，還是已經進 pipeline 了？"
---

## 這是什麼、解決什麼問題

notebook 裡那串 cell 你按了 30 次，這次終於出了一個好模型。

三個月後有人問「這個模型怎麼來的」，你打開 notebook，
發現 cell 被改過、順序被調過、有兩格已經刪掉了。**你答不出來。**

Pipeline 解的是這件事：**把流程寫成程式碼，每次執行留下一筆帶時間、
帶輸入、帶產物的紀錄。**

在 OpenShift AI 上，這套東西叫 **Data Science Pipelines**，
本體是 Kubeflow Pipelines v2，開關是一個叫 `DataSciencePipelinesApplication`（DSPA）的 CR。

---

## 什麼時候你會用到

- **同一個流程要重跑第二次以上**（含「換個參數再跑一次」）
- 要能回答「這個模型是哪份資料、哪組參數訓出來的」
- 訓練跑很久，你不想開著 notebook 等
- 一段流程要拆給兩個人各做一半

反過來，**還在探索期、每次都要看中間結果決定下一步** —— notebook 就好。
太早進 pipeline 只會綁死自己。

---

## 前置條件

- ODH／RHOAI 裝好，DSC 裡 `aipipelines` 是 `Managed`（⚠️ ODH 3.5 已從 `datasciencepipelines` 改名）
- **一個 S3 bucket**（pipeline 的產物要存在那裡，非選配）
- 一份 Connection（[Day 7]({{ '/2026/09/connect-your-storage/' | relative_url }}) 講過）

---

## 步驟一：開起來

Dashboard 上是 Data Science Projects → 你的專案 → **Configure pipeline server**，
填 S3 連線資訊按下去。背後產生的就是一個 DSPA CR：

```yaml
apiVersion: datasciencepipelinesapplications.opendatahub.io/v1
kind: DataSciencePipelinesApplication
metadata:
  name: dspa
  namespace: <你的專案 ns>
spec:
  dspVersion: v2
  apiServer:
    enableOauth: true
    cacheEnabled: true
  database:
    mariaDB:
      deploy: true            # ← lab 用，見下面的警告
  objectStorage:
    externalStorage:
      host: <你的 s3 host>
      port: "9000"
      bucket: pipelines
      s3CredentialsSecret:
        secretName: <你的 connection secret>
        accessKey: AWS_ACCESS_KEY_ID
        secretKey: AWS_SECRET_ACCESS_KEY
```

⚠️ **`mariaDB.deploy: true` 是 lab 設定。**
它會起一個帶 PVC 的 MariaDB pod ——**沒有備份、沒有 HA、pod 掛了紀錄就沒了**。
正式環境要指到外部資料庫（`spec.database.externalDB`）。
這是「lab 跑得起來」和「敢上線」之間的差別之一。

**驗證這一步：**

```bash
oc get pods -n <ns> | grep ds-pipeline
```

會看到七個：

```
ds-pipeline-dspa-...                      2/2   Running
ds-pipeline-metadata-envoy-dspa-...       2/2   Running
ds-pipeline-metadata-grpc-dspa-...        1/1   Running
ds-pipeline-persistenceagent-dspa-...     1/1   Running
ds-pipeline-scheduledworkflow-dspa-...    1/1   Running
ds-pipeline-workflow-controller-dspa-...  1/1   Running
mariadb-dspa-...                          1/1   Running
```

**分工大致是**：`api-server` 收請求、`metadata-grpc` 記血緣（哪個 run 產了哪個檔）、
`persistenceagent` 把 Argo 的執行狀態同步回資料庫、`scheduledworkflow` 管排程。

⚠️ **最後那個 `workflow-controller` 就是 Argo 本身**，
而且它是**跟著你的專案 namespace 一起長出來的**，不是躲在 operator 那層：

```bash
oc get deploy ds-pipeline-workflow-controller-dspa -n <ns> \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
# quay.io/opendatahub/ds-pipelines-argo-workflowcontroller:3.6.12
```

這件事的實務意義：**每開一個專案就多一份 Argo controller 在跑**，
資源要算進去；而且 pipeline 卡住時，log 在你自己的 namespace 裡，不用去別的地方翻。

知道這個分工，出問題時才知道去看哪個 pod 的 log。

---

## 步驟二：寫一條 pipeline

裝 SDK：`pip install kfp==2.*`

```python
from kfp import dsl, compiler

@dsl.component(packages_to_install=["pandas"])
def prepare(rows: int, out: dsl.Output[dsl.Dataset]):
    import pandas as pd
    pd.DataFrame({"x": range(rows)}).to_csv(out.path, index=False)

@dsl.component(packages_to_install=["pandas"])
def train(data: dsl.Input[dsl.Dataset], model: dsl.Output[dsl.Model]):
    import pandas as pd, json
    df = pd.read_csv(data.path)
    json.dump({"n": len(df)}, open(model.path, "w"))

@dsl.pipeline(name="hello-pipeline")
def p(rows: int = 100):
    d = prepare(rows=rows)
    train(data=d.outputs["out"])

compiler.Compiler().compile(p, "pipeline.yaml")
```

三個要點：

1. **每個 `@dsl.component` 是一個獨立的容器**。函式裡的 import 要寫在函式裡面，
   因為那段程式碼會被抽出來、丟到另一個 pod 執行。
2. **`Output[Dataset]` 不是普通的檔案路徑**。你寫進 `out.path`，
   KFP 會幫你上傳到 S3、記進 metadata、下一個 step 要用時再抓下來。
   **這就是「血緣」的來源** —— 你不用自己記。
3. **`packages_to_install` 每次執行都會 pip install**。離線環境跑不動，
   要改成自己 build 的 base image（`base_image=` 參數）。

---

## 步驟三：跑

Dashboard → Pipelines → Import pipeline，上傳 `pipeline.yaml`，
按 Create run。

程式上傳也可以：

```python
from kfp.client import Client
c = Client(host="https://ds-pipeline-dspa-<ns>.apps-crc.testing",
           existing_token=<你的 token>)
c.create_run_from_pipeline_package("pipeline.yaml", arguments={"rows": 500})
```

**驗證這一步：**

```bash
oc get workflows -n <ns>          # Argo 的執行實體
oc get pods -n <ns> -w            # 看容器一個一個起來
```

我把上面那段程式碼原封不動跑過一次（`kfp` 2.17.0），從編譯到跑完大約六分鐘：

```
$ oc get workflows -n llm-serve-demo
NAME                   STATUS      AGE
hello-pipeline-sk8lc   Succeeded   6m

$ oc get pods -n llm-serve-demo --sort-by=.metadata.creationTimestamp | tail -3
hello-pipeline-sk8lc-system-dag-driver-...         0/2   Completed
hello-pipeline-sk8lc-system-container-driver-...   0/2   Completed
hello-pipeline-sk8lc-system-container-impl-...     0/2   Completed
```

⚠️ 順帶一提，`@dsl.component` 現在會噴一個 `FutureWarning`：
預設 base image 之後要從 `python:3.11` 換成 `python:3.12`。
**這正好是下一節第 3 點的理由**——正式用途本來就該自己指定 `base_image=`。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 七個 pod 都 Running | `oc get pods \| grep -E 'ds-pipeline\|mariadb'` |
| 2 | UI 上 run 是綠的 | Pipelines → Runs |
| 3 | **產物真的進 S3** | 去 bucket 底下看有沒有東西 |
| 4 | **血緣查得到** | UI 上點某個 run → Artifacts 分頁，看得到輸入輸出 |

⚠️ **第 3 項不能只看第 2 項。**
UI 顯示綠色只代表**容器 exit code 是 0**。
一個什麼都沒做就結束的 step 也是綠的。

我在自己 lab 上就撞過這個：整條 pipeline 顯示 ALL DONE，
但實際上訓練那段被跳過了——**檢查點如果只看退出碼，等於沒檢查。**
要驗，就去看產物本身：檔案在不在、大小合不合理、時間戳對不對。

產物在 bucket 裡的位置是有規則的，
`<bucket>/<pipeline 名>/<run id>/<step 名>/<執行 id>/<產物名>`：

```
pipelines/hello-pipeline/b5d88be4-.../prepare/5db65d7f-.../out
pipelines/hello-pipeline/b5d88be4-.../prepare/5db65d7f-.../executor-logs-0
pipelines/hello-pipeline/b5d88be4-.../train/cb45a39c-.../model
pipelines/hello-pipeline/b5d88be4-.../train/cb45a39c-.../executor-logs-0
```

**每個 step 的 log 也一份份留在 S3 裡**（`executor-logs-0`），
pod 被回收之後還查得到——這在事後追「那次到底跑了什麼」時比 UI 好用。

---

## 常見問題

**Q：跟 Argo Workflows 是什麼關係？**
A：Argo 是引擎（真的去建 pod 的那個），KFP 是上面那層
（Python SDK、UI、血緣、快取）。
**你在 UI 上看到的 run，對應一個 `Workflow` 物件**：

```bash
oc get workflows -n <ns>
```

Pipeline 卡住而 UI 沒訊息時，直接看這個物件的 events 通常比較快。

**Q：v1 跟 v2 差在哪？**
A：v2 是現在的版本（`dspVersion: v2`），SDK 是 `kfp>=2`，
產物模型和 v1 不相容。**網路上大量的教學是 v1 的**，
`kfp.dsl.ContainerOp` 這種寫法出現就是 v1，別照抄。

**Q：改了 component 的程式碼，跑起來還是舊的。**
A：`cacheEnabled: true` 在作用。KFP 會依 component 定義算 hash，
一樣就直接沿用上次的產物。**改了函式內容 hash 就會變**，
但如果你改的是 component 外面的東西（例如讀了某個環境變數），
它不會知道。要強制重跑，在 run 的設定裡關掉 cache。

**Q：pipeline 跑完之後，模型怎麼自動上線？**
A：**平台不會幫你做這一步**，要自己在 pipeline 後面多接一棒。
這是最常見、也最容易被跳過的缺口——寫法與三個坑在
[Day 28]({{ '/2026/09/gates-before-production/' | relative_url }})。

**Q：可以排程嗎？**
A：可以，UI 上建 Recurring run，背後是 `scheduledworkflow` 那個 pod。
時區預設 **UTC**（`spec.scheduledWorkflow.cronScheduleTimezone`），
在台灣設「凌晨三點」要記得換算，不然會變成上午十一點跑。

---

**你們的訓練流程現在是 notebook、shell script，還是已經進 pipeline 了？**

{% include lab-env.html %}
