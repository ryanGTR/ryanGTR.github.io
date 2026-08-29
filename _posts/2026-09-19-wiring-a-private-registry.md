---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 19：接一個私有 registry"
date: 2026-09-19 09:00:00 +0800
tags: [openshift-ai, odh, harbor, registry, secrets, tutorial, ironman2026]
excerpt: "Day 18 把 image 搬進來了，這篇講怎麼讓叢集用得到：pull secret 放在哪一層、為什麼有時候「我明明設了」還是拉不到。"
feedback_question: "你們的內部 registry 是什麼？Harbor、Quay，還是 JFrog？"
---

## 這是什麼、解決什麼問題

Day 18 解決了「image 在不在」和「叢集去哪找」。
這篇解決第三件事：**叢集有沒有權限拉。**

私有 registry 幾乎都要認證。而 OpenShift 上「認證」這件事**有三層可以設**，
設錯層是「我明明設了還是拉不到」的主要原因。

---

## 什麼時候你會用到

- 用內部 Harbor / Quay / JFrog
- 合規要求 image 只能從內部來源拉
- 要在 pipeline 裡 build 並推 image

---

## 前置條件

- 一個私有 registry，帳號密碼或 robot account
- Day 18 的 TLS 信任已經設好（**不然這篇會白做**）

---

## 步驟一：三層 pull secret，選對一層

| 層 | 影響範圍 | 怎麼設 | 什麼時候用 |
|---|---|---|---|
| **叢集全域** | 所有 namespace | `oc set data secret/pull-secret -n openshift-config` | 平台自己要拉的 image |
| **ServiceAccount** | 那個 SA 起的所有 pod | `oc secrets link` | **最常用** |
| **Pod** | 單一 pod | `imagePullSecrets:` | 特例 |

⚠️ **最常見的錯誤：在 namespace 建了 Secret，以為就生效了。**

**建 Secret ≠ 有人在用它。** Secret 必須被 SA 連結、或被 pod 明確引用。

---

## 步驟二：建 Secret

```bash
oc create secret docker-registry harbor-pull \
  --docker-server=registry.internal:8088 \
  --docker-username='robot$odh+puller' \
  --docker-password='<token>' \
  -n <你的 ns>
```

⚠️ **Harbor 的 robot 帳號名稱含 `$` 和 `+`**，
在 shell 裡一定要用單引號，不然會被展開成空的——
**而錯誤訊息只會說認證失敗。**

---

## 步驟三：連到 ServiceAccount

```bash
oc secrets link default harbor-pull --for=pull -n <ns>
```

**但 OpenShift AI 的元件不都用 `default` SA。**
先確認你的 pod 用的是哪一個：

```bash
oc get pods -n <ns> -o custom-columns='NAME:.metadata.name,SA:.spec.serviceAccountName'
```

常見的有 `default`、`<你的 isvc>-sa`、workbench 自己的 SA。
**每一個都要連。**

**驗證這一步：**

```bash
oc get sa default -n <ns> -o jsonpath='{.imagePullSecrets}'
# [{"name":"harbor-pull"}]
```

---

## 步驟四：⭐ 真的拉一次

**前面每一步都可能「設了但沒生效」，只有這一步能證明。**

```bash
oc run pulltest --rm -it --restart=Never \
  --image=registry.internal:8088/odh/some-image:3.5 -- echo ok
```

失敗的話，錯誤訊息會告訴你是哪一類問題：

| 訊息 | 意思 | 回去看 |
|---|---|---|
| `x509: certificate signed by unknown authority` | 不信任 registry | Day 18 步驟四 |
| `unauthorized: authentication required` | 沒有憑證或沒連上 SA | 步驟三 |
| `manifest unknown` | image 不在那裡 | Day 18 步驟二 |
| `no route to host` | 網路不通 | 防火牆 |

**這張表能省下很多亂試的時間**——四個症狀對應四個完全不同的原因。

---

## 步驟五：pipeline 要推 image 的話

Pipeline 裡 build image 的話，**push 的憑證是另一份**（要寫入權限）。

```bash
oc create secret docker-registry harbor-push \
  --docker-server=registry.internal:8088 \
  --docker-username='robot$odh+pusher' \
  --docker-password='<token>' -n <ns>

oc secrets link pipeline-runner harbor-push -n <ns>   # 注意沒有 --for=pull
```

⚠️ **pull 和 push 要用不同的 robot 帳號。**
給推送權限的憑證如果被用在每個拉取的 pod 上，
**任何能進那個 pod 的人都能覆蓋你的 image。**

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | Secret 存在 | `oc get secret -n <ns>` |
| 2 | **連上了 SA** | `oc get sa <sa> -o jsonpath='{.imagePullSecrets}'` |
| 3 | **真的拉得下來** | 步驟四那個 pod |
| 4 | 平台元件也拉得到 | workbench 開得起來 |
| 5 | **權限是最小的** | 見下 |

第 5 項：**用你的 pull 帳號試著推一次，應該要失敗。**

```bash
skopeo copy docker://alpine:latest \
  docker://registry.internal:8088/odh/test:1 \
  --dest-creds 'robot$odh+puller:<token>'
# 應該要 401
```

**沒驗過的最小權限就不是最小權限。**

---

## 常見問題

**Q：`insecureRegistries` 可以長期用嗎？**
A：不行。它關掉的是 TLS 驗證，**中間人可以換掉你的 image**。
lab 用來省事可以，正式環境一定要走 CA 信任。

**Q：全域 pull secret 跟 namespace 的哪個優先？**
A：兩者是**合併**的，不是覆蓋。
但全域那份**每個 namespace 都拿得到**——
所以只有平台自己要用的憑證放那裡，**團隊的憑證不要放全域**。

**Q：robot 帳號會過期嗎？**
A：Harbor 的 robot 可以設有效期，**預設有**。
**過期時的症狀是「昨天還好好的，今天全部拉不到」**——
把到期日記進行事曆，不要等它咬你。

**Q：image 掃描要接在哪？**
A：registry 端（Harbor 內建 Trivy）和 pipeline 端都可以，
**兩者的意義不同**：registry 掃的是「倉庫裡有什麼」，
pipeline 掃的是「這次要不要放行」。
**只有後者會擋下東西**——這個區分 Day 27 會展開。

---

**你們的內部 registry 是什麼？Harbor、Quay，還是 JFrog？**

{% include lab-env.html %}
