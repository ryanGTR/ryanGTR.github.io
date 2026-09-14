---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 11：ServingRuntime——內建的十四種模型伺服器"
date: 2026-09-11 09:00:00 +0800
tags: [openshift-ai, odh, kserve, servingruntime, vllm, tutorial, ironman2026]
excerpt: "你不需要自己寫載入模型、開 API 的程式，平台內建了十四種伺服器。但「內建」不等於「能用」——這篇講怎麼查、怎麼選，以及為什麼照文件寫的自動配對在剛裝好的叢集上必定失敗。"
feedback_question: "你們的模型是什麼格式？ONNX、pickle、還是 HuggingFace 目錄？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上重跑的**（升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

Day 10 我們用「自帶容器」的方式把模型上線了——自己寫 API、自己載入權重、
自己開 port。**那是最後的手段，不是第一選擇。**

**`ServingRuntime` 就是「會跑某類模型格式的伺服器」的範本。**
你的模型格式對得上，`InferenceService` 就只要三行：

```yaml
spec:
  predictor:
    model:
      modelFormat: { name: onnx }
      storageUri: s3://models/my-model/
```

**沒有 image、沒有 port、沒有 API 程式碼。** 剩下的它處理。

⚠️ 但這份 YAML **在剛裝好的叢集上會直接失敗**——原因在步驟三，
那也是這篇最需要講清楚的一段。

---

## 什麼時候你會用到

**幾乎每次部署模型的時候，你都該先問這個問題**：
「我的格式有沒有現成的 runtime？」

有的話，你省掉的不只是寫程式的時間，還有：
維護那個 image、跟著上游修 CVE、處理批次推論、
處理並行、實作 metrics endpoint。**這些都是別人已經做完的事。**

**什麼時候不用**：自己刻的模型、有自訂前後處理、
或推論邏輯不是「輸入 → 模型 → 輸出」那麼單純。

---

## 前置條件

- DSC 裡 `kserve` 是 `Managed`
- 知道你的模型存成什麼格式（這是本篇的核心問題）

---

## 步驟一：看有哪些

ODH 把內建 runtime 放成 OpenShift `Template`。**別用名字去 grep：**

```bash
oc get templates -n opendatahub -o json \
  | jq -r '.items[] | select(.objects[0].kind=="ServingRuntime") | .metadata.name'
```

⚠️ `grep runtime-template` 只會列出 12 個——**`kserve-ovms` 和
`guardrails-detector-huggingface-serving-template` 的名字裡沒有那段字串**，
而 `kserve-ovms` 正是下面要用的那一個。

十四個，分成三類：

### vLLM 家族（給 LLM 用，九個）

`vllm-cpu`、`vllm-cpu-x86`、`vllm-cuda`、`vllm-rocm`、`vllm-gaudi`、
`vllm-multinode`、`vllm-spyre-{x86,s390x,ppc64le}`

**同一個 vLLM，九種硬體變體。** 選錯不會報錯，會跑得很慢或起不來。

⚠️ **`vllm-cpu` 不是「一般 CPU」的意思。** 名字會騙人，去問叢集：

```bash
oc get templates -n opendatahub -o json | jq -r '.items[]
  | select(.metadata.name|test("vllm"))
  | "\(.objects[0].metadata.name)\t\(.metadata.annotations["openshift.io/display-name"])"'
```
```
vllm-cpu-runtime        vLLM CPU (ppc64le/s390x) ServingRuntime for KServe
vllm-cpu-x86-runtime    vLLM CPU (x86) ServingRuntime for KServe - Tech Preview
vllm-cuda-runtime       vLLM NVIDIA GPU ServingRuntime for KServe
vllm-gaudi-runtime      vLLM Intel Gaudi Accelerator ServingRuntime for KServe
vllm-rocm-runtime       vLLM AMD GPU ServingRuntime for KServe
vllm-spyre-*-runtime    vLLM Spyre（IBM 的 AI 加速器）
```

**一般 x86 機器上跑 CPU 推論，你要的是 `vllm-cpu-x86-runtime`**——
而它標著 Tech Preview。`vllm-cpu-runtime` 是給 IBM Power 與 z 系列的。

### 傳統 ML（三個）

| runtime | 支援格式 |
|---|---|
| `mlserver-runtime` | sklearn、xgboost、lightgbm、onnx |
| `mlserver-cuda-runtime` | 同上，GPU 版 |
| `kserve-ovms`（OpenVINO） | openvino_ir、onnx、tensorflow、paddle、pytorch |

**scikit-learn 模型不需要任何自訂容器。** 這點常被忽略。

### 特殊用途（兩個）

`autogluon-runtime`（表格與時序）、`guardrails-detector-huggingface-runtime`（內容過濾）。

9 ＋ 3 ＋ 2 ＝ 14。

---

## 步驟二：查你的格式對不對得上

**不要看文件，問叢集：**

```bash
oc get template mlserver-runtime-template -n opendatahub -o json \
  | jq -r '.objects[0].spec.supportedModelFormats[] | "\(.name) \(.version)"'
```
```
sklearn 0
sklearn 1
xgboost 1
xgboost 2
lightgbm 3
lightgbm 4
onnx 1
```

一次掃全部：

```bash
for t in $(oc get templates -n opendatahub -o name | grep runtime-template); do
  echo "== $t"
  oc get $t -n opendatahub -o json \
    | jq -r '.objects[0].spec.supportedModelFormats[]?.name' | sort -u | tr '\n' ' '
  echo
done
```

**這份清單是你叢集上的事實，不會過期。**

---

## 步驟三：autoSelect——以及為什麼你八成用不到它

**這一節是我在對帳時整個推翻重寫的。** 原本我照文件寫「標了
`autoSelect: true` 的格式，你只寫 `modelFormat` 就會被自動挑中」。
實際到叢集上跑，**它不會**。

先看那個欄位確實存在：

```bash
oc get template kserve-ovms -n opendatahub -o json \
  | jq -r '.objects[0].spec.supportedModelFormats[] | "\(.name)\tauto=\(.autoSelect)"'
```
```
openvino_ir     auto=true
onnx            auto=null
tensorflow      auto=true
tensorflow      auto=true
paddle          auto=true
pytorch         auto=true
```

然後送一份最單純的 ISvc——只寫格式，不寫 runtime：

```yaml
spec:
  predictor:
    model:
      modelFormat: { name: sklearn }
      storageUri: s3://models/sklearn-demo/
```

結果**沒有 pod、沒有 Deployment**，`oc get isvc` 的 READY 是 `Unknown`
（不是 `False`，所以你盯著那一欄看不出壞了），錯誤只在 events 裡：

```bash
oc get events --field-selector involvedObject.name=<isvc 名字>
```
```
Warning  InternalError  no runtime found to support predictor with model type: {sklearn <nil>}
```

換成 `modelFormat: { name: vLLM }`——**明明有八個 template 標了
`autoSelect: true`**——一樣的錯誤。

❖❖❖

**問題出在「內建」這兩個字。**

KServe 配對時只掃兩種東西：你 namespace 裡的 `ServingRuntime`，
和叢集層的 `ClusterServingRuntime`。**ODH 內建的那 14 個都是 `Template`，
兩種都不是**——KServe 根本看不到它們。

```bash
oc get clusterservingruntimes
# No resources found

oc get servingruntimes -A
# llm-serve-demo   mlserver-runtime   ...   ← 只有這一個，還是我從 dashboard 部署時生的
```

而那唯一一個實例，`autoSelect` **七個格式全是 null**：

```bash
oc get servingruntime mlserver-runtime -n <ns> -o json \
  | jq -r '.spec.supportedModelFormats[] | "\(.name)\tauto=\(.autoSelect)"'
```
```
sklearn    auto=null
sklearn    auto=null
xgboost    auto=null
...
```

**因此：`Template` 上的 `autoSelect: true` 是看得到吃不到的。**
要它生效，得先把 template 實例化成真的 `ServingRuntime`：

```bash
oc get template vllm-cpu-x86-runtime-template -n opendatahub -o json \
  | jq '.objects[0]' | oc apply -n <你的 ns> -f -
```

我把 `vllm-cpu-runtime` 實例化之後，**同一份沒改過的 ISvc 立刻配對成功**，
Deployment 生出來、image 是 `quay.io/vllm/vllm:latest`。

## 解法

實務上就兩條路，而它們最後都指向同一件事：

1. **顯式寫 `runtime:`**——不依賴配對。我 lab 裡那個 sklearn 模型就是這樣，
   dashboard 幫你部署時也是這樣填的。
2. 真的想要自動配對，就**先實例化你要的 runtime**，而且要知道
   `mlserver` 系列即使實例化了也不會自動配（它自己沒標 `autoSelect`）。

⚠️ 而且一旦實例化多個 vLLM 變體，**八個都標著 `autoSelect: true`**，
配到哪一個不是你決定的——**在 x86 上配到 `vllm-cpu-runtime`（ppc64le/s390x 版）
不會報錯**，只會慢或起不來。

**所以正式環境的結論很簡單：`runtime:` 一律顯式寫死。**

---

## 步驟四：用它

以 vLLM 跑一個 HuggingFace 格式的 LLM 為例：

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: my-llm
spec:
  predictor:
    model:
      modelFormat: { name: vLLM }
      runtime: vllm-cpu-x86-runtime   # ← x86 機器要用這個，不是 vllm-cpu-runtime
      storageUri: s3://models/my-llm/
      resources:
        limits: { cpu: "8", memory: 16Gi }
```

runtime 那邊的 container 定義長這樣（節錄）：

{% raw %}
```yaml
image: quay.io/vllm/vllm:latest
args:
  - --port=8080
  - --model=/mnt/models              # ← storage-initializer 放的位置
  - --served-model-name={{.Name}}    # ← 你的 ISvc 名字會代進來
```
{% endraw %}

**看得懂這三行，你就知道整個機制是怎麼接起來的**：
權重被抓到 `/mnt/models`，runtime 從那裡讀，模型名字從 ISvc 帶進去。

**驗證這一步：**

```bash
oc get servingruntime -n <ns>        # ← 先確認它在，不在就沒有下一步
oc get isvc my-llm
oc logs <predictor-pod> -c kserve-container | head -30
```

⚠️ 顯式寫了 `runtime:` 也一樣要**那個 `ServingRuntime` 真的存在於你的 namespace**。
只有 template 不算。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 0 | **runtime 存在於你的 namespace** | `oc get servingruntime -n <ns>`（只有 template 不算） |
| 1 | runtime 被選中了 | `oc get isvc <name> -o jsonpath='{.spec.predictor.model.runtime}'` |
| 2 | pod 起來 | `oc get pods -l serving.kserve.io/inferenceservice=<name>` |
| 3 | **權重真的被讀到** | `oc logs <pod> -c kserve-container` 有載入紀錄 |
| 4 | 回應格式對 | 打一次真的請求，看回傳結構 |

⚠️ 配不到 runtime 的時候，第 1 到 3 項**全部是空的或 Unknown，而不是紅字**。
真正的錯誤只在 `oc get events` 裡。**這一欄的空白就是訊息本身。**

第 4 項要提醒：**不同 runtime 的 API 協定不一樣。**
MLServer 走 **v2 protocol**，vLLM 走 **OpenAI 相容 API**（`/v1/completions`）。

MLServer 這邊實際打一次長這樣：

```bash
curl -X POST http://<name>-predictor.<ns>.svc.cluster.local/v2/models/<name>/infer \
  -H "Content-Type: application/json" \
  -d '{"inputs":[{"name":"input-0","shape":[1,4],"datatype":"FP32",
       "data":[5.1,3.5,1.4,0.2]}]}'
```
```json
{"model_name":"iris-sklearn","outputs":[{"name":"predict","shape":[1,1],
 "datatype":"INT64","data":[0]}]}
```

⚠️ 順帶：`/v2/models`（列出模型）在 MLServer 上回 `{"detail":"Not Found"}`，
但 `/v2/health/ready` 是 200。**探活要打 health，不要打 models。**

**換 runtime 可能要改前端的呼叫方式**，這在評估時就要問。

---

## 常見問題

**Q：`oc get servingruntimes` 是空的。**
A：剛裝好時是空的，正常——但**這正是自動配對失敗的原因**（見步驟三）。
內建的是 `Template`，**你在 dashboard 上選用時才會在那個專案裡實例化**。
要預先建，把 template 裡的物件抽出來 apply（這條我實跑過，可用）：

```bash
oc get template kserve-ovms -n opendatahub -o json \
  | jq '.objects[0]' | oc apply -n <你的 ns> -f -
```

**Q：pickle 檔可以直接上嗎？**
A：不建議。pickle 反序列化會執行任意程式碼，**這是一個真的資安問題**，
金融業的資安審多半會擋。sklearn 模型可以先轉 ONNX。

**Q：可以自己寫一個 ServingRuntime 嗎？**
A：可以，它就是個 CR，本質是一份 container spec。
**比自帶容器好的地方是可以重複使用**——一個 runtime 服務多個模型。

**Q：`vllm:latest` 這種 tag 可以用在正式環境嗎？**
A：不行。內建 template 用 `latest` 是為了方便，
**正式環境要釘 digest**。這是 Day 20 離線環境會再談的事。

---

**你們的模型是什麼格式？ONNX、pickle、還是 HuggingFace 目錄？**

{% include lab-env.html %}
