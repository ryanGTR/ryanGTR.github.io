---
layout: post
title: "在 OpenShift AI 上搭一條四棒的模型交付鏈"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-09 09:00:00 +0800
tags: [kubeflow-pipelines, openshift-ai, rhoai, dspa, mlops, tutorial]
excerpt: "prepare → train → evaluate → gate。用 Python 寫、編成 YAML、丟上平台跑。這篇是最小可用版本，以及三個當初卡住我的設定。"
feedback_question: "你們的模型訓練有沒有進 pipeline？還是還在 notebook 或某台機器上手動跑？"
---

模型能上線之後，下一個問題是：**它是怎麼來的？**

如果答案是「某個人在某台機器上跑了一個 notebook」，那你沒有交付鏈，
你有的是一個口述歷史。

這篇是在 OpenShift AI 上把它變成 pipeline 的最小可用版本。

---

## 平台這邊要有什麼

OpenShift AI 的 pipeline 引擎是 **Kubeflow Pipelines v2**，
在 DSC 裡的元件名叫 **`aipipelines`**（2.x 叫 `datasciencepipelines`，改名了）。

開了之後，每個 project 要建一個 `DataSciencePipelinesApplication`（DSPA），
它會生出一整組東西：

```bash
oc get deploy -n llm-serve-demo | grep ds-pipeline
```
```
ds-pipeline-dspa                      1/1   ← API server
ds-pipeline-metadata-envoy-dspa       1/1   ← lineage 的 proxy
ds-pipeline-metadata-grpc-dspa        1/1   ← lineage 儲存
ds-pipeline-persistenceagent-dspa     1/1   ← 把 run 狀態寫回 DB
ds-pipeline-scheduledworkflow-dspa    1/1   ← 排程
ds-pipeline-workflow-controller-dspa  1/1   ← 實際執行（Argo Workflows）
mariadb-dspa                          1/1   ← metadata DB
```

**七個 pod。** 你只是要跑四個步驟，平台幫你把「誰跑過什麼、產出了什麼、
現在跑到哪」這些事管起來——代價是這七個東西。

> ⚠️ **那個內建的 MariaDB 有一個字元集陷阱，但不是你以為的那個。**
> 我實際去查過：資料表與欄位是 `utf8mb4_unicode_ci`，**中文存得進去、
> dashboard 也顯示得正確**（我的 run 描述就是中文）。
>
> 陷阱在**連線端**：
>
> ```bash
> SHOW VARIABLES LIKE 'character_set_%';
> # character_set_client      latin1
> # character_set_connection  latin1
> # character_set_results     latin1
> ```
>
> 所以你自己寫的查詢、報表、或 `mysqldump` **沒指定 `--default-character-set=utf8mb4`
> 就會拿到 `?????`**——而且不會報錯。備份尤其危險：你會得到一份看起來成功、
> 但中文全毀的 dump。
>
> （只有 8 個欄位真的是 `latin1`，全部是 UUID 欄位——那些只存十六進位字元，
> 用 latin1 是刻意的省空間，不影響中文。）

---

## Pipeline 本身：用 Python 寫

KFP v2 的寫法是宣告容器步驟，然後宣告它們的順序：

```python
from kfp import dsl

@dsl.container_component
def prepare_data(run_id: str, sample_mb: str):
    return dsl.ContainerSpec(image=IMAGE, command=["bash", "-c", PREPARE, "bash"],
                             args=[run_id, sample_mb])

@dsl.container_component
def train_model(run_id: str, max_iters: str): ...

@dsl.container_component
def evaluate_model(run_id: str): ...

@dsl.container_component
def promotion_gate(run_id: str, max_val_loss: str): ...


@dsl.pipeline(name="llm-lifecycle")
def llm_lifecycle(run_id: str = "run1", sample_mb: str = "2",
                  max_iters: str = "300", max_val_loss: str = "6.0"):
    p = prepare_data(run_id=run_id, sample_mb=sample_mb)
    t = train_model(run_id=run_id, max_iters=max_iters).after(p)
    e = evaluate_model(run_id=run_id).after(t)
    g = promotion_gate(run_id=run_id, max_val_loss=max_val_loss).after(e)
```

`.after()` 就是 DAG 的邊。編譯：

```bash
python pipeline_llm.py        # → llm-lifecycle.yaml
```

然後在 ODH dashboard 上傳那個 YAML，或用 `kfp` SDK 推上去。

![Pipeline definitions](/assets/img/rhoai/pipeline-defs.png)

---

## 三個當初卡住我的設定

### 1. 關掉 caching，不然你會以為它跑了

```python
task.set_caching_options(False)
```

KFP 預設會 cache——輸入沒變就直接沿用上次的結果，秒回成功。

在 CI/CD 的情境這是優點。**在「我要驗證這條鏈真的會跑」的情境，
這是災難**：你會看到一條全綠的 run，實際上什麼都沒執行。

（這跟我在[另一篇](/2026/09/all-green-but-dead/)講的假綠是同一類問題。
做 lab 或驗收時，先把所有 cache 關掉。）

### 2. S3 憑證用 secret 注入，不要進 image

```python
kubernetes.use_secret_as_env(
    task, secret_name="minio-s3",
    secret_key_to_env={"AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
                       "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY"})
task.set_env_variable("S3_ENDPOINT", S3_ENDPOINT)
```

理由很直接：**image 會進 registry，密碼不該跟著走。**
image 可能被複製到別的環境、被別人拉、被掃描工具展開——
任何進了 image layer 的東西都要當成已經公開。

### 3. 資源要一步一步給，不要整條開大

```python
t.set_cpu_request("1").set_cpu_limit("3") \
 .set_memory_request("2Gi").set_memory_limit("6Gi")
```

只有 `train` 這一棒吃資源，其他三棒是複製檔案和算數字。
整條 pipeline 開大會在資源緊的叢集上直接排不進去（我的 CRC 只有 10 CPU）。

---

## 讓 lineage 有東西可查

最後一棒有一行值得單獨講：

```python
task.set_env_variable("PIPELINE_IMAGE", IMAGE)
```

因為**容器裡沒有 git 可以問**。你想在台帳裡記「這顆模型是哪一版程式碼訓練的」，
就得在編譯 pipeline 的時候把身份**帶進去**。

gate 那一棒收到之後寫進台帳：

```python
entry["lineage"]["code_image"] = os.environ.get("PIPELINE_IMAGE", "unknown")
entry["lineage"]["run_id"] = run_id
```

> **這是 lineage 最容易漏掉的一段**：大家都會記「用了哪份資料」，
> 但「用了哪一版程式碼」常常沒人記——而它同樣會改變模型。
> image digest 是最誠實的答案，因為它不會被改寫。

---

## 跑起來長這樣

四棒依序執行，每一格綠了才走下一格：

```
prepare-data → train-model → evaluate-model → promotion-gate
```

參數在建立 run 的時候填：

```
run_id=run1  sample_mb=2  max_iters=300  max_val_loss=6.0
```

⚠️ **最後那個 `max_val_loss` 是 run 參數——誰都能填。**
這件事的後果我[另外寫了一篇](/2026/09/three-identical-models-one-shipped/)，
簡短版是：三顆能力幾乎相同的模型，只因為門檻從 6.0 改成 8.0，一顆上線兩顆被擋。

---

**你們的模型訓練有沒有進 pipeline？還是還在 notebook 或某台機器上手動跑？**

{% include lab-env.html %}
