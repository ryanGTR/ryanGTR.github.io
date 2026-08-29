---
layout: post
title: "容器裡沒有 curl，而且 port 不是 8080"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-05 09:00:00 +0800
tags: [kserve, openshift-ai, troubleshooting, debugging]
excerpt: "兩個會讓你在 KServe 上浪費半小時的小事。都不難，但都不會寫在教學裡。"
feedback_question: "你們排錯時是用 oc exec 還是 port-forward？有沒有在容器裡裝除錯工具的政策？"
---

模型上線之後，第一件事是打它一發看看。這兩個坑我各花了十分鐘。

---

## 一、`oc exec ... curl` 會告訴你 curl 不存在

```bash
oc exec -n llm-serve-demo deploy/llm-scratch-predictor -c kserve-container -- \
  curl -s localhost:8080/model
```
```
executable file `curl` not found in $PATH: No such file or directory
command terminated with exit code 1
```

不是路徑問題，是**容器裡真的沒有 curl**。

現代的 serving image 大多是 distroless 或 minimal base，
連 `curl`、`wget`、`ps`、`netstat` 都沒有。這是**刻意的**——
少一個二進位就少一個 CVE，也少一個攻擊者能用的工具。

**正確做法是從外面打，不要進去打：**

```bash
oc port-forward -n llm-serve-demo deploy/llm-scratch-predictor 18000:8000 &
curl -s localhost:18000/model
```

（如果你真的需要在 pod 內排錯，OpenShift 有 `oc debug` 可以起一個帶工具的
副本，但那會是另一個容器，網路命名空間不同，要注意你在測的到底是誰。）

---

## 二、port 是 8000，不是 8080

我照慣例打 8080，port-forward 說連上了：

```
Forwarding from 127.0.0.1:18080 -> 8080
```

然後每一個請求都失敗：

```
error: an error occurred forwarding 18080 -> 8080:
  failed to connect to localhost:8080 inside namespace ...:
  dial tcp [::1]:8080: connect: connection refused
```

**這個錯誤訊息會誤導你**，因為 `Forwarding from ...` 那行看起來像成功了。
port-forward 只是建立了轉發通道，它不驗證對面有沒有人在聽。

去看容器實際開的 port：

```bash
oc get deploy llm-scratch-predictor -o jsonpath='{.spec.template.spec.containers[0].ports}'
# [{"containerPort":8000,"protocol":"TCP"}]
```

**8000。** 8080 是 KServe 生態的慣例值，但那是慣例不是規定——
自訂容器要開哪個 port 是你自己在 YAML 裡寫的。

改成 8000 就通了：

```bash
oc port-forward -n llm-serve-demo deploy/llm-scratch-predictor 18000:8000 &
curl -s localhost:18000/model | jq
```
```json
{"serving_digest":"sha256:4d694be9342d…","in_registry":true,"status":"production",
 "metrics":{"test_loss":3.462,"perplexity":31.88,"test_bpc":4.9946}}
```

---

## 一分鐘的檢查清單

打不到自己的模型服務時，照順序：

```bash
# 1. pod 活著嗎（不是 Running 就先看 Init 或 CrashLoop）
oc get pods -l serving.kserve.io/inferenceservice=<name>

# 2. 容器開哪個 port（不要假設 8080）
oc get deploy <name>-predictor -o jsonpath='{.spec.template.spec.containers[0].ports}'

# 3. 應用自己說它 listen 在哪
oc logs deploy/<name>-predictor -c kserve-container --tail=5
# INFO: Uvicorn running on http://0.0.0.0:8000

# 4. 從外面打
oc port-forward deploy/<name>-predictor 18000:<真正的port> &
curl -s localhost:18000/health
```

第 3 步最可靠——**應用啟動時通常會自己印出來它在聽哪個 port**，
那比任何設定檔都準。

---

**你們排錯時是用 `oc exec` 還是 port-forward？有沒有在容器裡裝除錯工具的政策？**

{% include lab-env.html %}
