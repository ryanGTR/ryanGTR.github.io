---
layout: post
series: "OpenShift AI 入門 30 天"
title: "Day 22：憑證與機敏資料"
date: 2026-09-22 09:00:00 +0800
tags: [openshift-ai, odh, secrets, security, tutorial, ironman2026]
excerpt: "S3 金鑰、資料庫密碼、API token——這些東西在 AI 平台上會出現在比你想的更多地方。這篇是盤點方法、四個不該放的位置，以及輪替時會斷在哪。"
feedback_question: "你們的 S3 金鑰多久換一次？換的時候要重啟哪些東西，講得出來嗎？"
lab_env_note: "本篇在 **ODH 3.6.0-ea.1** 上實跑對帳（共用區塊寫的 3.5.0 是 Day 1–9 的版本）。"
---

## 這是什麼、解決什麼問題

一個 AI 平台上會有的憑證，比一般應用多：

- S3 的 access key（資料、模型、pipeline 產物）
- registry 的 pull / push 憑證
- 資料庫密碼（DSPA、Model Registry）
- 外部 API token（LLM 供應商、資料來源）

**而它們散在很多地方**：Secret、環境變數、notebook 裡、
`.ipynb` 的輸出、pipeline 的參數、image layer。

這篇講**怎麼盤點**、**四個不該放的位置**、**輪替時會斷在哪**。

---

## 什麼時候你會用到

- 資安要盤點「這個平台上有哪些憑證」
- 金鑰要輪替
- 有人問「notebook 裡的密碼會不會外流」
- **稽核前**

---

## 步驟一：盤點——問叢集，不要問人

```bash
# 這個 namespace 有哪些 Secret
oc get secret -n <ns> --field-selector type!=kubernetes.io/service-account-token

# ⭐ 真正被掛進 pod 的（這份才是活的）
oc get pods -n <ns> -o json | jq -r '
  .items[] | .metadata.name as $p |
  (.spec.containers[].envFrom[]?.secretRef.name // empty),
  (.spec.containers[].env[]?.valueFrom.secretKeyRef.name // empty),
  (.spec.initContainers[]?.envFrom[]?.secretRef.name // empty),
  (.spec.initContainers[]?.env[]?.valueFrom.secretKeyRef.name // empty),
  (.spec.volumes[]?.secret.secretName // empty)' | sort -u
```

⚠️ **那兩行 `initContainers` 不能省，而我第一版就漏了。**
KServe 的 S3 憑證在 `storage-initializer` 裡——**那是 initContainer**：

```
storage-initializer.AWS_ACCESS_KEY_ID      ← secretKeyRef: minio-s3
storage-initializer.AWS_SECRET_ACCESS_KEY  ← secretKeyRef: minio-s3
```

我的 namespace 裡剛好沒漏，因為 pipeline server 也用同一把（那是一般 container）。
**把 pipeline server 排除掉再跑一次，那把金鑰的命中數就變成 0**——
也就是說，**在一個只有模型服務的 namespace，它會從盤點清單上整個消失。**

**兩份清單要對。**

- 第一份有、第二份沒有 → **沒人在用的憑證**，該清掉（它還是會外洩）
- 第二份有、第一份沒有 → 跨 namespace 引用，**要弄清楚為什麼**

---

## 步驟二：四個不該放憑證的位置

### ① image 裡

```dockerfile
ENV AWS_SECRET_ACCESS_KEY=xxx     # ❌
```

**image 會進 registry、會被複製、會被掃描工具展開。**
任何進了 image layer 的東西都要當成已經公開——
**而且刪不掉，它在歷史 layer 裡。**

### ② notebook 裡

```python
key = "AKIA..."     # ❌
```

`.ipynb` 會被 commit 進 git，**而且輸出區塊也會存進去**。
`print(os.environ)` 執行過一次，金鑰就進版控了。

**正確做法**：從環境變數讀（Day 7 的 Connection 已經幫你注入好了）。

### ③ pipeline 參數

```python
@dsl.pipeline
def p(s3_key: str):     # ❌
```

**pipeline 參數會被記進 MLMD，而且顯示在 UI 上。**
Day 16 說血緣會記參數——**那是優點，但對憑證是缺點。**

**正確做法**：用 `kubernetes.use_secret_as_env`。

### ④ ConfigMap

ConfigMap 沒有 Secret 的存取限制，**而且常常被 GitOps 一起同步進 git**。

---

## 步驟三：Secret 的實際保護程度

⚠️ **講清楚這件事很重要，因為很多人誤會：**

**k8s Secret 預設只是 base64 編碼，不是加密。**

```bash
oc get secret my-secret -o jsonpath='{.data.password}' | base64 -d
```

任何在那個 namespace 有讀 Secret 權限的人都看得到。
**要真的加密要另外做**（etcd encryption at rest、或外部 vault）。

實務上的分層：

| 做法 | 保護程度 | 代價 |
|---|---|---|
| 原生 Secret | 靠 RBAC | 零 |
| + etcd 加密 | 磁碟被拿走也讀不到 | 叢集設定一次 |
| **外部 vault** | 憑證不在叢集裡 | 要接、要維運 |

**金融業多半會被要求走第三種**，
但**先做好第一種**——RBAC 沒設對的話，後面兩種也擋不住內部人。

---

## 步驟四：⭐ 輪替——這才是難的部分

「怎麼存」大家都會答，**「怎麼換」才是會出事的地方**。

**環境變數是 pod 啟動時注入的。改了 Secret，跑著的 pod 不會知道。**

換一把 S3 金鑰要重啟的東西：

| 誰在用 | 怎麼生效 |
|---|---|
| workbench | 重啟（**使用者未存的東西會不見**） |
| 模型服務 | `oc delete pod`，滾動更新 |
| DSPA | 重啟 pipeline server |
| 正在跑的 pipeline | ❌ **會失敗，沒有辦法** |

**所以輪替要排時間，不能隨手做。**

安全的順序（**舊金鑰先別停用**）：

1. 建新金鑰，**兩把都有效**
2. 更新 Secret
3. 依序重啟各個消費者
4. **確認沒有東西還在用舊的**（看 S3 的存取紀錄）
5. 停用舊金鑰

**第 4 步是關鍵，也是最常被跳過的。**
沒有第 4 步，第 5 步就是在賭。

---

## ⭐ 我真的輪替了一次——三個發現

lab 上把 MinIO 的憑證從 `minioadmin` 換成一組新的，全程實測。

### 發現一：改了 Secret，跑著的東西完全沒感覺

改完兩個 Secret 之後立刻去問各個容器：

| 誰 | 改 Secret 之後 | 重啟之後 |
|---|---|---|
| workbench | `minioadmin`（**舊的**） | `mlops-2026q3` ✓ |
| pipeline server | `minioadmin`（**舊的**） | `mlops-2026q3` ✓ |
| 模型服務 | **根本沒有這個環境變數** | — |

第三列是意外收穫：**KServe 的模型容器裡沒有 S3 憑證**——
它的 `storage-initializer` 從 ServiceAccount 的 `secrets` 清單取，
主容器只拿到已經下載好的檔案。**所以模型服務不需要為了輪替重啟**，
但**下一次它重啟時**（換版、節點重開）會用新的——那時才是驗證的時機。

### 發現二：重啟的成本比我想的低

| 對象 | 實測 |
|---|---|
| pipeline server（`rollout restart`） | **8 秒** |
| 模型服務（刪 pod 重抓 5.4 MB＋99 MB） | 15 秒 |
| workbench（刪 pod） | **12 秒**，⚠️ 但**未存檔的東西會不見** |

**真正的成本不是秒數，是 workbench 那一列**——
那是別人正在工作的環境。**輪替要跟使用者約時間，不是半夜偷偷做。**

### 發現三：⚠️ 如果服務用的是 root 帳號，輪替永遠做不完

我照自己的建議走安全順序：先建新憑證、更新 Secret、逐一重啟、
**最後停用舊憑證**。

然後卡在最後一步：

```
mc admin user disable <old>
# mc: <ERROR> Unable to disable user. Access Denied.
```

因為舊憑證是 **MinIO 的 root**，而新憑證只有 `readwrite`——
**權限不夠停用 root，而 root 本來也不該被停用。**

> **用 root／管理員帳號當服務憑證，代價不是「權限太大」這麼抽象。**
> **代價是你永遠走不完輪替的最後一步——舊金鑰會一直有效。**
>
> 而那一步正是輪替唯一真正有意義的部分：**外洩的那把要失效。**

所以這件事要在**發第一把金鑰的時候**就做對：
**服務用專屬帳號，不要用 root。** 事後補要停機。

---

## 怎麼確認做對了

| | 檢查 | 怎麼看 |
|---|---|---|
| 1 | 兩份清單對得上 | 步驟一 |
| 2 | **image 裡沒有憑證** | `skopeo inspect --config` 看 Env |
| 3 | notebook 裡沒有 | grep 一次 `.ipynb` |
| 4 | 誰能讀 Secret | `oc auth can-i get secrets --as=alice -n <ns>` |
| 5 | ⭐ **輪替演練過** | 見下 |

第 4 項先跑一次，結果可能會讓你意外：

```bash
oc auth can-i get secrets --as=alice -n team-a
# yes        ← alice 只有 team-a 的 edit
```

**給 `edit` 就等於給了讀那個 namespace 所有 Secret 的權限。**
「資料科學家只要 edit 就夠」（Day 21）和「誰能看到憑證」是同一個決定，
**不是兩件事。**

第 2 項的指令：

```bash
skopeo inspect --config docker://<image> | jq -r '.config.Env[]'
```

第 5 項：**在非正式環境完整換一次金鑰，記錄花了多久、斷了什麼。**
**沒演練過的輪替，等於沒有輪替能力**——
而外洩事故發生時，第一件要做的事就是輪替。

我自己演練的結果在上面：**技術上的秒數不是問題，
問題是「有沒有人正在用」和「舊的停不停得掉」。**

---

## 常見問題

**Q：Connection 用的 Secret 安全嗎？**
A：它就是普通的 Secret，**保護程度取決於你的 RBAC**。
`opendatahub.io/dashboard` label 讓它出現在 UI 上，
**這表示所有能看那個 project 的人都看得到它存在**（值還是要有權限才讀得到）。

**Q：可以用 External Secrets Operator 嗎？**
A：可以，而且**在有 vault 的環境建議這樣做**。
它把外部 vault 的內容同步成 k8s Secret，
對平台來說沒有差別，但憑證的真實來源在叢集外。

**Q：workbench 裡的使用者能看到 Secret 嗎？**
A：注入成環境變數的話，**能**——`os.environ` 就讀得到。
這是設計如此（他們要用那把金鑰），
**但這也表示：能進 workbench 的人就有那把金鑰。**
給 workbench 的憑證要用最小權限的那一把。

**Q：pipeline log 會不會印出憑證？**
A：會，如果你的程式印了。
**而 log 會留在叢集上、可能被集中收走。**
養成習慣：任何 `print` 之前想一下裡面有沒有 `os.environ`。

---

**你們的 S3 金鑰多久換一次？換的時候要重啟哪些東西，講得出來嗎？**

{% include lab-env.html %}
