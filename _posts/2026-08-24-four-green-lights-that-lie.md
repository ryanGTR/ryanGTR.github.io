---
layout: post
title: "儀表板全綠，四件事還是錯的：OpenShift AI 驗收實錄"
date: 2026-08-24 10:00:00 +0800
tags: [openshift-ai, odh, kserve, gpu, disconnected, mlops, acceptance]
excerpt: "在 lab 上把 Open Data Hub 裝起來、把模型上線、把 pipeline 跑完，全程綠燈。然後逐條去驗，發現四件事是錯的——而且每一件都是靠看儀表板永遠看不出來的。"
---

當你是驗收的人而不是安裝的人，你面對的不是「裝好了沒」，而是**「裝對了嗎」**。這兩個問題的差別，在於前者看畫面就能回答，後者不行。

我在筆電的 CRC 上把 Open Data Hub 3.5 裝起來（它是 OpenShift AI 的上游開源版，元件同源），用 KServe 把一顆自己從零訓練的小 GPT 上線，再開 Data Science Pipelines 跑一條訓練流程。全程綠燈。

然後我開始逐條驗證。以下四件事都是綠燈狀態下的錯誤。

---

## 一、pod 要到 GPU，不代表模型算在 GPU 上

這是四件裡最貴的。

幾乎所有推論服務都有這樣一行，這是標準寫法不是壞程式：

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
```

CUDA 掛不上時——驅動沒裝、容器沒掛 nvidia runtime、torch 裝成 CPU wheel、`CUDA_VISIBLE_DEVICES` 被清空——這行**不報錯、不警告**，安靜退回 CPU。服務照常啟動、readiness probe 照常變綠、推論照常回應。

而在 Kubernetes 這一層，pod 的 `resources.limits["nvidia.com/gpu"]: 1` 照樣成立，**那張卡照樣被這個 pod 佔住不給別人用**。於是你同時得到「GPU 被消耗」和「GPU 沒被使用」。

我做了一組對照：同一份程式碼、同一個 venv、同一個 torch（cu128 build）、同一顆模型、同一個 prompt。**B 組唯一的改動是 `CUDA_VISIBLE_DEVICES=""`** ——這正是「容器沒把卡掛進來」在應用層的效果。兩組分開跑，先確認閒置時 GPU 是乾淨的。

| 訊號 | A：真 GPU | B：假 GPU | 分得出來？ |
|---|---|---|---|
| 服務啟動 / 推論結果 | 正常 | 正常 | ❌ |
| 延遲中位數（8 次） | 606 ms | 1520 ms | ⚠️ 只差 2.5 倍 |
| `nvidia-smi` 使用率 | 30% | 0% | ⚠️ 整張卡的數字 |
| **`--query-compute-apps`** | **`19826, python, 228 MiB`** | **（空）** | ✅ |

只有最後一條分得出來：

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

它問的是「**哪個進程真的在這張卡上配了記憶體**」，而這是應用程式無法自報、也不容易造假的。在叢集上就是：

```bash
oc debug node/<gpu-node> -- chroot /host nvidia-smi \
   --query-compute-apps=pid,process_name,used_memory --format=csv
```

**要在送出推論請求前後各跑一次。** 沒有對照，不算證據。

兩個會讓你自己騙自己的陷阱：

1. **延遲比不是固定的**。我這顆模型只有 8M 參數，GPU 只快 2.5 倍（模型太小，kernel launch 和資料搬運吃掉大半好處）。7B 級的模型應該是 10–50 倍。反過來說，**小模型「沒快多少」不能證明沒用到 GPU**。
2. **顯存佔用小不代表沒用到**。A 組只吃 228 MiB、使用率峰值 30%。判準是「有沒有出現在 compute-apps」，不是數字大不大。

---

## 二、`imageID` 不反映 image 從哪裡拉來

離線環境的核心問題是：叢集裡的每顆 image 都必須來自你的私有 registry。

直覺的驗法是列出所有 pod 的 `imageID` 看有沒有外部位址。我原本也是這樣寫檢查腳本的，直到我真的把一顆 image 鏡像進私有 registry、設好 `ImageDigestMirrorSet`、確認 pod 從私有 registry 起來了——然後查 `imageID`：

```
quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9@sha256:6f0e6268…
```

**還是 quay.io。** 但它確實是從私有 registry 拉的，證據有三件：Harbor 的 `pull_count` 增加了、節點的 `registries.conf` 裡那個來源標著 `blocked = true`、pod 正常 Running。原站被封鎖而 pod 起得來，只可能來自鏡像端。

原因很簡單但容易忽略：`imageID` 回報的是 image 的**身份**（來源名稱 ＋ digest），IDMS 改的是**去哪裡拿**，不改**這是誰**。

所以這一條要拆成兩個不同的問題：

| 問題 | 能用 imageID 回答嗎 | 正確方法 |
|---|---|---|
| 哪些 image 必須進鏡像清單？ | ✅ 可以 | `imageID` 去重統計 |
| 這些 image 實際上會不會走外網？ | ❌ 不行 | 看節點 `/etc/containers/registries.conf` 的 `blocked` 與 `mirror` |

順帶一提第一個問題的答案很有意思：在一個 DataScienceCluster 顯示 Ready、dashboard 全綠的叢集上，正在跑的 pod 用了 **60 顆外部 image**，光元件本身就 23 顆。而 operator bundle 的 `relatedImages` 只列了 5 顆。**只照 bundle 做鏡像，會裝到一半斷。**

最容易漏的是 sidecar：`odh-kube-rbac-proxy` 被注入到每個 InferenceService 的 pod 裡，不在任何「元件清單」上，但少了它模型就上不了線。

---

## 三、鏡像設定的預設值，會讓離線測試「假成功」

`ImageDigestMirrorSet` 有一個欄位叫 `mirrorSourcePolicy`，預設是 `AllowContactingSource`：

| 值 | 行為 |
|---|---|
| `AllowContactingSource`（預設） | 鏡像拉不到 → **回頭找原站** |
| `NeverContactSource` | 鏡像拉不到 → 直接失敗 |

如果你在還有對外連線的環境測試離線設定——這是常態，因為要先驗過才敢斷網——用預設值會得到「看起來成功」的結果，實際上走的是外網。真正斷網後才失敗。

節點端對應的實作就是 `registries.conf` 裡的 `blocked = true`。驗收時要看到這個字。

還有一個相關的坑：IDMS 靠 **digest** 比對，所以鏡像過程必須保留原始 digest（`skopeo copy --preserve-digests`）。若映像被重新打包導致 digest 改變，**IDMS 會靜默失效**，症狀是叢集仍往外網拉，而錯誤訊息不會指向真正原因。

更麻煩的是：**IDMS 只管 digest 拉取**（節點設定是 `pull-from-mirror = "digest-only"`）。但 pipeline 執行期產生的 pod 是**用 tag 拉的**：

```
init=quay.io/opendatahub/ds-pipelines-argo-argoexec:3.6.12
```

tag，不是 digest。IDMS 完全管不到，要另外設 `ImageTagMirrorSet`。官方離線文件主推 IDMS（因為安裝階段的 image 都釘 digest），但執行期不是。這是「照官方文件做完，離線仍然不通」的典型缺口。

而且這幾顆 image（`argoexec`、`driver`、`launcher`）**是跑第一條 pipeline 才會被拉的**——安裝完成後做任何盤點都不會出現它們。跑過一次 pipeline 後，全叢集去重 image 從 65 顆變成 93 顆。

**結論：image 盤點必須在「跑過一次完整 pipeline 之後」再做一次。**

---

## 四、內建資料庫是 latin1，中文一個字都存不進去

這條特別適合台灣的環境。

上傳一條 pipeline 直接 HTTP 500，介面上只顯示「Internal Server Error」。翻後端 log 才看到真因：

```
Error 1366 (22007): Incorrect string value: '\xE5\xAE\x8C\xE6\x88\x90...'
  for column `mlpipeline`.`pipeline_versions`.`PipelineSpec`
```

`\xE5\xAE\x8C\xE6\x88\x90` 是「完成」兩個字。內建 MariaDB 的 database 預設是 `latin1_swedish_ci`。

影響範圍比第一眼大：不只 pipeline 本體，中繼資料的 `Artifact`／`Execution`／`Context` 表也全是 latin1——代表**執行期間只要產生任何中文中繼資料就會失敗**。

修法要注意表級 `CONVERT TO CHARACTER SET` 會被外鍵擋（Error 1832），得逐欄位轉並跳過外鍵欄位：

```sql
ALTER TABLE `<table>` MODIFY `<col>` <type>
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

我這邊轉了 67 個文字欄位，剩下 8 個是 UUID 欄位不需要中文。轉完就上傳成功。

**為什麼這條值得寫進驗收條件**：用英文測永遠不會踩到。這是 demo 抓不到、上線才爆的典型。

---

## 那怎麼驗才有用

四件事的共同點是：**它們在所有會給你看的畫面上，都跟正確狀態長得一模一樣。**

歸納下來，有用的驗收條件有三個特徵：

1. **問「證據」不問「狀態」**。「GPU 有配給嗎」是狀態，「哪個進程在卡上配了記憶體」是證據。
2. **要求對照，不接受單點**。送請求前後各查一次、GPU 與 CPU 各跑一次、封鎖原站再拉一次。單一數字沒有意義。
3. **明確寫出「不接受什麼」**。這比寫「要求什麼」有用得多——因為對方交來的東西通常都能滿足「要求什麼」。

最後一個心得跟技術無關：我原本用覆蓋率當進度指標（清單裡幾條驗過了），後來發現那是錯的指標。lab 裡永遠驗不到 GPU 硬體、驗不到 75 GB 的鏡像量級，而那恰好是真實環境裡最貴的部分。**覆蓋率衝高只會製造「準備好了」的錯覺。**

換成「這條沒驗，現場我會不會被唬過去」來排序之後，做的事情完全不一樣了。

---

*文中所有數字皆為單機 lab 實測（CRC + Open Data Hub 3.5 + KServe RawDeployment，GPU 部分為 RTX 5070 Laptop）。*
