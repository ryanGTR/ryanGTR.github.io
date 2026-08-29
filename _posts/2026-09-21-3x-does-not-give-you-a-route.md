---
layout: post
title: "3.x 不會幫你開對外入口：「上線了」和「打得到」是兩件事"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-21 09:00:00 +0800
tags: [openshift-ai, kserve, route, networking, acceptance]
excerpt: "ISvc 顯示 READY=True，你拿到的網址卻是 .svc.cluster.local。這是 2.x 換到 3.x 之後最容易忽略的一個差別，而它會在驗收當天才爆出來。"
feedback_question: "你們的模型端點是誰開的？平台自動生的，還是網路組另外開的？"
---

## 症狀

模型部署完，`oc get isvc` 顯示綠的：

```bash
oc get isvc llm-scratch -n llm-serve-demo
# NAME          URL                                                            READY
# llm-scratch   http://llm-scratch-predictor.llm-serve-demo.svc.cluster.local   True
```

**`READY=True`。** 然後你把那個 URL 給前端工程師，他說連不到。

看清楚那個網址：**`.svc.cluster.local`**——那是叢集**內部**位址。
從叢集外面（你的筆電、前端伺服器、API gateway）打不到。

---

## 為什麼 2.x 沒有這個問題

| | 2.x | 3.x |
|---|---|---|
| KServe 預設模式 | **Serverless**（Knative） | **RawDeployment** |
| 對外路由 | **Knative 幫你生** | **你自己開** |
| 自動縮至零 | 有 | 沒有 |

2.x 的 Knative 會連帶處理 ingress gateway 與對外網址，所以「部署完就打得到」。

**3.x 把 Knative 那層拿掉了**——好處是排錯回到熟悉的 `oc get deploy/svc`，
不用先學 Knative；代價是**路由與伸縮要自己接**。

⚠️ 所以照 2.x 教學做的人，會在這裡卡住而且找不到原因——
因為那些教學根本沒有「開 Route」這一步。

---

## 怎麼開

一行：

```bash
oc create route edge llm-play \
  --service=llm-scratch-predictor \
  --port=8000 \
  -n llm-serve-demo
```

`edge` 是 TLS 終止在 Route 這一層（叢集內走 HTTP）。
其他選項：`passthrough`（TLS 直通到 pod）、`reencrypt`（重新加密）。

確認：

```bash
oc get route llm-play -n llm-serve-demo
# NAME       HOST/PORT                                        SERVICES
# llm-play   llm-play-llm-serve-demo.apps-crc.testing         llm-scratch-predictor
```

⚠️ **`--port` 要填容器實際開的 port，不是慣例值。**
我的服務開在 **8000**（不是 8080），填錯的話 Route 建得起來但打不通。

---

## ⭐ 這個 Route 沒有 owner

```bash
oc get route llm-play -o jsonpath='{.metadata.ownerReferences}'
# （空的）
```

**它不是 KServe 生的，是我自己建的。**

這一點有兩個實際後果：

**① 刪掉 ISvc，Route 不會跟著消失。**
沒有 ownerReference 就沒有連帶刪除。你會留下一個指向不存在服務的 Route——
打過去得到 503，而且沒人知道它為什麼還在。

**② 它不在任何 GitOps 或備份的自動涵蓋範圍內**，除非你自己把它寫進去。

> **所以 Route 要跟 ISvc 放在同一份 manifest 裡管理**，不要用 `oc create` 隨手開。
> 隨手開的東西，會變成三個月後沒人敢刪的東西。

---

## 還有一個假網址要小心

ISvc 的 status 裡有兩個 URL：

```bash
oc get isvc llm-scratch -o jsonpath='{.status.address.url}'
# http://llm-scratch-predictor.llm-serve-demo.svc.cluster.local   ← 內部，真的

oc get isvc llm-scratch -o jsonpath='{.status.components.predictor.url}'
# http://llm-scratch-predictor-llm-serve-demo.example.com          ← 假的
```

第二個看起來像對外網址，但 **`example.com` 是 KServe 的預設 domain 沒被設定**
留下的佔位值。**拿它去打會 DNS 解不到。**

不要因為「status 裡有一個看起來像外部的 URL」就以為平台幫你開好了。

---

## 關鍵指標

| | 「跑完了」 | ⭐「做對了」 |
|---|---|---|
| 對外入口 | `oc get isvc` READY=True | **從叢集外面 `curl` 得到回應** |

**驗收就是這一句：從你自己的筆電打一次。**
不要在 pod 裡打、不要用 port-forward、不要看截圖。

```bash
curl -sk https://llm-play-llm-serve-demo.apps-crc.testing/health
```

---

## 給驗收的提醒

這一項特別值得單獨列一條，因為它有一個很討厭的特性：

**在 lab 或 demo 環境，大家習慣用 `oc port-forward` 測試——那會通。
所以這個問題可以一路藏到正式環境上線當天。**

而上線當天發現「還要跟網路組申請對外入口」，
通常代表你要多等一個變更視窗。

---

**你們的模型端點是誰開的？平台自動生的，還是網路組另外開的？**

{% include lab-env.html %}
