---
layout: post
title: "pod 要到卡 ≠ 模型算在卡上"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-16 09:00:00 +0800
tags: [openshift-ai, gpu, hardware-profile, acceptance, kserve, verification]
excerpt: "GPU 被 pod 佔住、但模型其實跑在 CPU 上——這是最貴的一種失敗，而且所有你會看到的畫面都是綠的。這篇是分辨它的唯一乾淨訊號，以及 3.x 的 Hardware Profile 怎麼設。"
feedback_question: "你們驗收 GPU 環境時，實際看的是哪個訊號？有沒有查過 compute-apps？"
---

## 1. 這是什麼

**Hardware Profile** 是 OpenShift AI 3.x 用來定義「一份工作可以要多少資源」的物件——
CPU、記憶體、還有加速器。

⚠️ **2.x 叫 Accelerator Profile，3.x 改名並擴大範圍。**
（任何文件裡出現「Accelerator Profile」，那是 2.x 的材料。）

看預設的長什麼樣：

```bash
oc get hardwareprofile -A
# opendatahub   default-profile   5d
oc get hardwareprofile default-profile -n opendatahub -o jsonpath='{.spec}' | jq
```
```json
{"identifiers":[
  {"identifier":"cpu",    "resourceType":"CPU",    "minCount":1,     "defaultCount":2,    "maxCount":4},
  {"identifier":"memory", "resourceType":"Memory", "minCount":"2Gi", "defaultCount":"4Gi","maxCount":"8Gi"}]}
```

要加 GPU 就多一個 identifier，`identifier: nvidia.com/gpu`。

**但這篇的重點不是怎麼設定它。是設定完之後，你怎麼知道 GPU 真的被用到。**

---

## 2. 為什麼會被唬：安靜的 fallback

幾乎每個 PyTorch 服務都有這一行：

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
```

**這是業界標準寫法，不是壞程式。** 但它有一個特性：
當 CUDA 掛不上時——驅動沒裝、容器沒掛 nvidia runtime、
torch 裝成 CPU wheel、`CUDA_VISIBLE_DEVICES` 被清空——
**它不報錯、不警告，安靜退回 CPU。**

服務照常啟動、readiness probe 照常變綠、推論照常回應正確答案。

而在 Kubernetes 那一層：pod 的 `resources.limits["nvidia.com/gpu"]: 1`
**照樣成立，那張卡照樣被這個 pod 佔住不給別人用。**

於是你同時得到「**GPU 被消耗**」和「**GPU 沒被使用**」——最貴的一種失敗。

> 廠商 demo 常死在這裡，而且**他們自己不知道**，
> 因為所有他們會給你看的畫面都是綠的。

---

## 3. ⭐ 實測：哪些訊號分辨得出來

我跑了兩組。**同一份程式碼、同一個 venv、同一個 torch（cu128）、同一顆模型、同一個 prompt。
B 組唯一的改動是 `CUDA_VISIBLE_DEVICES=""`**——那正是「容器沒把卡掛進來」在應用層的效果。

| 訊號 | A：真 GPU | B：假 GPU | 分辨得出來？ |
|---|---|---|---|
| 服務啟動 | 正常 | 正常 | ❌ |
| `/health` 的 `model_loaded` | true | true | ❌ |
| 推論結果正確性 | 正常 | 正常 | ❌ |
| 應用自報的 `device` 欄 | `cuda` | `cpu` | ⚠️ 見 §4 |
| 延遲中位數（8 次） | **606 ms** | **1520 ms** | ⚠️ 2.5×，見 §4 |
| `nvidia-smi` 使用率峰值 | 30% | 0% | ⚠️ 只反映整張卡 |
| `nvidia-smi` 顯存 | 238 MiB | 2 MiB（＝基線） | ⚠️ 同上 |
| **`--query-compute-apps`** | **有這個 process** | **（空）** | ✅ **唯一乾淨訊號** |

### 那個唯一乾淨的訊號

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

**真的在用**（我剛才重跑的）：

```
pid, process_name, used_gpu_memory [MiB]
377875, python, 374 MiB
```

**假的**（`CUDA_VISIBLE_DEVICES=""`）：

```
pid, process_name, used_gpu_memory [MiB]
（空）
```

**它列的是「哪些 process 正在這張卡上跑 compute context」。**
不是整張卡的使用率、不是顯存總量——是**逐個 process 的歸屬**。

一個沒有真的用到卡的程式，**不可能出現在這個清單上**。

---

## 4. 為什麼其他訊號都不夠

**應用自報的 `device` 欄**：那是應用自己說的。它說 `cuda` 只代表
`torch.cuda.is_available()` 回 true，不代表**這次推論**真的走了 GPU
（模型可能沒 `.to(device)`、輸入可能還在 CPU）。**自報不是證據。**

**延遲**：2.5 倍看起來很明顯，但那是我這個小模型在這張卡上的比值。
換模型、換卡、換 batch size，倍率就變了。
**而且你要有「快的那組」當對照才知道慢——驗收現場通常只有一組。**

**整張卡的使用率與顯存**：`nvidia-smi` 頂部那個數字是**整張卡**的。
如果那台機器上還有別的東西在用 GPU（監控、另一個 pod、你自己的測試腳本），
你會看到很漂亮的 30%，而它跟你要驗的那個服務無關。

> ⚠️ 我第一輪就是這樣被騙的：A 組沒關就去跑 B 組，讀到假訊號。
> **量之前先確認基線是乾淨的**：`compute-apps` 要是空的、顯存回到 2 MiB。

---

## 5. 驗收現場照這個順序要

由弱到強，**對方給不出第 4 項就是沒證據**：

1. 「有設 GPU」→ 看 `oc get pod -o jsonpath='{...resources.limits}'`
   　（只證明**要到了**卡）
2. 「應用說它用 cuda」→ 打 `/health`
   　（**自報**，不算）
3. 「`nvidia-smi` 有數字」→ 整張卡的，**要求同時停掉其他工作**
4. ✅ **「`--query-compute-apps` 列出這個服務的 process」**
   　（**唯一能把使用歸屬到特定 process 的訊號**）

進階一點的第 5 項：**在容器裡面跑**那個指令。
因為在主機上跑只證明「有東西在用卡」，在容器裡跑才證明
「**這個容器**看得到、而且用得到」。

---

## 6. 兩個會讓你自己騙自己的量級陷阱

**① 模型太小，看不出差別。** 我的模型只有 0.8M 參數，
GPU 的優勢在這個尺度上不明顯。如果你拿一個小模型驗收，
可能真的量不出差異——**要用接近正式規模的模型驗**。

**② 只跑一次。** 第一次推論包含 CUDA context 初始化、模型載入到顯存，
比後續慢很多。**至少跑 8 次取中位數**，而且丟掉第一次。

---

## 7. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| GPU | pod 的 `limits` 有 `nvidia.com/gpu` | **`--query-compute-apps` 在容器內列得出這個 process** |

---

## 8. 我的環境限制（誠實標一下）

我的 CRC 是一台 VM，**叢集內看不到 GPU**：

```bash
oc get nodes -o jsonpath='{.items[*].status.capacity.nvidia\.com/gpu}'
# （空的）
```

所以上面的 A/B 對照是**在主機上**跑的，不是在 pod 裡。
**機制完全相同**（`CUDA_VISIBLE_DEVICES=""` 模擬的就是「容器沒掛到卡」），
但我沒有實測過 device plugin、nvidia runtime、Hardware Profile 注入這一整段。

那一段要在真的有 GPU 的叢集上驗，而**驗法還是同一個指令**。

---

**你們驗收 GPU 環境時，實際看的是哪個訊號？有沒有查過 compute-apps？**

{% include lab-env.html %}
