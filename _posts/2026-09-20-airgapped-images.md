---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 20：離線環境——image 怎麼進來、怎麼拉得到"
date: 2026-09-20 09:00:00 +0800
tags: [openshift-ai, odh, airgap, mirror, idms, harbor, registry, tutorial, ironman2026]
excerpt: "銀行的叢集連不到 quay.io。這篇講三件事：把 image 搬進來、讓叢集知道去哪找、以及讓它有權限拉——三件都做完才會動。"
feedback_question: "你們的叢集連得到外網嗎？如果不能，image 現在怎麼進來的？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。lab 的 Harbor 走 **HTTP**，所以下面 TLS 那節是照文件寫的、未在本 lab 驗證，實際用的是 `insecureRegistries`。"
---

## 這是什麼、解決什麼問題

你照著 Day 4 裝好了平台，然後在公司環境重做一次——**卡在第一步。**

`ImagePullBackOff`。因為 quay.io 連不到。

**這不是例外，這是金融業的常態。** 而且影響的不只是安裝：
每個 workbench image、每個 ServingRuntime、
pipeline 裡每個 `packages_to_install`——**全部都要有離線的來源。**

---

## 什麼時候你會用到

- 叢集在內網、連不到外部 registry
- 合規要求「所有 image 必須經過內部掃描」
- 用內部 Harbor / Quay / JFrog

---

## 前置條件

- 一個內部 registry
- 一台**同時連得到外網和內部 registry** 的擺渡機
- `skopeo`（或 `oc mirror`）

---

## 步驟一：先搞清楚有三件事要做

**這是最多人卡住的地方**——他們做了第一件，以為就完成了。

| | 做什麼 | 沒做會怎樣 |
|---|---|---|
| **A 搬 image** | 複製到內部 registry | image 不存在 |
| **B 改路由** | 設 mirror 規則 | **image 在，但叢集還是去 quay.io 找** |
| **C 給權限** | pull secret + TLS 信任 | 找得到，但拉不動 |

**三件事是獨立的，而且錯誤訊息不一樣**——步驟五那張表會用到。

---

## 步驟二：搬 image（A）

```bash
skopeo copy --all \
  docker://quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9@sha256:<digest> \
  docker://registry.internal:8088/odh/odh-workbench-jupyter-datascience-cpu-py312-ubi9:3.5
```

⚠️ **來源用 digest，目的地用 tag。**

因為 **mirror 規則是按 digest 比對的**（下一步會看到），
你搬的必須是規則會去找的那一顆，**而 tag 隨時可能指向不同的 image**。

**驗證這一步：**

```bash
skopeo inspect docker://registry.internal:8088/odh/<name>:3.5 | jq -r '.Digest'
```

**digest 要跟來源一模一樣。**

> 我 lab 上搬**一個** workbench image 花了 17 分 26 秒、1,698 MB。
> 完整的平台有幾十個——**這是排期時要算進去的**。

---

## 步驟三：告訴叢集去哪找（B）

OpenShift 4.x 用 `ImageDigestMirrorSet`：

```yaml
apiVersion: config.openshift.io/v1
kind: ImageDigestMirrorSet
metadata:
  name: odh-workbench-mirror
spec:
  imageDigestMirrors:
  - source: quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9
    mirrors:
    - registry.internal:8088/odh/odh-workbench-jupyter-datascience-cpu-py312-ubi9
```

⚠️ **IDMS 只對 digest 形式的參照生效。**
YAML 裡寫 `image: quay.io/xxx:3.5`（tag 形式）**不會被改寫**——
這是名字裡 Digest 的意思。（tag 需求要用 `ImageTagMirrorSet`。）

這不是推論，**套用之後節點上就是這樣寫的**：

```bash
oc debug node/<node> -q -- chroot /host cat /etc/containers/registries.conf
```
```toml
[[registry]]
  location = "quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9"
  blocked = true
  [[registry.mirror]]
    location = "registry.internal:8088/odh/odh-workbench-jupyter-datascience-cpu-py312-ubi9"
    insecure = true
    pull-from-mirror = "digest-only"      # ← 就是這一行
```

⭐ **上面那個 `blocked = true` 來自一個你該自己決定的欄位**：

```yaml
  - source: quay.io/opendatahub/odh-workbench-jupyter-datascience-cpu-py312-ubi9
    mirrors:
    - registry.internal:8088/odh/odh-workbench-jupyter-datascience-cpu-py312-ubi9
    mirrorSourcePolicy: NeverContactSource     # ← 加這行才會 blocked
```

| `mirrorSourcePolicy` | 行為 |
|---|---|
| `AllowContactingSource`（**預設**） | mirror 拉不到就**回去試原來的來源** |
| `NeverContactSource` | 節點上 `blocked = true`，**絕不出門** |

**離線環境應該選 `NeverContactSource`**——不然你會以為自己離線了，
其實每次 mirror 沒命中它都在偷偷敲外網（在真的斷網的機房就是變成逾時）。
**這個選擇會直接改變你在步驟五看到的錯誤訊息。**

套用後**節點會逐台重啟 CRI-O**：

```bash
oc get imagedigestmirrorset
oc get mcp        # 等 UPDATED=True
```

---

## 步驟四：憑證與權限（C）

### TLS 信任

**如果你的 registry 是 HTTPS 且用自簽憑證**，要讓叢集信任它：

```bash
oc create cm registry-certs -n openshift-config \
  --from-file=registry.internal..8088=ca.crt

oc patch image.config.openshift.io/cluster --type=merge \
  -p '{"spec":{"additionalTrustedCA":{"name":"registry-certs"}}}'
```

⚠️ **檔名格式很特別**：主機名裡的冒號寫成兩個點（`registry.internal..8088`）。

> 📌 **誠實標註**：這一段是照 OpenShift 文件寫的，**我 lab 上沒有走過這條路**——
> 我那台 Harbor 是 HTTP，走的是下面那條捷徑。所以「冒號寫兩個點」我沒有實際踩過。

**我 lab 實際用的（正式環境不要）**，它關掉的是 TLS 驗證，
**中間人可以換掉你的 image**：

```bash
oc patch image.config.openshift.io/cluster --type=merge \
  -p '{"spec":{"registrySources":{"insecureRegistries":["registry.internal:8088"]}}}'
```

套用後節點上會長出這個：

```toml
[[registry]]
  prefix = ""
  location = "registry.internal:8088"
  insecure = true
```

### pull secret——三層，選對一層

| 層 | 影響範圍 | 什麼時候用 |
|---|---|---|
| 叢集全域（`openshift-config/pull-secret`） | 所有 namespace | 平台自己要拉的 |
| **ServiceAccount** | 那個 SA 起的所有 pod | **最常用** |
| Pod（`imagePullSecrets`） | 單一 pod | 特例 |

⚠️ **最常見的錯誤：建了 Secret，以為就生效了。**
**Secret 必須被 SA 連結、或被 pod 明確引用。**

```bash
oc create secret docker-registry harbor-pull \
  --docker-server=registry.internal:8088 \
  --docker-username='robot$odh+puller' \
  --docker-password='<token>' -n <ns>

oc secrets link default harbor-pull --for=pull -n <ns>
```

⚠️ **Harbor 的 robot 帳號含 `$` 和 `+`**，shell 裡一定要單引號，
不然會被展開成空的——**而錯誤訊息只會說認證失敗**。

**而且 OpenShift AI 的 pod 不都用 `default` SA：**

```bash
oc get pods -n <ns> -o custom-columns='SA:.spec.serviceAccountName' --no-headers | sort -u
```

我那個裝了 pipeline、workbench 和模型服務的 namespace，跑出來是這樣：

```
default
ds-pipeline-metadata-envoy-dspa
ds-pipeline-metadata-grpc-dspa
ds-pipeline-workflow-controller-dspa
ds-pipelines-mariadb-sa-dspa
llm-sa
```

**一個 namespace 裡六種 SA。** 只連 `default` 的話，
上面那五個起的 pod 全部拉不到 image——**而且它們是平台自己建的，你沒辦法預先知道有幾個。**

**每一個用到的 SA 都要連。**

---

## 步驟五：⭐ 真的拉一次，然後看錯誤訊息分類

```bash
oc run pulltest --rm -it --restart=Never \
  --image=registry.internal:8088/odh/some-image:3.5 -- echo ok
```

失敗的話，**訊息會告訴你是三件事裡的哪一件**：

⚠️ **一定要加 `--image-pull-policy=Always`。** 節點上如果已經有那顆 image 的快取，
kubelet 根本不會去拉，你會看到一個毫無意義的「成功」——我第一次測就這樣過關了兩次。

| 訊息 | 是哪一件 | 回去看 |
|---|---|---|
| `unknown: repository <名字> not found`<br>（或 `manifest unknown`，看 registry 實作） | A：image 不在那裡 | 步驟二 |
| **`registry <來源> is blocked in /etc/containers/registries.conf`** | B：**你用了 tag**。mirror 是 `digest-only`，而來源被 `NeverContactSource` 擋掉了 | 步驟三，改用 digest |
| 還是去外網拉（或離線時逾時） | B：同上，但 `mirrorSourcePolicy` 沒設 `NeverContactSource`，所以它還會去試來源 | 步驟三 |
| `x509: certificate signed by unknown authority` | C：不信任 registry | 步驟四 TLS |
| `unauthorized: authentication required` | C：沒憑證或沒連上 SA | 步驟四 pull secret |
| `no route to host` | 網路／防火牆 | 不是這篇的問題 |

**這張表能省下很多亂試的時間**——每個症狀對應完全不同的原因。

⭐ **第二列是我實測才發現要拆開寫的。** 我原本以為「用了 tag」的症狀是
「它還在連外網」，實際上我的 IDMS 設了 `mirrorSourcePolicy: NeverContactSource`，
節點上對應的是 `blocked = true`：

```
Failed to pull image "quay.io/prometheus/prometheus:v3.6.0":
  registry quay.io/prometheus/prometheus is blocked in /etc/containers/registries.conf
```

**它連試都不會試。** 對離線環境來說這是好事（保證不出門），
但症狀跟「mirror 沒生效」長得完全不一樣——
**看到 `blocked` 不要去查網路，去看你是不是用了 tag。**

同一顆 image 改用 digest：

```
Successfully pulled image "quay.io/prometheus/prometheus@sha256:76947e7e…"
  in 91ms. Image size: 314435533 bytes
```

**91 毫秒、300 MB**——從區網那台 registry 拉的，沒出過門。

---

## 步驟六：別忘了 image 以外的東西

**這是離線環境最容易漏的一節。**

| 東西 | 離線會怎樣 | 怎麼辦 |
|---|---|---|
| pipeline 的 `packages_to_install` | ❌ pip 連不到 PyPI | 自己 build base image |
| workbench 裡 `pip install` | ❌ 同上 | 內部 PyPI mirror |
| HuggingFace 模型下載 | ❌ 連不到 | 先下載，放 S3 |
| **operator 更新** | ❌ OperatorHub 連不到 | 離線 catalog |

**最後一列常被忽略**，然後半年後要修 CVE 時才發現升不了級。
**評估離線可行性時，把「怎麼更新」一起問。**

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | image 在、digest 一致 | `skopeo inspect` |
| 2 | IDMS 存在、節點更新完 | `oc get idms`、`oc get mcp` |
| 3 | **Secret 連上了 SA** | `oc get sa <sa> -o jsonpath='{.imagePullSecrets}'` |
| 4 | **真的拉得下來** | 步驟五那個 pod |
| 5 | 權限是最小的 | 用 pull 帳號試推，**應該要 401** |
| 6 | ⭐ **斷網再試一次** | 見下 |

第 6 項是唯一真的驗證：

> **把叢集對外的連線切掉，然後刪掉一個 pod 讓它重拉。**
>
> 起得來，才代表你真的離線可用。
> 沒做這件事，你不知道有多少東西是靠著「其實還連得到」在跑。

---

## 常見問題

**Q：`oc mirror` 跟 `skopeo` 選哪個？**
A：`oc mirror` 能整批處理 operator catalog，**適合初次建置**；
`skopeo` 精準，**適合補漏和日常維護**。兩個都會用到。

**Q：要 mirror 多少個 image？**
A：**沒有人能事先列全。** 先在能連外網的環境裝一次，然後：

```bash
oc get pods -A -o json | jq -r '.items[].spec.containers[].image' | sort -u
```

**這份清單就是你的搬運清單。**

**Q：pull 和 push 可以用同一個帳號嗎？**
A：不要。**給推送權限的憑證如果被用在每個拉取的 pod 上，
任何能進那個 pod 的人都能覆蓋你的 image。**

**Q：robot 帳號會過期嗎？**
A：Harbor 的 robot 可以設有效期，**預設有**。
症狀是「昨天還好好的，今天全部拉不到」——**把到期日記進行事曆**。

**Q：這些設定能寫進 GitOps 嗎？**
A：IDMS 和 image config 是 cluster-scoped 的 CR，**可以，而且建議一定要**——
這是重建叢集時最容易漏的一塊。Day 25 會談。

---

**你們的叢集連得到外網嗎？如果不能，image 現在怎麼進來的？**

{% include lab-env.html %}
