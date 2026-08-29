---
layout: post
title: "llm-d 我跑不起來——但我可以告訴你它需要什麼"
date: 2026-08-31 09:00:00 +0800
tags: [llm-d, openshift-ai, rhoai, kserve, inference, acceptance]
excerpt: "CRD 全都在，但三個前置一個都不滿足。這篇不是教學，是一份「你評估 llm-d 時該問廠商什麼」的清單——而叢集自己就會把缺什麼講出來。"
feedback_question: "你們有在評估 llm-d 或 disaggregated inference 嗎？卡在硬體、授權，還是還沒到那一步？"
---

先講結論：**我的 lab 跑不起 llm-d，而且不是「裝一裝就好」的那種跑不起來。**

但這篇還是值得寫，因為對「要評估一套 AI 平台」的人來說，
**「跑不起來，以及為什麼」比一篇看起來很順的教學更有用**——
它直接變成你問廠商的問題。

---

## 先確認：llm-d 是什麼，以及它只存在於 3.x

[llm-d](https://github.com/llm-d/llm-d) 是 Kubernetes 原生的分散式推論專案，
2026 年 3 月捐給 CNCF，背後是 IBM、Red Hat、Google、CoreWeave、NVIDIA。
它建在 vLLM 上，核心是 **prefill／decode 拆開跑**：

- **prefill**：把整段 prompt 一次算完、產出第一個 token——**計算密集**
- **decode**：一次產一個 token，每次都要完整走一遍模型——**記憶體頻寬密集**

這兩件事的資源特性完全不同，綁在同一個 pod 裡一定有一邊被浪費。
llm-d 讓它們各自獨立擴縮。

**重點是**：這東西在 RHOAI **2.x 完全不存在**。
所以如果你手上的評估文件是 2.x 的，這一整塊你連問都不會問到。

---

## CRD 都在，但一個都用不了

先看好消息，ODH 3.5 的叢集上這些 CRD 是齊的：

```bash
oc get crd | grep -iE "llminference|inferencepool|llm-d"
```
```
inferencemodelrewrites.llm-d.ai
inferenceobjectives.llm-d.ai
inferencepools.inference.networking.k8s.io
inferencepools.inference.networking.x-k8s.io
llminferenceserviceconfigs.serving.kserve.io
llminferenceservices.serving.kserve.io
```

看到這個很容易以為「那就可以用了」。**不行。**

---

## 叢集自己會告訴你缺什麼

這是這篇最有用的一段——**你不需要看文件，DSC 的 condition 直接把前置寫出來**：

```bash
oc get dsc default-dsc -o jsonpath='{range .status.conditions[?(@.type=="KserveLLMInferenceServiceDependencies")]}{.status}{"  "}{.message}{"\n"}{end}'
```
```
False   Red Hat Connectivity Link not installed
```

再看 Wide EP（更大規模的那條路徑）：

```
False   LeaderWorkerSet not installed; Red Hat Connectivity Link (Wide EP) not installed
```

兩個名字都不是隨便取的：

| 前置 | 是什麼 | 為什麼 llm-d 需要它 |
|---|---|---|
| **Red Hat Connectivity Link** | [Kuadrant 的商用版](https://docs.redhat.com/en/documentation/red_hat_connectivity_link/1.0/html/introduction_to_connectivity_link/about-connectivity-link_rhcl)，基於 Kubernetes **Gateway API** 的 ingress 控制平面（TLS／認證／限流／DNS 政策） | llm-d 用 Gateway API Inference Extension 做**推論感知的路由**——它要知道哪個 pod 有你要的 KV cache |
| **LeaderWorkerSet** | Kubernetes 的一種工作負載型別，把一組 pod 管成「一個 leader + N 個 worker」 | 一個模型跨多張卡／多個節點時，那些 pod 要一起生、一起死、一起排程 |

**這兩行就是你的驗收提問稿。** 廠商說要上 llm-d，你不用跟他辯，
把這兩個 condition 叫出來，缺什麼一目了然。

---

## 就算裝起來，我的 lab 還是跑不了

因為還有一個更硬的限制：

```bash
oc get nodes -o jsonpath='{.items[*].status.capacity.nvidia\.com/gpu}'
# （空的）
```

CRC 是一台 VM，我筆電上那張 RTX 5070 沒有 passthrough 進去。
**叢集看不到任何 GPU。**

而且就算把卡穿進去也沒用——**單張 8 GB 的筆記型顯卡，
不在 prefill／decode 拆開跑的射程內**。那個架構要解決的問題，
是「一張卡放不下」和「兩個階段搶同一份資源」，
而我的問題是「只有一張卡」。

**這是硬體層級的限制，不是設定問題。**

---

## 所以評估時該問什麼

把上面整理成一張可以直接帶去會議的表：

| 問題 | 怎麼驗（不用信對方口頭） |
|---|---|
| 我們的 RHOAI 版本支援 llm-d 嗎？ | 2.x 沒有這東西。`oc get crd \| grep llm-d` |
| 前置裝了嗎？ | `oc get dsc -o jsonpath=...` 看那兩個 condition 的 message |
| Connectivity Link 的授權算誰的？ | 那是獨立產品，不是 RHOAI 內含 |
| 要幾張卡、幾個節點？ | prefill／decode 各自要能獨立擴縮，單節點單卡沒有意義 |
| 你們打算用哪條路徑？ | 標準 `LLMInferenceService` vs Wide EP（後者還要 LeaderWorkerSet） |
| 效能宣稱怎麼來的？ | 公開的 70% tokens/sec 提升是在 **B200** 上量的，換硬體就不成立 |

最後一條特別重要。**任何效能數字都綁著它被量出來的那套硬體**——
這跟我前一篇講門檻的邏輯是同一件事：**沒有量測條件的數字不是結論，是廣告。**

---

## 我沒有驗證的部分

誠實標一下，免得你當成結論：

- 我**沒有**實際裝過 Connectivity Link 或 LeaderWorkerSet，
  所以「裝了就會 True」是我的推論，不是實測
- 我的環境是 **ODH 3.5**（上游開源版）不是 RHOAI，
  condition 的名字與 message 在商用版可能不同
- prefill／decode 的效能特性我是**讀來的**，沒有自己量過

---

**如果你也在評估這塊，我想知道：
你們有在看 llm-d 或 disaggregated inference 嗎？
卡在硬體、授權，還是還沒到那一步？**

{% include lab-env.html %}
