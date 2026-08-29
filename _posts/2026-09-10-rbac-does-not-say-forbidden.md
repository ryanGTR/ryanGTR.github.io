---
layout: post
title: "RBAC 缺權限不會回 Forbidden，它會讓 controller 逾時自殺"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-10 09:00:00 +0800
tags: [openshift-ai, kserve, rbac, troubleshooting, kubernetes]
excerpt: "兩個 controller 冷啟動後 CrashLoopBackOff，log 停在一行 cache sync 逾時。看起來像網路慢，實際上是權限。這個錯誤訊息我在網路上一筆都搜不到。"
feedback_question: "你有沒有遇過「錯誤訊息完全指向錯的方向」的 k8s 問題？最後是怎麼找到根因的？"
---

冷啟動之後，DSC 說 KServe 沒就緒：

```
kserve-controller-manager    0/1  CrashLoopBackOff  10 (5m ago)   4d4h
llmisvc-controller-manager   0/1  CrashLoopBackOff   5 (2m ago)   4d4h

DSC: Kserve False (DeploymentNotReady)
ISvc llm-scratch READY=False      ← 但舊 pod 還在跑，推論打得通
```

（順帶一提最後那行：**舊的 predictor pod 還活著，服務照樣有回應**。
所以「服務能打」不代表「平台是健康的」——這件事我另外寫過。）

---

## log 停在這一行

兩個 controller 的 log 都停在同一個地方：

```
failed to wait for tls-profile-watcher caches to sync kind source:
*v1.APIServer: timed out waiting for cache to be synced for Kind *v1.APIServer
```

**我在網路上搜不到這一行。** KServe 的 GitHub issue 裡有 controller CrashLoop 的案例，
但都是別的原因（`inferencegraph` 的 cache sync）。

第一眼看起來像什麼？**像網路問題、像 API server 慢、像叢集還沒起完。**
所以我一開始的反應是「再等等」，然後等了五分鐘它又 crash 一次。

---

## 根因：ServiceAccount 沒有那個權限

controller 啟動時要 watch `apiservers.config.openshift.io`——
它要讀叢集的 TLS profile 設定。

先確認那個資源存在：

```bash
oc get apiserver
# NAME      AGE
# cluster   30d
```

存在。那就是**權限**：

```bash
oc auth can-i watch apiservers.config.openshift.io \
   --as=system:serviceaccount:opendatahub:kserve-controller-manager
# no
```

**`no`。**

---

## 為什麼它不回 `Forbidden`

這是這篇的重點，也是它難查的原因。

controller-runtime 建立 informer 去 watch 一個資源時，如果 SA 沒權限：

1. watch 請求被 API server 拒絕
2. **informer 不會把這個錯誤往上拋**——它會重試，那是它的設計（網路抖動時要能自己恢復）
3. 於是 cache 永遠 sync 不到
4. manager 等到逾時，**整個 process 退出**
5. Kubernetes 看到容器結束，重啟它，回到第 1 步 → CrashLoopBackOff

**你看到的是「逾時」，而逾時是「權限被拒絕」被重試機制吞掉之後的殘影。**

> 一般寫程式時，權限不足會拿到 403，訊息很明確。
> 但在 controller 這個架構裡，權限錯誤被包在一層「會自動重試的抽象」底下，
> 而那層抽象只會告訴你「我等不到」。

---

## 修法

補一個 ClusterRole 給那兩個 SA：

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kserve-apiserver-config-reader
rules:
- apiGroups: ["config.openshift.io"]
  resources: ["apiservers"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kserve-apiserver-config-reader
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kserve-apiserver-config-reader
subjects:
- kind: ServiceAccount
  name: kserve-controller-manager
  namespace: opendatahub
- kind: ServiceAccount
  name: llmisvc-controller-manager
  namespace: opendatahub
```

`oc apply` 之後**約 40 秒兩個 pod 自動恢復**——CrashLoop 的退避重啟會自己再試一次，
不用手動刪 pod。`DSC Kserve: True`，`ISvc READY=True`。

✅ **這個修法撐得過冷啟動。** 隔天重開機再查，兩個 controller 都正常，
沒有復發——它不是一次性的 workaround。

---

## 可以帶走的除錯順序

`CrashLoopBackOff` 而 log 是「等不到 / 逾時 / timed out」時，**先懷疑權限**：

```bash
# 1. log 停在哪一個資源？（上面那行是 *v1.APIServer）
oc logs <pod> -n <ns> --previous --tail=20

# 2. 那個資源存在嗎？
oc get <resource>

# 3. 這個 SA 有權限嗎？ ← 關鍵這步
oc get pod <pod> -o jsonpath='{.spec.serviceAccountName}'
oc auth can-i <verb> <resource> --as=system:serviceaccount:<ns>:<sa>
```

第 3 步的 `oc auth can-i --as=` 是最被低估的 k8s 除錯指令。
**它讓你不用改任何東西就能問「如果我是它，我做得到嗎」。**

---

## 這對驗收的意義

這類坑是 **operator 的 CSV 權限清單漏了東西**——不是你的設定錯。
在 ODH 3.5 上我遇到三個同類的。

所以驗收清單裡值得加一條：

> **冷啟動之後**（不是安裝完當下）**，逐一確認所有 controller 的狀態。**
> 安裝當下很多權限問題不會浮現，因為那時候該建的都建好了；
> 它們會在**下一次重啟**才爆出來。

而正式環境的「下一次重啟」，通常是某個你不希望它出事的時間點。

---

**你有沒有遇過「錯誤訊息完全指向錯的方向」的 k8s 問題？最後是怎麼找到根因的？**

{% include lab-env.html %}
