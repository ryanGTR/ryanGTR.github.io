---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 18：換版——不重 build image 換一個模型"
date: 2026-09-18 09:00:00 +0800
tags: [openshift-ai, odh, kserve, canary, deployment, tutorial, ironman2026]
excerpt: "模型換版比程式換版頻繁得多。這篇講三種換法、各自的代價，以及換完之後怎麼確認線上真的換了。"
feedback_question: "你們換模型版本要走什麼流程？跟換程式一樣嗎？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。滾動更新的行為兩版一致。"
---

## 這是什麼、解決什麼問題

**模型會換，而且比程式碼換得頻繁。**

如果換一個權重檔要走完整套「重 build image → 推 registry → 掃描 → 部署」，
**兩週換一次還好，每週換兩次就受不了。**

Day 10 講過的 `STORAGE_URI` 就是為了這個：**權重不進 image，換模型只是換 S3 上的檔案。**

---

## 什麼時候你會用到

- 模型定期重訓（多數 ML 專案都會）
- 要 A/B 比較兩個版本
- 上線後發現不對要回滾

---

## 四種換法

| 換法 | 停機 | 能回滾 | 有核准紀錄 | 適合 |
|---|---|---|---|---|
| A 改 `STORAGE_URI` | 短暫 | 改回去 | ❌ | lab、內部服務 |
| B 建新 ISvc + 切 Route | 無 | 切回去 | ❌ | 需要零停機時 |
| C canary 流量分配 | 無 | 調比例 | ❌ | 要漸進驗證 |
| **D 改 git，讓 Argo 部署** | 依底下用 A 或 B | **revert commit** | ✅ | **正式環境** |

⚠️ **注意最後一欄。** A、B、C 都是**某個人在終端機打指令**——
做得到，但沒有任何東西記得是誰、什麼時候、根據什麼做的。
**正式環境要的是 D，而 D 底下跑的還是 A 或 B。**

---

## 換法 A：改 STORAGE_URI

最直接的：

```bash
oc patch isvc my-model --type=merge \
  -p '{"spec":{"predictor":{"model":{"storageUri":"s3://models/my-model/v3/"}}}}'
```

KServe 會滾動更新 pod，新 pod 的 `storage-initializer` 抓新版。

⚠️ **先看你的 ISvc 是哪一種寫法**，這兩種要改的欄位不一樣：

```bash
oc get isvc my-model -o yaml | grep -A3 'predictor:'
```

| 寫法 | 長什麼樣 | 要改哪裡 |
|---|---|---|
| **ModelSpec** | `predictor.model.storageUri` | 上面那條 merge patch |
| **custom container** | `predictor.containers[].env` 裡有 `STORAGE_URI` | 見下 |

我 lab 上那個服務是第二種（自己寫的 FastAPI，不是內建 runtime），要這樣改：

```bash
oc patch isvc llm-scratch --type=json \
  -p '[{"op":"replace","path":"/spec/predictor/containers/0/env/1/value",
        "value":"s3://models/llm-v2/"}]'
```

**拿第一種指令去 patch 第二種不會報錯，但也什麼都不會發生**——
merge patch 會長出一個空的 `model:` 區塊，然後你盯著沒有變化的 pod 想很久。

⚠️ **只改 S3 上的檔案、不改 URI 是不會生效的**——
`storage-initializer` 只在 pod 啟動時跑一次，要刪 pod 才會重來。

**而「同一個路徑換內容」這種做法要盡量避免**：它讓 Day 16 講的血緣鏈直接斷掉，
而且**你會有一個顯示正常、內容早就換過的服務**。**每一版一個路徑。**

---

## 換法 B：新開一個，切 Route

```yaml
# 舊的留著，新開一個
metadata:
  name: my-model-v3
spec:
  predictor:
    model:
      storageUri: s3://models/my-model/v3/
```

新的起來、驗過之後才切：

```bash
oc patch route my-model --type=merge \
  -p '{"spec":{"to":{"name":"my-model-v3-predictor"}}}'
```

**好處是回滾只要把 Route 切回去，舊的一直都在**；代價是同時跑兩份，資源要兩倍。

---

## 換法 C：canary

KServe 支援按比例分流：

```yaml
spec:
  predictor:
    model:
      storageUri: s3://models/my-model/v3/
  canaryTrafficPercent: 10
```

10% 的請求打到新版。

⚠️ **兩個要先想清楚的**：10% 的流量要累積多久才有結論（統計問題，低頻服務可能要好幾天）；
以及你要用什麼指標判斷新版比較好——**延遲和錯誤率看得到，
「預測比較準」要等真實標籤回來**，那可能是幾個月後。

**canary 對前者有效，對後者無效。** 別把它當成模型品質的驗證機制。

---

## 換法 D：改 git，讓 Argo 去部署

**這是正式環境的做法**，前三種是它底下的實作。

git 上只改兩行——`storageUri` 指到新版路徑、`model/sha256` 換成新的 digest——
開 PR、有人 review、merge，Argo 同步。

換到的東西是**紀錄**：誰核准＝PR 的 approver、回滾＝`git revert`、
上線前檢查＝PR 上的 CI。

⚠️ **界線**：Argo 保證「叢集上的宣告 == git 上的宣告」，
**但權重在 S3 不在 git**——有人換掉 S3 上的檔案，Argo 全綠而服務已經變了。
**下面那些檢查照樣要做。** 完整寫法在 Day 25。

---

## 步驟：⭐ 換完之後怎麼確認真的換了

**這是本篇最重要的一節。** 我為了寫它真的換了一次，
第一次就失敗了——**而且四個檢查點全是綠的。**

### 我實際做的事

lab 上有兩個模型：線上的 v1（32 MB）和 gate 放行過但沒人上線的候選（5.5 MB）。
我把候選複製到一個有版本的路徑 `s3://models/llm-v2/`，然後改 `STORAGE_URI`。

**`storage-initializer` 下載成功——1.43 秒，兩個檔案。**
（⚠️ 下面這段 log 是**補檔之前**的狀態。補上 `clean_corpus.txt` 之後這個路徑變成
110 MB，同一個動作實測要 **4.26 秒**——秒數不重要，重要的是它證明少的那個檔案有多大。）

```
Found S3 object: llm-v2/ckpt.pt (5784727 bytes)
Found S3 object: llm-v2/tokenizer.json (37097 bytes)
Successfully copied s3://models/llm-v2/ to /mnt/models
Model downloaded in 1.4338307090074522 seconds.
```

**然後服務起不來。**

```
File "/app/serve/app.py", line 95, in _load_model
    drift=DriftMonitor.from_artifacts(ART),
FileNotFoundError: [Errno 2] No such file or directory: '/mnt/models/clean_corpus.txt'
```

**新版本的路徑少了一個檔案**——而且不是模型檔，是漂移監控用的基準語料。
我複製權重的時候只想到「模型」，沒想到「這個服務啟動需要什麼」。

> **教訓比我原本寫的那條更嚴格**：不是「每一版一個路徑」，
> 是**每一版的路徑要裝得下那一版服務啟動所需要的全部東西**。

### ⭐ 而失敗當下，這四個訊號全是綠的

| 你會看的 | 顯示 | 真相 |
|---|---|---|
| `oc get isvc` | **READY True** | 舊 pod 還在服務 |
| ISvc 的 conditions | Ready / PredictorReady / IngressReady **全 True** | 同上 |
| 打 API | **正常回應** | 回的是**舊版** |
| `oc get deploy` | READY 1 / UPDATED 1 / AVAIL 1 / DESIRED 1 | 這幾欄各自數的不是同一個 pod |

最後一列最陰險：`UPDATED 1` 數的是正在 CrashLoop 的新 pod，
`READY 1`／`AVAIL 1` 數的是還在服務的舊 pod。**每一欄都沒說謊，合起來卻是假的。**

### ⚠️ 還有一種更安靜的失敗：新 pod 根本排不進去

我後來重跑這個實驗時遇到另一種：新 pod 不是 CrashLoop，是 **Pending**。

```
llm-scratch-predictor-...-bscwf   true,true   Running   0        ← 舊的，還在服務
llm-scratch-predictor-...-rjtnm   <none>      Pending   <none>   ← 新的，排不進去

$ oc describe pod ...-rjtnm
Warning  FailedScheduling  0/1 nodes are available: 1 Insufficient cpu.
```

原因是**滾動更新的 `maxSurge` 進位成 1**——換版期間必須多起一個 pod，
而那台節點的 CPU requests 已經到 96%。

> **零停機不是免費的：它要求你在換版期間有第二份資源。**
> 資源不夠時**不會報錯，會靜默卡住**，而上面那四個訊號一樣全綠。

而且這比 CrashLoop **更難發現**：CrashLoop 至少 `RESTARTS` 會跳數字，
**排不進去的 pod 連重啟次數都是 `<none>`**。
我連續量了兩分鐘，`oc get isvc` 六次都是 `READY True`、`/health` 六次都是 200。

**在資源吃緊的叢集上，這比模型檔少一個更常發生。**

### ⭐ 同一個原因的第二種：PVC 卡住

CPU 只是「同時只能有一份」的資源之一。**新舊 pod 必須並存**這件事，
會讓任何獨佔型資源變成換版的絆腳石——**最常見的就是 PVC**。

我實測了兩種 access mode（同一個叢集、同一種 Deployment、預設滾動策略）：

| PVC 的 access mode | 獨佔層級 | 換版結果 |
|---|---|---|
| `ReadWriteOnce`（RWO） | **節點** | ✅ 41 秒完成——新舊 pod 在同一個節點上，RWO 擋不住 |
| `ReadWriteOncePod`（RWOP） | **Pod** | 🔴 **卡死**，新 pod 一直 Pending |

RWOP 那個的事件訊息很直白：

```
Warning  FailedScheduling  0/1 nodes are available:
  1 node(s) unavailable due to PersistentVolumeClaim with ReadWriteOncePod
  access mode already in-use by another pod.
```

**而且這是死鎖**：`maxUnavailable` 捨去成 0 → 舊 pod 要等新 pod Ready 才會被收；
新 pod 要等舊 pod 放開 PVC 才排得進去。**兩邊互等，不會自己好。**

期間 Deployment 的狀態是：

```
Available=True    MinimumReplicasAvailable: Deployment has minimum availability.
Progressing=True  ReplicaSetUpdated: ReplicaSet "..." is progressing.
```

**兩個 condition 都是 True，其中一個還明說「progressing」。**
要等 `progressDeadlineSeconds`（**預設 600 秒**）過了才會轉成 `ProgressDeadlineExceeded`——
**十分鐘**。在那之前你看到的一切都說它很好。

> ⚠️ **RWO 那一列只在單節點上成立。** 多節點叢集上新 pod 可能被排到別的節點，
> 那時 RWO 就會擋（`Multi-Attach error`）。**我這台 CRC 是單節點，驗不到那個情況。**
> 掛 RWO PVC 又跑多節點的服務，換版前先想一下這件事。

**只有兩個訊號抓得到：**

```bash
# ① 有兩個 pod，而且一個沒 ready
oc get pods -l serving.kserve.io/inferenceservice=<name>   -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[*].ready,RESTARTS:.status.containerStatuses[0].restartCount'
# llm-scratch-predictor-6786d546b4-5tfgh   false,true   6      ← 新的，重啟 6 次
# llm-scratch-predictor-868c589678-6k9cm   true,true    1      ← 舊的，還在服務

# ② 服務回報的 digest 沒有變
```

---

## 補完之後：真正的數字

補上缺的檔案、刪掉 pod 讓它重抓：

| 動作 | 實測 |
|---|---|
| 換版 rollout（5.5 MB 權重） | **13 秒** |
| 回滾 rollout（32 MB 權重） | **14 秒** |
| `storage-initializer` 下載 5.8 MB | 1.43 秒 |
| **這期間的服務中斷** | **0**（見下） |

### ⭐ 不要刪 pod——我第一次做錯了

我第一次補完檔案之後，是用 `oc delete pod -l <selector>` 讓它重抓的。
**那一條指令把新舊兩個 pod 都刪掉了，服務真的斷了。**

**正確做法是什麼都不刪，只 patch。** 因為 KServe 產的 Deployment 本來就有滾動策略：

```bash
oc get deploy <name>-predictor -o jsonpath='{.spec.strategy}'
# {"rollingUpdate":{"maxSurge":"25%","maxUnavailable":"25%"},"type":"RollingUpdate"}
```

`replicas: 1` 時 k8s 這樣換算：
**`maxSurge` 25% → 進位成 1**，**`maxUnavailable` 25% → 捨去成 0**。

> **也就是說：新 pod 必須先 Ready，舊 pod 才會被收掉。零停機是預設行為。**

我重做了一次來驗證——**patch 之後什麼都不碰**，同時開一個探針每 0.3 秒打一次 `/health`，
連續 150 秒，中間完成一次換版加一次回滾：

```
RESULT ok=465 fail=0
```

**465 次請求，0 次失敗。**

**所以真正該注意的是：**

| 情況 | 做法 |
|---|---|
| 一般換版 | **只 patch，什麼都不刪**——滾動更新會處理 |
| 新 pod CrashLoop、修好了要它重試 | **只刪那個新的 pod**（`oc delete pod <新pod名>`），不要用 label selector |
| 只改了 S3 上的內容、URI 沒變 | 這時才需要刪 pod——**而且要一個一個刪** |

⚠️ **`oc delete pod -l <selector>` 在單副本服務上就是一次停機。**
這個指令常被當成「讓它重讀設定」的萬用招，**但它不區分新舊，
會把正在服務的那個一起帶走。**（副本數拉到 2 以上就連刪錯都還有另一個頂著。）

我用同一支探針量了這兩條路，數字擺在一起比較清楚：

| 做法 | 探針結果 |
|---|---|
| 只 `patch`，什麼都不刪 | **ok=669 fail=0** |
| `oc delete pod -l <selector>` | **ok=208 fail=42** ← 約 **13 秒**沒有服務 |

**同一個服務、同一支探針、同一天。**

### ⭐ 換完之後，服務自己說它沒登記

換到 v2 之後打服務的 `/model`：

```json
{"serving_digest":"sha256:bb614beb…", "in_registry":false,
 "status":"UNREGISTERED", "metrics":null}
```

**digest 換對了，但這個模型不在台帳上**——因為我是手動 promote 的，跳過了註冊。
如果這是正式環境，現在沒有人答得出「線上這版的評估指標是多少」。
回滾之後才變回 `"status":"production"` 加上完整 metrics。

> **手動換版最貴的代價不是停機，是斷掉證據鏈。**

---

## 四個檢查

前三個上面的實測已經示範過了，這裡只列指令：

```bash
# ① 只剩一個 pod，而且是新的
oc get pods -l serving.kserve.io/inferenceservice=my-model \
  -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[*].ready,AGE:.metadata.creationTimestamp'

# ② 權重檔對（時間戳與大小）
oc exec <pod> -c kserve-container -- ls -la /mnt/models

# ③ digest 對
oc exec <pod> -c kserve-container -- sha256sum /mnt/models/ckpt.pt
```

⚠️ 第 ② 項要看的是**整個目錄**，不是只看模型檔——
我今天就是因為少複製了一個非模型的檔案，服務起不來。

⚠️ **想確認「這個 pod 是從哪個路徑抓的」，不要看主容器的 `STORAGE_URI`：**

```bash
oc get pod <pod> -o jsonpath='{.spec.containers[0].env[?(@.name=="STORAGE_URI")].value}'
# /mnt/models              ← 被 KServe 改寫了，看不出版本

oc get pod <pod> -o jsonpath='{.spec.initContainers[0].args[0]}'
# s3://models/llm-v2/      ← 真正的來源在這裡
```

KServe 的 webhook 會把 `STORAGE_URI` 搬進 storage-initializer 的參數，
再把主容器那個 env 改成本地路徑。**看錯地方會以為自己沒換成功。**

第 ④ 個是新的：

### 檢查四：⭐ 讓服務自己說它在跑哪一版

**在你的服務上開一個回報身分的端點**，像上面那個 `/model`：
一次回答三題——跑哪一版、在不在台帳上、那一版的指標是多少。

⚠️ 這是**你要自己寫的**，平台不會給你。
但它是我今天唯一一眼就看出「換成功了但沒登記」的訊號。
**如果只能加一個東西到模型服務裡，我會加這個。**

---

## 怎麼確認做對了

| | 檢查 | 通過條件 |
|---|---|---|
| 1 | **只剩一個 pod，而且是新的** | ⚠️ 兩個 pod 就是沒換成功，不管 ISvc 說什麼 |
| 2 | ~~ISvc Ready~~ | ❌ **這條沒有用**——換版失敗時它也是 True |
| 3 | **權重檔對** | `ls -la /mnt/models` 時間戳與大小 |
| 4 | **digest 對** | `sha256sum` 等於預期 |
| 5 | **服務自報的身分對** | `/model` 之類的端點（檢查四） |
| 6 | **在台帳上** | ⚠️ 換對了不代表登記了 |
| 7 | **回滾演練過** | ⭐ 見下 |

第 7 項：**在你需要回滾之前，先回滾一次。**

換版當下就是最好的演練時機——新版剛上、舊版還在、你人還在。
**沒演練過的回滾等於沒有回滾。**

我今天做完換版就立刻回滾了一次，**15 秒**，digest 與台帳狀態都回到原樣。
知道這個數字，跟猜「應該很快吧」是兩件事。

---

## 常見問題

**Q：換版要不要重跑資安掃描？**
A：image 沒變就不用重掃 image。**但模型檔本身該不該掃是另一個問題**——
pickle 格式是可執行內容，資安審會問。

**Q：能不能自動換？資料更新就自動重訓上線？**
A：技術上可以。**但「自動上線」要先有自動的品質閘門**，
否則你只是把出錯的速度加快了。Day 28 專講這個。

**Q：舊版本要留多久？**
A：至少留到你確定不用回滾為止。S3 上的舊權重很便宜，留著。

**Q：怎麼確認換版真的沒有斷線？**
A：**自己開探針量，不要相信「應該不會斷」。** 我用的方法：

```bash
# 在叢集內起一個 pod，每 0.3 秒打一次 /health，數成功與失敗
oc run probe --image=curlimages/curl --restart=Never -- sh -c '
  ok=0; fail=0; end=$(( $(date +%s) + 150 ))
  while [ $(date +%s) -lt $end ]; do
    curl -fsS -m 2 http://<svc>/health >/dev/null 2>&1 && ok=$((ok+1)) || { fail=$((fail+1)); echo FAIL; }
    sleep 0.3
  done
  echo "RESULT ok=$ok fail=$fail"'
```

**這也是驗收清單上該有的一條**——「換版不停機」如果沒量過，它就只是個說法。

**Q：換版之後監控的基準要跟著換嗎？**
A：要，而且很容易忘。**用舊版基準看新版會噴一堆假警報**，
然後大家就開始忽略告警。Day 29 會講。

---

**你們換模型版本要走什麼流程？跟換程式一樣嗎？**

{% include lab-env.html %}
