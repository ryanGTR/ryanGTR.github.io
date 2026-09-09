---
layout: post
title: "Day 8：Workbench——把 notebook 搬到叢集上"
series: "OpenShift AI 入門 30 天"
date: 2026-09-08 09:00:00 +0800
tags: [openshift-ai, odh, workbench, jupyter, notebook, tutorial]
excerpt: "Workbench 就是跑在叢集上的 JupyterLab。這篇講它跟你筆電上的 notebook 差在哪、怎麼開一個、資料和 GPU 怎麼接進去。"
feedback_question: "你們資料科學家現在在哪裡寫 code？自己筆電、共用機器，還是平台上？"
---

## 這是什麼、解決什麼問題

**Workbench ＝ 跑在叢集上的 JupyterLab。**

聽起來只是換個地方開 notebook，但換了地方之後這幾件事就變了：

| | 筆電上的 notebook | Workbench |
|---|---|---|
| 資料 | 要先下載一份 | 直接讀叢集裡的儲存 |
| GPU | 有就有，沒有就沒有 | 跟叢集要 |
| 環境 | 每個人不一樣 | 平台給的 image，大家一樣 |
| 資料落地 | **在你筆電上** | 在叢集裡 |
| 你關機之後 | 停了 | 還在跑 |

**金融業通常是為了倒數第二列才導這個東西。**
「資料不落地」寫在規範裡的時候，人在哪裡寫 code 就不是個人偏好問題。

底層是一個叫 `Notebook` 的 CR。它會展開成 **StatefulSet + PVC + Service**，
對外那一段則跟版本有關——**3.x 不再給每個 workbench 開 Route**，
而是在 `opendatahub` namespace 裡建一條 Gateway API 的 `HTTPRoute`：

```bash
oc get httproute -n opendatahub | grep '^nb-'
# nb-llm-serve-demo-my-workbench
# nb-llm-serve-demo-ryan-workbench
```

（命名是 `nb-<namespace>-<workbench 名>`。照舊文件去 `oc get route` 找會找不到。）

---

## 什麼時候你會用到

- 資料不能下載到個人設備
- 要用 GPU，而 GPU 在叢集裡
- 想要「大家的環境一樣」，不要再處理「我這邊跑得動啊」
- 訓練要跑幾小時，不想開著筆電

**不需要的時候**：只是寫寫 code、資料是公開的、也不用 GPU。
那 workbench 只是讓你多按幾次滑鼠。

---

## 前置條件

- ODH／RHOAI 裝好，DSC 裡 `workbenches` 是 `Managed`
- 一個 Data Science Project（就是一個 namespace）
- 想接資料的話：一份 Connection

---

## 步驟一：看有哪些 image 可以選

平台預先準備好一組 notebook image，以 ImageStream 的形式放著：

```bash
oc get is -n opendatahub | grep -E 'jupyter|code-server'
```

ODH 上有 11 個（3.5 和 3.6 都是這 11 個）：

| image | 裡面有什麼 |
|---|---|
| `jupyter-minimal-notebook` | 最小，只有 Jupyter |
| `jupyter-datascience-notebook` | pandas / sklearn / matplotlib |
| `jupyter-pytorch-notebook` | PyTorch（CPU/CUDA） |
| `jupyter-tensorflow-notebook` | TensorFlow |
| `jupyter-minimal-gpu-notebook` | 最小 + CUDA |
| `jupyter-rocm-*` | AMD GPU 版（minimal / pytorch / tensorflow） |
| `jupyter-trustyai-notebook` | 加了 TrustyAI（偏誤與可解釋性） |
| `jupyter-pytorch-llmcompressor` | 模型壓縮／量化 |
| `code-server-notebook` | **不是 Jupyter，是 VS Code** |

最後一個常被忽略。習慣 IDE 的人給 Jupyter 會很痛苦，
**有 VS Code 這個選項這件事本身就值得先講**。

每個 image 有多個版本標籤（`3.5`、`3.6`）：

```bash
oc get is jupyter-datascience-notebook -n opendatahub \
  -o jsonpath='{range .spec.tags[*]}{.name}{"\n"}{end}'
```

⚠️ **選版本不是選新的就好。** 平台升級時舊 tag 可能被移除，
你的 workbench 會起不來。**正式用途要把用到的 image 版本記下來**，
跟記 base image 版本是同一件事。

---

## 步驟二：開一個

Dashboard → 你的專案 → Workbenches → Create workbench。要填的是：

| 欄位 | 怎麼決定 |
|---|---|
| Image | 上一節那張表 |
| Container size | Small / Medium / Large（**這是 requests/limits**） |
| Accelerator | 要 GPU 才選 |
| Cluster storage | PVC 大小（表單會帶一個預設值，**建之前看一眼**；我這台上手動建的兩個是 10Gi 和 2Gi） |
| Connections | 勾了就整包注入環境變數 |

**Container size 那格最容易出事。** 它對應的是 CPU 和記憶體的
requests／limits。單節點叢集上選 Large，可能就直接 Pending，
而且**畫面上不會說是為什麼**：

```bash
oc get pods -n <ns> | grep <workbench 名>
# NAME              READY   STATUS    ...
# my-workbench-0    0/1     Pending

oc describe pod <pod> | tail -5
# Warning  FailedScheduling  ... Insufficient cpu
```

**驗證這一步：**

```bash
oc get notebooks -n <ns>
oc get pods -n <ns> -l notebook-name=<workbench 名>
```

⚠️ **選擇器要用 `notebook-name`，不要用 `app`。**
`app` 這個 label 在 CR 上有、**在 pod 上沒有**，
`-l app=<名字>` 會回你 `No resources found`，看起來像 workbench 沒建起來：

```bash
oc get pods -n llm-serve-demo -l app=my-workbench
# No resources found in llm-serve-demo namespace.   ← 其實它好好地跑著

oc get pods -n llm-serve-demo -l notebook-name=my-workbench
# my-workbench-0   1/1   Running
```

⚠️ **幾個容器算「起來了」跟版本有關。**
舊版會在 pod 裡塞一個 OAuth proxy 側車，所以要看到 `2/2`；
**3.x 把驗證收到 gateway（`openshift-ingress` 裡的 `kube-auth-proxy`）之後，
我這台上兩個 workbench 都是 `1/1 Running`**——
即使其中一個的 `notebooks.opendatahub.io/inject-oauth` 還寫著 `true`。

別背數字，看你自己叢集上正常的那個長什麼樣：

```bash
oc get pod <workbench>-0 -o jsonpath='{range .spec.containers[*]}{.name}{"\n"}{end}'
```

---

## 步驟三：接資料

**方法一：Connection**（推薦）
建立時勾選，環境變數就注入好了：

```python
import os, boto3
s3 = boto3.client("s3", endpoint_url=os.environ["AWS_S3_ENDPOINT"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
```

**方法二：PVC**
資料很大、或本來就在企業儲存上時用這個。掛進來就是一個目錄。

⚠️ **兩者的差別不只是方便**：
S3 上的物件有版本、有存取紀錄；PVC 裡的檔案**誰改了都不會留下痕跡**。
要做資料血緣的話，這個差別是關鍵的。

---

## 步驟四：⭐ 存檔的位置

**只有 PVC 掛載的那個目錄會留下來。**

⚠️ **那個目錄是哪個，不要用猜的。** 從 dashboard 建的通常掛在
`/opt/app-root/src`（JupyterLab 的預設目錄），
但手寫 YAML 建的很可能掛在別的地方。看 CR 最準：

```bash
oc get notebook <name> -n <ns> \
  -o jsonpath='{range .spec.template.spec.containers[0].volumeMounts[*]}{.mountPath}{"\n"}{end}'
```

我這台上兩個 workbench 就掛在不同地方——
`ryan-workbench`（dashboard 建的）在 `/opt/app-root/src`，
`my-workbench`（我自己寫 YAML 建的）在 `/opt/app-root/src/work`。

**在 notebook 裡直接看，一眼分辨：**

```python
!df -h /opt/app-root/src /opt/app-root/src/work
```
```
Filesystem      Size  Used Avail Use% Mounted on
overlay         120G  100G   21G  84% /                       ← 這層是暫時的
/dev/vda4       120G  100G   21G  84% /opt/app-root/src/work  ← 這層才是 PVC
```

**看到 `overlay` 就是寫在容器裡，pod 一重啟就沒了。**
上面這個例子特別值得記：`/opt/app-root/src` 看起來像家目錄、
JupyterLab 一開也是停在這裡，**但它在這台 workbench 上不是 PVC**。

而 workbench 會因為閒置自動停止，這是預設行為——
所以「pod 重啟就沒了」不是理論上的風險，是每天都會發生的事。

還有：**`pip install` 裝的東西不會留下來。** 那些進的是 image layer，
不是 PVC。要固定環境有兩條路：

- `requirements.txt` 放在 PVC 裡，每次開起來自己裝（簡單，但每次要等）
- **自己 build 一個 notebook image**（正式做法，也是可審計的那條路）

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | pod 起來了（容器數看你的版本） | `oc get pods -l notebook-name=<name>` |
| 2 | 打得開 | Dashboard 按 Open |
| 3 | **資料讀得到** | notebook 裡真的 list 一次 bucket |
| 4 | **檔案存得住** | 存檔 → 停掉 → 再開 → 檔案還在 |
| 5 | GPU 真的拿到（若有） | 見下方 |

第 4 項要真的做一次。**很多人是在丟了三小時的工作之後才知道自己寫錯目錄。**

GPU 的驗法——**`nvidia-smi` 有卡不代表你的模型在用卡**：

```python
import torch; print(torch.cuda.is_available(), torch.cuda.device_count())
```

兩個都對才算數。

---

## 常見問題

**Q：建 workbench 一直失敗，訊息看不懂。**
A：ODH 3.5.0 有一個 admission webhook 會擋掉部分 Notebook 建立。
先看 `oc get events -n <ns> --sort-by=.lastTimestamp | tail`。
細節和我的處理方式寫在
[這篇]({{ '/2026/09/workbench-is-broken-in-odh-35/' | relative_url }})。

那組 webhook 在 3.6 上還在，名字可以自己查：

```bash
oc get validatingwebhookconfigurations,mutatingwebhookconfigurations \
  | grep notebook
# odh-notebook-controller-validating-webhook-configuration
# odh-notebook-controller-mutating-webhook-configuration
```

**Q：關掉瀏覽器，訓練會停嗎？**
A：不會，pod 還在跑。但**閒置逾時會把 workbench 停掉**（預設有這個機制），
跑很久的訓練建議別放 notebook，改用 pipeline。

**Q：可以裝自己的套件嗎？**
A：可以，但只活到 pod 重啟。要長久就自己 build image。
**離線環境更是只有這條路**——`pip install` 出不去。

**Q：多人可以用同一個 workbench 嗎？**
A：技術上可以（給 Route 權限），但**不要**。
每個人一個，資源歸屬和存取紀錄才對得起來。

---

**你們資料科學家現在在哪裡寫 code？自己筆電、共用機器，還是平台上？**

{% include lab-env.html %}
