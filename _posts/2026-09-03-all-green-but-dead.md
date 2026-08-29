---
layout: post
title: "平台全綠，但服務是死的"
date: 2026-09-03 09:00:00 +0800
tags: [openshift-ai, rhoai, kserve, troubleshooting, acceptance, monitoring]
excerpt: "冷啟動之後，DSC 說 KserveReady=True，opendatahub 的 pod 零異常，而模型服務是死的。根因在叢集外面——而叢集的健康檢查只看得到叢集裡的東西。"
feedback_question: "你們的 AI 平台有哪些依賴是「不在叢集裡」的？（外部 S3、私有 registry、外掛 DB…）健康檢查看得到它們嗎？"
---

早上開機，照重開手冊查三件事：

```bash
oc get dsc default-dsc                                     # → KserveReady True     ✅
oc get pods -n opendatahub | grep -vE "Running|Completed"   # → 空的，零異常          ✅
oc get isvc -n llm-serve-demo                              # → llm-scratch READY=False ❌
```

**前兩條是平台自己的健康檢查，全綠。第三條才看到服務其實沒活。**

---

## 症狀

```
llm-scratch-predictor-868c589678-48dkf   0/2   Init:Error   5 (79s ago)   14h

PredictorReady  False  MinimumReplicasUnavailable  Deployment does not have minimum availability.
```

掛在 `storage-initializer`——那是 KServe 在你的容器啟動前塞進去的 init container，
負責把模型權重從 S3 下載到 `/mnt/models`。

---

## 根因在叢集外面

我的模型放在 **MinIO**，serving image 放在 **Harbor**。
這兩個都是跑在**主機上的 podman 容器，不在叢集裡**。

`crc start` 只把叢集拉起來。**它不知道那兩個容器的存在。**

```bash
podman ps
# （空的）
```

所以 `storage-initializer` 去抓模型，抓不到，`Init:Error`，重試，再失敗。

> **叢集的健康檢查只看得到叢集裡的東西。**
> DSC 說 KServe 就緒，指的是 KServe 這個元件就緒；
> 它不知道 KServe 要抓的模型在一台沒開機的 S3 上。

---

## 修法（含一個容易漏的步驟）

```bash
podman start minio
podman pod start pod_harbor      # 等 harbor-core 由 starting → healthy

# ⚠️ 這一行不能省
oc delete pod -n llm-serve-demo -l serving.kserve.io/inferenceservice=llm-scratch
```

**光把 MinIO 起來不會自動復原。** init container 只在 pod 啟動時跑一次——
它已經失敗過了，除非 pod 重建，否則不會再試一次成功的路徑。
刪掉讓它重建，30 秒內 `2/2 Running`，ISvc 轉 `READY=True`。

---

## 順便：`Not Ready` 不一定是壞的

修完之後我去看 DSC，它顯示 **`Not Ready`**：

![DSC Conditions](/assets/img/rhoai/dsc-notready.png)

```
Ready            False   NotReady   Some modules are not ready: workbenches
ComponentsReady  True
ModulesReady     False   NotReady   Some modules are not ready: workbenches
AIGatewayReady   False   Removed    Module ManagementState is set to Removed
```

而同一時間，模型服務是好的、推論打得通、監控有資料。

看 reason 就懂了：
- `workbenches` 沒起來——但我根本沒在用 workbench
- `AIGatewayReady False` 的 reason 是 **`Removed`**，意思是**「我沒開」**，不是「它壞了」

**所以最上面那個 `Ready` 是所有模組的 AND。開了不用的模組會把它拉紅，
而關掉的模組也會出現在條件列表裡。** 只看那一格會得到完全錯誤的印象。

---

## 這是「假綠」的第三種形態

我在這條鏈上收集到的：

| 形態 | 長什麼樣 | 為什麼騙得過人 |
|---|---|---|
| 1 | log 印著 `ALL DONE`，但 checkpoint 一步都沒跑 | 看 log，不看產物的時間戳 |
| 2 | gate 印著 `SUCCEEDED`，但只是門檻被調鬆了 | 看結果，不看門檻是誰填的 |
| **3** | **平台健康檢查全綠，但服務是死的** | **檢查的範圍小於系統的範圍** |

第三種最難防，因為前兩種你至少還在看正確的東西。
第三種是**你看的東西本身沒有錯，只是它看不到那麼遠**。

---

## 拿去驗收用

兩條，可以直接寫進清單：

**1.「平台就緒」不等於「服務可用」。**
驗收必須打到端點拿回實際回應——`/model` 加一次真實推論——
不能只看 operator 的 Ready 條件。

**2. 要求對方說明「哪些依賴不在叢集內」。**
外部 S3、私有 registry、外掛資料庫、授權伺服器……
**那些就是健康檢查的盲區**，也是冷啟動、機房停電、網段變更之後第一個壞的地方。

我這個 lab 的盲區是兩個 podman 容器。在正式環境，那可能是一整套儲存設備。

---

**你們的平台有哪些依賴是不在叢集裡的？健康檢查看得到它們嗎？**

{% include lab-env.html %}
