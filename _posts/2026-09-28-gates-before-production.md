---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 28：模型上線前該有哪些門"
date: 2026-09-28 09:00:00 +0800
tags: [openshift-ai, governance, gate, admission, mlops, tutorial, ironman2026]
excerpt: "平台給你的是「可以擋」的能力，不是「已經在擋」的機制。這篇講一個模型上線前該經過哪幾道門、每道門實際擋得住什麼，以及一個會讓門形同虛設的設計錯誤。"
feedback_question: "你們這條鏈上，哪一個點真的會擋下東西？"
---

## 這是什麼、解決什麼問題

**整個系列反覆出現的一句話：平台給你的是「可以擋」的能力，
不是「已經在擋」的機制。**

具體來說：

- Model Registry 可以記錄版本，**但不會阻止沒登記的模型上線**
- image 掃描可以發現 CVE，**但不會阻止有 CVE 的 image 部署**
- 品質閘門可以比較分數，**但門檻是你自己填的**
- 資源上限可以設，**但沒人驗過它會不會擋**

**這一篇講怎麼把「可以擋」變成「在擋」。**

---

## 什麼時候你會用到

- 模型要從 PoC 進正式環境
- 資安或法遵問「怎麼防止未經核可的模型上線」
- 出過一次事，要補機制

---

## 五道門

一個模型從訓練完到上線，可以有五道門。
**你不一定要五道都有，但你要知道自己有幾道。**

| # | 門 | 擋什麼 | 在哪裡實作 |
|---|---|---|---|
| 1 | 資料門 | 資料品質、個資、來源授權 | pipeline 的第一個 step |
| 2 | 品質門 | 模型比上一版差 | pipeline 的評估 step |
| 3 | 供應鏈門 | image 有 CVE、來源不明 | build / registry |
| 4 | **登記門** | 沒登記的模型上線 | **admission webhook** |
| 5 | 上線門 | 人的核可 | 變更管理流程 |

**多數團隊有 2 和 3，缺 1、4、5。**

而 **4 是唯一一道真的會在部署當下擋下來的技術性的門**——
其他的都在更早的階段，繞過它們只要不走那條路就行了。

---

## 門 2：品質門——⭐ 一個很容易犯的設計錯誤

最常見的寫法：

```python
if new_score >= baseline_score - tol:
    promote()
```

「不要比基準差太多」，看起來很合理。

**但如果 `baseline` 每次都更新成上一次的分數，這就是一個滑坡。**

假設 `tol = 0.02`：

| 次數 | 基準 | 這次 | 判定 |
|---|---|---|---|
| 1 | 3.00 | 3.02 | ✅ 過 |
| 2 | 3.02 | 3.04 | ✅ 過 |
| … | | | |
| 20 | 3.38 | 3.40 | ✅ 過 |

**二十次連續退步，每次都合格，累積劣化 0.40。**
沒有任何一次觸發告警。

**修法有三個，可以疊加：**

1. **絕對底線**：`score <= 3.10`，不管基準是多少
2. **不可回退的基準**（ratchet）：基準只能變好，不能跟著變差
3. **累積偵測**：連續 N 次接近門檻就告警

**光有第 1 種就能擋住上面那個滑坡。**

---

## 門 4：登記門——唯一真的會擋的那一道

想法很簡單：**部署 `InferenceService` 的時候，
檢查它的模型版本有沒有在 registry 裡。沒有就拒絕。**

實作是一個 validating admission webhook。**我真的做了一個**，核心只有幾行：

```python
claimed = ann.get("model/sha256") or lbl.get("model-registry/version")
if not claimed:
    deny(f"InferenceService '{name}' 沒有標註模型版本。")
elif claimed not in registry_versions():
    deny(f"模型版本 '{claimed}' 不在台帳上（台帳有 {n} 筆）。")
```

⭐ **`namespaceSelector` 是讓它可以先在一個 namespace 試跑的關鍵**：

```yaml
namespaceSelector:
  matchLabels: {model-gate: enforced}
```

**只有貼了那個 label 的 namespace 會被管。** 這讓你能先在 dev 開、
確認訊息夠清楚、再往 prod 推——**不用一次賭整個叢集。**

### ⭐ 五個反向測試，我全跑過

| 測試 | 結果 |
|---|---|
| 沒標版本就部署 | ✅ **擋下**，訊息直接說要加哪個 annotation |
| 標一個假版本 | ✅ **擋下**：「不在台帳上（台帳有 3 筆）」 |
| 標台帳上真的有的版本 | ✅ **放行** |
| **webhook 讀不到台帳** | ✅ **擋下**——見下 |
| 沒開登記門的 namespace | ✅ **不受影響** |

第四項是意外驗到的。我第一版有個 bug（去讀了自己的 namespace，
台帳其實在另一個），結果是：

```
denied: 登記門讀不到台帳，保守拒絕：HTTP Error 403: Forbidden
```

**`failurePolicy: Fail` 正確地把「我不知道」當成「不可以」。**

> 這一條值得停一下：**護欄壞掉的時候，它的預設行為是什麼？**
> 如果我當時設的是 `Ignore`，那個 bug 會讓登記門靜靜地放行所有東西，
> **而且沒有任何人會發現**——因為部署都成功了。
>
> **一道會在自己壞掉時放行的門，比沒有門更危險：你以為有。**

⚠️ 代價要講清楚：`Fail` 表示 **webhook 掛掉時沒有人能部署模型**。
所以它自己要有監控、要有多副本。**這是你要跟營運換的取捨，不是免費的。**

⚠️ **`failurePolicy` 要想清楚：**

| 設定 | webhook 掛掉時 | 適合 |
|---|---|---|
| `Fail` | **所有部署都被擋** | 安全優先 |
| `Ignore` | 全部放行 | 可用性優先 |

**注意 `failurePolicy` 只處理「webhook 連不上」，
不處理「webhook 說不行」**——後者一定會擋。

這也表示：**`Ignore` 加上一個掛掉的 webhook，等於沒有門。**
所以 webhook 本身要有監控。

---

## ⭐ 一個我 lab 上真的發生的缺口

我做了品質閘門，它會判斷候選模型能不能上。
某次它判定通過。

**然後我去看線上服務——跑的還是舊的那個。**

- 候選模型：5.5 MB，8/28 產出
- 線上服務：32 MB，8/24 部署

**因為沒有任何東西實作「通過之後把它換上去」這一步。**

閘門是綠的、報告是對的、模型是好的，**只是它沒上線。**

> **這是最典型的「可以擋 vs 在擋」**：
> 我做了判斷的部分，沒做執行的部分。
> 而所有的檢查點都顯示正常，因為它們檢查的都是判斷那一段。

**驗證方式很簡單，而且該進驗收清單**：

```bash
# 閘門通過的那個 digest
cat gate-result.json | jq -r '.candidate_sha256'
# 線上實際跑的
oc exec <pod> -c kserve-container -- sha256sum /mnt/models/model.bin
```

**兩個要相等。**

---

## ⭐ 所以「換上去」那一步要自己寫——長這樣

上面那個缺口我後來補起來了，而且在 lab 上跑通。這裡是最小可用的版本。

**在 pipeline 的 gate 之後多接一棒**，做三件事：

```python
# 1. 讀台帳，把這一輪的模型標成 production
reg = load_registry_from_s3()
newest = reg[-1]                      # gate 剛寫進去的那筆
for e in reg:
    if e["status"] == "production":
        e["status"] = "archived"      # 舊的降級
newest["status"] = "production"
newest["lineage"]["promoted_by"] = "pipeline"
save_registry_to_s3(reg)

# 2. 推到服務讀得到的路徑（每一版一個路徑，不要覆蓋）
for f in ("ckpt.pt", "tokenizer.json", "clean_corpus.txt"):
    put(f"/work/artifacts/{f}", "models", f"llm-v{n}/{f}")

# 3. 把台帳同步回叢集，服務端才看得到
patch_configmap("model-registry", {"registry.json": json.dumps(reg)})
```

第 3 步在容器裡不需要 `oc`，用內建的 SA token 打 API 就好：

```python
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
tok = open(SA + "/token").read()
ns  = open(SA + "/namespace").read()
req = urllib.request.Request(
    f"https://kubernetes.default.svc/api/v1/namespaces/{ns}/configmaps/model-registry",
    data=body, method="PATCH",
    headers={"Authorization": "Bearer " + tok,
             "Content-Type": "application/strategic-merge-patch+json"})
```

### 三個我實際撞到的坑

**① 要給 pipeline 的 SA 權限，而且要給最小的。**
預設沒有改 ConfigMap 的權限。用 `resourceNames` 鎖死成那一個：

```yaml
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  resourceNames: ["model-registry"]     # ← 只有這一個
  verbs: ["get", "patch", "update"]
```

**② ⚠️ 不要覆蓋共用的台帳——我就是這樣弄丟一個模型的。**

我的台帳有兩份來源，pipeline 讀的那份原本不存在，
所以 gate 從一個空台帳開始寫、promote 用只有一筆的台帳蓋掉了共用設定。

結果：**原本在跑的另一個服務，digest 沒變、pod 沒重啟，
但它的 `/model` 從 `production` 變成 `UNREGISTERED`。**

**登記新模型成功的同一個動作，把舊模型從名冊上抹掉了。**
台帳要**合併**不要覆蓋，而且**先確認只有一份來源**。

**③ 每一版一個路徑。** 覆蓋同一個路徑的話，git 和台帳都看不出模型換過（Day 18）。

### 怎麼知道真的接上了

**問服務自己**：

```bash
curl -s http://<svc>/model | jq
```

| | 手動 promote | pipeline promote |
|---|---|---|
| `serving_digest` | 對 | 對 |
| **`in_registry`** | **false** | **true** |
| **`status`** | **UNREGISTERED** | registered / production |
| `metrics` | null | 有評估數字 |

**digest 對不代表接上了。** 這張表右邊那三列才是「這顆模型有身分」的意思——
而它們全都來自 promote 那一步。

---

## 怎麼確認你的門真的在擋

**每一道門都要有一次反向測試。**

| 門 | 反向測試 |
|---|---|
| 資料門 | 餵一份有問題的資料，確認 pipeline 停下來 |
| 品質門 | **餵一個明顯更差的模型，確認被拒絕** |
| 供應鏈門 | 用一個已知有 CRITICAL CVE 的 image，確認被擋 |
| 登記門 | 部署一個沒 label 的 ISvc，確認被拒絕 |
| 上線門 | 跳過核可直接部署，確認做不到 |

**每一項都要留下「被擋下來」的證據截圖。**
這是 Day 26 說的護欄層條目，也是稽核最想看的東西。

⭐ 加上一條**閘門到上線的對帳**：

> 閘門通過的那個 digest，等於線上正在跑的 digest。

**這一條會抓到我上面那個缺口**，而前五條都不會。

---

## 常見問題

**Q：五道門都要有嗎？**
A：不用，**但你要講得出自己有幾道、缺哪幾道、為什麼**。
「我們評估後決定不做登記門，因為只有兩個模型且都由同一人管理」
是可以接受的答案；「我們有做治理」不是。

**Q：webhook 會不會拖慢部署？**
A：會，但很少（毫秒級）。
**真正的風險是 webhook 掛掉時的行為**，那要靠 `failurePolicy` 和監控。

**Q：admission webhook 要自己寫嗎？**
A：可以用 **Kyverno** 或 **OPA Gatekeeper** 寫規則，不用自己寫程式。
在 OpenShift 上這兩個都常見。

**Q：這些跟 AI 法規有什麼關係？**
A：法規會問的是「你怎麼確保只有經過核可的模型在服務」。
**這五道門就是那題的技術答案**，而 Day 30 會講證據那一面。

---

**你們這條鏈上，哪一個點真的會擋下東西？**

{% include lab-env.html %}
