---
layout: post
title: "Day 4：在一台筆電上裝一套 OpenShift AI"
series: "OpenShift AI 入門 30 天"
date: 2026-09-04 09:00:00 +0800
tags: [openshift-ai, odh, crc, installation, tutorial, getting-started]
excerpt: "用 CRC 在自己的機器上跑一套完整的 OpenShift AI。從資源規劃、裝 operator、建 DataScienceCluster，到確認每個元件真的起來——含每一步的驗證指令。"
feedback_question: "你是在自己的機器上試，還是直接用公司的測試叢集？遇到的第一個卡點是什麼？"
---

## 這是什麼、解決什麼問題

要評估或學習 OpenShift AI，你需要一套能自己動手改壞再改回來的環境。

**CRC（OpenShift Local）** 就是這個——它在你的機器上跑一台 VM，
裡面是一套完整的單節點 OpenShift。然後你在上面裝
**ODH（Open Data Hub）**，那是 RHOAI 的上游開源版，元件同源。

**這篇走完之後你會有**：一套跑得起來的 OpenShift AI，
可以建 workbench、跑 pipeline、上線模型。

---

## 什麼時候你會用到

- 要評估要不要導入，但不想先跟人要一套測試叢集
- 廠商說「這個功能有」，你想自己驗一次
- 要學，而學習需要一個弄壞了不會有人罵的環境

**如果你已經有測試叢集可以用，可以跳過 CRC 那段**，直接從
「裝 operator」開始——後面的步驟一樣。

---

## 前置條件

| 項目 | 最低 | 建議 | 說明 |
|---|---|---|---|
| vCPU | 4 | **12 以上** | 我實跑 13 vCPU，開四個元件後餘裕不多 |
| 記憶體 | 9 GB | **40 GB** | 我實跑 40 GB；實際用掉 27.9 GiB（Day 23 有數字）。32 GB 我沒驗過 |
| 磁碟 | 35 GB | **120 GB** | image 很吃空間，workbench 一顆就 1.7 GB |
| 帳號 | Red Hat 帳號（免費） | | 拿 pull secret 用 |

⚠️ **CPU 是最容易低估的。** 我一開始給 10 vCPU，
結果開了幾個元件之後 workbench 就排不進去了——
`Insufficient cpu`，而 DSC 的錯誤訊息完全不會提到這件事。

**先給足，比之後再調省事。**（調整要重啟 CRC，約十分鐘。）

> **關於機器**：我用的是 **Framework Laptop 16**——
> Ryzen AI 7 350（8 核 16 執行緒）、64 GB RAM、1 TB NVMe。
>
> ⚠️ **表上寫的是 vCPU，不是實體核心。** 這台實體核心只有 8 個，
> 但有 16 個邏輯處理器，所以撥得出 13 vCPU——`crc config set cpus` 吃的就是這個數字。
>
> 值得講一句的不是規格，是**這條路一定會撞到記憶體牆**。
> 上面那張表的「建議」欄，40 GB 是起點不是終點，
> 開始跑 pipeline 和多個 workbench 之後還會再往上（Day 23 有實測數字）。
>
> **所以挑機器時，記憶體和硬碟能不能自己換，比買的時候給你多少更重要。**
> 這台的 RAM 和 SSD 都是標準規格、自己拆得開，
> 撞牆的時候我還有路走；焊死記憶體的機器就只剩「開小一點」或「重買」。

---

## 步驟一：裝 CRC 並起一套 OpenShift

到 [console.redhat.com/openshift/create/local](https://console.redhat.com/openshift/create/local)
下載 CRC 和你的 pull secret。

```bash
crc setup                       # 檢查環境、裝好虛擬化相關的東西
crc config set cpus 13
crc config set memory 40960     # MB
crc config set disk-size 120    # GB
crc start -p ./pull-secret.txt
```

第一次會拉一個約 30 GB 的 VM 映像，看網路速度，通常是半小時到一小時。

**驗證這一步：**

```bash
eval $(crc oc-env)                        # 把 oc 放進 PATH（只對當前 shell 有效）
crc status
#   OpenShift:  Running (v4.22.7)

oc login -u kubeadmin -p $(crc console --credentials | grep -oP "(?<=kubeadmin -p )\S+") \
   https://api.crc.testing:6443
oc get nodes
#   NAME   STATUS   ROLES                         AGE   VERSION
#   crc    Ready    control-plane,master,worker   30d   v1.35.6
```

⚠️ `eval $(crc oc-env)` **每開一個新終端機都要重跑一次**，
不然會找不到 `oc`。可以寫進 `.bashrc`／`.zshrc`。

---

## 步驟二：裝 ODH operator

### ⚠️ 先確認你的 catalog 裡真的有它

**這一步我卡最久**，而且卡的地方在動手之前：

```bash
oc get packagemanifest -n openshift-marketplace | grep -i opendatahub
```

**如果是空的，先別急著查 channel。** 我在 OCP **4.22** 上就是空的——
內建的 community catalog 有 297 個 package，**裡面沒有 `opendatahub-operator`**。

解法是自己掛一個舊版的 index：

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: community-operators-v420
  namespace: openshift-marketplace
spec:
  sourceType: grpc
  image: registry.redhat.io/redhat/community-operator-index:v4.20
  displayName: Community Operators (v4.20)
```

⚠️ **這個 index image 有數 GB，pull 要 6–8 分鐘**，`oc get pods -n openshift-marketplace` 會看到它慢慢起來。

**驗證這一步**——確認它從哪個 catalog 來：

```bash
oc get packagemanifest opendatahub-operator -n openshift-marketplace \
  -o jsonpath='{.status.catalogSource}'
# community-operators-v420
```

> **這件事值得記住的不是「掛 v4.20」這個解法**（你的版本組合可能不一樣），
> 是**「operator 在不在 catalog 裡」跟「channel 選哪個」是兩個不同的問題**，
> 而錯誤訊息長得很像：都是「找不到」。

### 然後才是 Subscription

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: opendatahub-operator
  namespace: openshift-operators
spec:
  channel: fast-3                     # ← 3.x 走這個 channel
  name: opendatahub-operator
  source: community-operators-v420    # ← 對應你上面掛的那個
  sourceNamespace: openshift-marketplace
```

⚠️ **channel 選錯會裝到 2.x。** 先看有哪些：

```bash
oc get packagemanifest opendatahub-operator -n openshift-marketplace \
  -o jsonpath='{range .status.channels[*]}{.name}{"\t"}{.currentCSV}{"\n"}{end}'
```
```
fast        opendatahub-operator.v2.35.0
fast-3      opendatahub-operator.v3.5.0     ← 要這個
odh-2.8.z   opendatahub-operator.v2.8.1
```

### 還要裝 cert-manager

**3.x 的 KServe 需要 cert-manager。**（2.x 需要的是 Serverless + Service Mesh，
3.x 已經不用了——所以照 2.x 教學做會缺這個，而缺了它 KServe 起不來。）

在 OperatorHub 裝 `cert-manager-operator` 即可，用預設設定。

**驗證這一步：**

```bash
oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort -u | grep -E 'opendatahub|cert-manager'
#   cert-manager-operator.v1.20.0
#   opendatahub-operator.v3.5.0
```

⚠️ **不要用 `oc get csv -A | grep`。** operator 裝在 AllNamespaces 模式時，
CSV 會複製到每一個 namespace——我的叢集會吐 **82 行**，看起來像裝了 82 套。
加 `jsonpath` 和 `sort -u` 才看得到真實數量。

---

## 步驟三：建 DataScienceCluster

裝完 operator，叢集上**什麼都還沒有**。你要建一個 `DataScienceCluster`（DSC）
告訴它要開哪些元件：

```yaml
apiVersion: datasciencecluster.opendatahub.io/v2
kind: DataScienceCluster
metadata:
  name: default-dsc
spec:
  components:
    dashboard:     { managementState: Managed }   # 網頁介面
    kserve:        { managementState: Managed }   # 模型上線
    aipipelines:   { managementState: Managed }   # pipeline
    workbenches:   { managementState: Managed }   # Jupyter
    modelregistry: { managementState: Managed }   # 模型台帳
    ray:           { managementState: Removed }
    trustyai:      { managementState: Removed }
    feastoperator: { managementState: Removed }
```

### 先開哪些？

**建議從三個開始**：`dashboard` + `kserve` + `aipipelines`。
那三個就能讓你「訓練出模型 → 上線 → 打得到」。

需要 Jupyter 再加 `workbenches`，需要模型台帳再加 `modelregistry`。

**不要一開始就全開**，理由有三個：

1. 每開一個元件，離線環境的鏡像清單就長一截
2. 有些元件有額外的前置（例如 `kueue` 要另外裝 RHBOK）
3. **開了卻沒配好的元件會讓 DSC 長期顯示 `Not Ready`**——
   然後你就學會忽略那個狀態了

⚠️ **元件名稱在 3.x 改過**：2.x 叫 `datasciencepipelines`，
3.x 叫 **`aipipelines`**。照 2.x 的 YAML 寫，DSC 會拒絕。

**驗證這一步：**

```bash
oc get dsc default-dsc -o json | jq -r '.status.conditions[] | select(.type|endswith("Ready")) | "\(.type)\t\(.status)\t\(.reason)"'
```
```
DashboardReady      True
KserveReady         True
AIPipelinesReady    True
WorkbenchesReady    True
ModelRegistryReady  True
Ready               False   NotReady        ← 見下
```

### ⭐ 怎麼讀 `Ready: False`

**最上面那個 `Ready` 是所有模組的 AND。** 你關掉的元件也會出現在條件列表裡：

```
AIGatewayReady   False   Removed    Module ManagementState is set to Removed
```

reason 是 **`Removed`** 代表「**我沒開**」，不是「它壞了」。

**所以看到 `Ready: False` 先別緊張，逐條讀 reason。**
我的叢集長期是 `Not Ready`，而模型服務、pipeline、監控全部正常。

---

## 步驟四：打開 dashboard

```bash
oc get route -A | grep -iE 'rh-ai|dashboard'
#   openshift-ingress   rh-ai   rh-ai.apps-crc.testing
```

⚠️ **3.x 的 route 名稱換過。** 2.x 是 `odh-dashboard`／`rhods-dashboard`，
我這裡是 `rh-ai`（走 `data-science-gateway`）。照舊文件找會找不到——
**直接列出來看最快。**

用 `kubeadmin` 登入，你會看到 Projects、AI hub、Develop & train 那幾個選單。

---

## 怎麼確認做對了

**四個檢查，前三個是「跑完了」，第四個才是「能用」：**

```bash
# 1. 叢集活著
oc get nodes

# 2. operator 都 Succeeded
oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}' | sort -u

# 3. 你開的元件都 Ready
oc get dsc default-dsc -o json | jq -r '.status.conditions[] | select(.status=="True") | .type'

# 4. ⭐ dashboard 打得開，而且建得出一個 project
```

**第四步只能用眼睛。** 前三步全綠而 dashboard 打不開的情況是存在的
（例如 route 沒生成、或憑證還沒簽好）。

---

## ⚠️ 我裝的時候還撞到三件事

這三個在官方文件裡都找不到，但都會讓你停在「看起來裝好了，其實沒有」。

**① dashboard 起不來：缺一個 CRC 不會有的 ClusterRole**

CRC 預設關掉 cluster monitoring，所以沒有 `cluster-monitoring-view` 這個 ClusterRole，
而 ODH 的 dashboard 部署會去綁它。建一個同名的空殼就過了：

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: cluster-monitoring-view}
rules: []
```

**② dashboard 的 notebooks 模組：operator 自己權限不夠**

symptom 是 RBAC escalation 檢查不過——operator 要授出 PV／workspaces 的權限，
但它自己沒有。補給 `opendatahub:dashboard-operator`
（PV、workspaces、storageclasses，加上 `escalate` 和 `bind`）。

⚠️ **補完之後要刪掉 operator pod 強制它重新 reconcile**，
否則它不會自己重試——**你會以為權限沒補對。**

**③ 模型目錄少一個檔，容器就 CrashLoop**

我第一次上線失敗是因為服務啟動時還需要一個漂移監控用的語料檔，
而我只放了權重和 tokenizer。**「模型需要哪些檔案」要當成部署前置條件寫下來**，
不是「有 ckpt 就好」。

---

## 常見問題

**Q：`crc start` 卡在 "Waiting for kube-apiserver availability" 很久。**
A：第一次啟動要拉 30 GB 映像並初始化，慢是正常的。
超過三十分鐘再看 `crc status` 和 `~/.crc/crc.log`。

**Q：`oc` 指令找不到。**
A：每個新終端機都要 `eval $(crc oc-env)`。

**Q：某個元件一直不 Ready。**
A：照這個順序查——
`oc get pods -n opendatahub | grep -v Running` 看誰沒起來 →
`oc describe pod <名字>` 看 Events（**CPU/記憶體不足會出現在這裡，
而不會出現在 DSC 的訊息裡**）→ `oc logs` 看應用層的錯。

**Q：DSC 一直 `Not Ready`，但東西都能用。**
A：正常。逐條看 `status.conditions` 的 reason，
`Removed` 是你沒開，不是壞了。

**Q：CRC 重開之後模型服務起不來。**
A：如果你的模型或 image 放在**叢集外**（例如主機上用 podman 跑的 MinIO／registry），
`crc start` 不會把它們帶起來，要自己起。
[這個坑我另外寫過](/2026/09/all-green-but-dead/)。

**Q：資源給不夠想改。**
A：`crc stop` → `crc config set cpus N` → `crc start`。約十分鐘。
**磁碟不要事後調**（要重建 VM），一開始就給足。

---

**你是在自己的機器上試，還是直接用公司的測試叢集？遇到的第一個卡點是什麼？**

{% include lab-env.html %}
