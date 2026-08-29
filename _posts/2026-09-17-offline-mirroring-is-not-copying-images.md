---
layout: post
title: "離線鏡像：operator 宣告 2 顆，實際跑起來要 76 顆"
date: 2026-09-17 09:00:00 +0800
tags: [openshift-ai, odh, offline, disconnected, oc-mirror, idms, acceptance]
excerpt: "在內網裝 OpenShift AI，最大的坑不是「怎麼鏡像」，是「鏡哪些」。operator bundle 給你的清單，跟實際要跑的差了一個數量級。"
feedback_question: "你們的離線鏡像清單是怎麼來的？廠商給的，還是自己從跑起來的環境反查的？"
---

## 1. 問題長什麼樣

企業內網裝東西的標準流程：跟廠商要一份 image 清單 → 鏡進內網 registry →
設定叢集去那裡拉 → 安裝。

聽起來很直接。**問題出在第一步：那份清單是怎麼來的。**

---

## 2. ⭐ 我在叢集上數的

Operator 的 CSV 有一個 `relatedImages` 欄位，那是**官方宣告的相依 image 清單**：

```bash
oc get csv opendatahub-operator.v3.5.0 -n openshift-operators \
   -o jsonpath='{.spec.relatedImages}' | jq -r '.[].name'
```
```
odh-kube-auth-proxy-image
odh-kube-rbac-proxy-image
```

**兩顆。**

現在數實際跑起來需要幾顆：

```bash
oc get pods -n opendatahub --field-selector=status.phase=Running \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.image}{"\n"}{end}' \
  | sed 's|@sha256.*||' | sort -u | wc -l
```
```
22
```

**光是 `opendatahub` 這一個 namespace 就 22 顆。**

整個叢集（含 OCP 自己的）：

```bash
oc get pods -A --field-selector=status.phase=Running \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.image}{"\n"}{end}' \
  | sed 's|@sha256.*||' | sort -u | wc -l
```
```
76
```

來源分佈：

```
37  quay.io
31  registry.redhat.io
 2  docker.io
 5  私有 registry（我自己的）
 1  registry.access.redhat.com
```

**68 顆來自外網。**

---

## 3. 為什麼 `relatedImages` 靠不住

它宣告的是「**operator 這個程式自己需要的**」，不是「**這個平台跑起來需要的**」。

差在這幾類：

| 類別 | 例子 | 為什麼不在清單上 |
|---|---|---|
| **元件 image** | dashboard、kserve controller、notebook controller、model controller | 那是 operator **部署出來的東西**，不是它自己 |
| **注入式 sidecar** | `kube-rbac-proxy`、`storage-initializer` | **執行期才被注入**，靜態清單看不到 |
| **runtime image** | notebook 的 Jupyter image、serving runtime | 使用者選的，數量隨你開幾種而變 |
| **你自己的** | 你的模型服務 image | 廠商當然不知道 |

第二類最陰：**`storage-initializer` 不在任何元件清單上，
但它是模型上線的必要條件**——沒有它，`InferenceService` 起不來。

---

## 4. 怎麼拿到真的清單

**唯一可靠的方法：在一個能連外網的環境把它跑起來，然後反查。**

```bash
# 所有 Running pod 實際用的 image（含 sidecar 與 init container）
oc get pods -A --field-selector=status.phase=Running -o jsonpath='
{range .items[*].status.containerStatuses[*]}{.imageID}{"\n"}{end}
{range .items[*].status.initContainerStatuses[*]}{.imageID}{"\n"}{end}' \
  | sed 's|@.*||' | sort -u
```

⚠️ 用 **`.imageID`** 不要用 `.image`——前者是實際拉到的 digest，
後者可能是 tag。**離線環境一律用 digest，tag 會漂移。**

而且要**把功能都跑過一遍再數**：只裝不用的話，
很多 image（notebook runtime、pipeline 的 launcher、driver）根本不會被拉下來。

---

## 5. ⚠️ 清單裡可能有不存在的 image

這個我今天才踩到。ODH 3.5 內建的 ImageStream 裡有這種東西：

```
jupyter-rocm-minimal:3.6
  → quay.io/opendatahub/odh-workbench-jupyter-minimal-rocm-py312-ubi9:3.6_ea1-v1.47
  → not found
```

**3.5 的 tag 匯入正常，3.6 的 tag 在 quay.io 上根本不存在。**

在連得到外網的環境，這只是 DSC 上一行警告，沒人在意。
**在離線環境，你會照著一份含有不存在 image 的清單去鏡像**——
然後在內網花時間查為什麼少東西。

**所以鏡像之前要先驗證清單本身**：每一顆都 `skopeo inspect` 得到，才算數。

```bash
while read -r img; do
  skopeo inspect "docker://$img" >/dev/null 2>&1 || echo "✗ $img"
done < image-list.txt
```

---

## 5.5　⭐ 我後來真的踩到了，而且是最糟的形式

上面那條「清單裡可能有不存在的 image」，我原以為只是理論上的風險。
**幾天後我自己撞到了，而且比想像的更陰。**

我要建一個 workbench，pod 卡在 `ImagePullBackOff`。錯誤訊息：

```
Failed to pull image "quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9@sha256:8ab465…":
  (Mirrors also failed: [100.117.49.79:8088/odh/…@sha256:8ab465…:
   reading manifest sha256:8ab465… : artifact not found])
```

**注意：Harbor 裡「有」那個 repo。** IDMS 也「有」設而且生效。
但拉不到。

去比對 digest：

| | digest |
|---|---|
| imagestream 的 `3.5` tag 要 | `sha256:8ab465…` |
| imagestream 的 `3.6` tag 要 | `sha256:6f0e62…` |
| **Harbor 裡實際鏡的** | **`sha256:6f0e62…`** ← 那是 **3.6** |

**我鏡的是 3.6，而 pod 要的是 3.5。**

完整因果鏈：

```
① 我鏡像那天，抓的是 3.6 的 digest
② 上游後來把 3.6 的 tag 撤掉了（3.6_ea1-v1.47 → not found）
③ imagestream 現在解析到 3.5
④ IDMS 把來源改導向我的 Harbor
⑤ Harbor 裡只有 3.6 那顆 → manifest not found
⑥ ImagePullBackOff
```

### 為什麼這種失敗最難查

- Harbor 裡**有那個 repo**，名字完全正確 ✅
- IDMS **設了而且生效** ✅
- `oc get pod` 只說 `ImagePullBackOff`，**不會說「你鏡的是另一個 digest」**

而且它會誤導你往「網路慢」的方向想。我一開始也以為是慢——
分辨方法是**直接去問你的 registry**：

```bash
skopeo inspect --tls-verify=false \
  docker://<你的registry>/<repo>@sha256:<pod要的那個digest>
```

回 `artifact not found` 就是**沒鏡到**，不是慢。
慢的話你會看到 `Pulling` 持續進行而沒有 `Failed`。

### 補鏡的成本（實測）

```bash
skopeo copy \
  docker://quay.io/opendatahub/…@sha256:8ab465…   ← 來源給 digest
  docker://<你的registry>/…:3.5                     ← 目的地給 tag
```

**17 分 26 秒，1,698 MB。一顆。**

⚠️ **來源一定要給 digest**——這樣鏡過去的那顆 digest 才會跟來源一致，
IDMS 才對得上。如果來源也用 tag，你無法保證鏡到的是同一顆——
**而那正是這個坑的成因。**

### 兩個帶得走的結論

**① 上游會撤 tag。** 你鏡的時候存在的東西，幾天後可能就不在了。
**鏡像清單需要定期重驗，不是鏡完就結束。**

**② 正式環境的時間成本要先算。**
一顆 1.7 GB 的 image 要 17 分鐘。幾十顆就是好幾小時——
而且往往是在**你發現少東西的當下**才開始傳，也就是最不想等的時候。

---

## 6. IDMS ≠ insecureRegistries

拿到 image 之後，要讓叢集去內網拉。這裡有一個常見的混淆：

| | 做什麼 |
|---|---|
| **IDMS**（ImageDigestMirrorSet） | **改寫拉取來源**：看到 `quay.io/x` 就去 `my-registry/x` 拿 |
| **insecureRegistries** | **允許用 HTTP 或自簽憑證**連某個 registry |

**兩件事完全獨立。**

只設 `insecureRegistries`：叢集還是會去 quay.io 拉，只是允許你的私有 registry 不用 TLS。
離線環境下 quay.io 連不到，一樣失敗。

只設 IDMS：來源改對了，但如果你的私有 registry 是 HTTP 或自簽憑證，
會卡在 TLS 驗證。

⚠️ **IDMS 用的是 digest 比對。** 如果你的 pod spec 寫的是 tag 不是 digest，
IDMS 不會生效——要用 ITMS（ImageTagMirrorSet）。這是另一個常見的「設了但沒作用」。

---

## 7. 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| 離線鏡像 | 內網 registry 裡有東西、安裝成功 | **斷網之後，把所有功能再跑一次都不失敗** |

**安裝成功不代表鏡完整。** 很多 image 是「用到某個功能時才拉」的——
你不建 workbench，就不會發現 notebook image 沒鏡到。

驗收方式：**斷網，然後把驗收清單從頭走一遍。**

---

## 8. 給驗收的三個要求

1. **要 digest 清單，不要 tag 清單**（`name@sha256:...`）
2. **要求清單來自「跑起來的環境反查」**，不是 operator bundle 的 `relatedImages`
   ——問一句「這份清單怎麼產的」就知道
3. **明確問：sidecar 與 init container 有沒有涵蓋**
   ——`kube-rbac-proxy`、`storage-initializer` 這兩顆特別容易漏

而最有效的一句是：

> **「請在斷網的環境，從 dashboard 建一個 workbench 並開啟它。」**

那一個動作會同時驗到：元件 image、notebook runtime image、
sidecar、以及 IDMS 有沒有真的生效。

---

**你們的離線鏡像清單是怎麼來的？廠商給的，還是自己從跑起來的環境反查的？**

{% include lab-env.html %}
