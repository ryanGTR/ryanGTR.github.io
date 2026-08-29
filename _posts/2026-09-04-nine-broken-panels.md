---
layout: post
title: "監控上線了，九個面板全是壞的"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-04 09:00:00 +0800
tags: [grafana, prometheus, monitoring, openshift-ai, mlops, false-green]
excerpt: "Grafana pod Running、Prometheus target up、dashboard 開得起來——而九個面板一個資料都查不到。根因是一個沒填的欄位。"
feedback_question: "你們的 Grafana datasource 是 provisioning 進去的嗎？有顯式指定 uid 嗎？"
---

昨天我把 Prometheus + Grafana 用 kustomize 部進 OpenShift，
記錄裡寫著「監控已上線」。

今天要截圖，打開 dashboard——**九個面板全是空的。**

## 每一個檢查都是綠的

`oc get pods -n llm-serve-demo | grep -E "grafana|prometheus"
# grafana-6d67df5797-gqkcc      1/1  Running
# prometheus-864787c4d-vx2jr    1/1  Running
`

Prometheus 有在抓：

`curl -s localhost:19090/api/v1/targets?state=active
# llm-api   up
curl -s --get localhost:19090/api/v1/query --data-urlencode 'query=llm_requests_total'
# 4 個序列
`

Dashboard 也在：

`curl -sk https://grafana-.../api/search
# [{"uid":"llm-serving","title":"LLM 推論服務監控",...}]
`

**pod 綠的、target 綠的、資料有的、dashboard 在的。而面板是空的。**

## 根因：一個沒填的欄位

我的 datasource 是 provisioning 進去的：

`apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
`

看起來沒問題。但**沒有 uid**。

Grafana 會自動幫它產一個：

`curl -sk https://grafana-.../api/datasources | jq '.[0].uid'
# "PBFA97CFB590B2093"
`

而我的 dashboard JSON 裡，**20 處都寫死了**：

`{"type": "prometheus", "uid": "prometheus"}
`

prometheus ≠ PBFA97CFB590B2093。每一個 panel 都在找一個不存在的 datasource。

驗證：

`curl -sk -X POST https://grafana-.../api/ds/query -H 'Content-Type: application/json' \
  -d '{"queries":[{"refId":"A","datasource":{"type":"prometheus","uid":"prometheus"},
       "expr":"llm_requests_total","instant":true}]}'
# {"message":"Data source not found"}
`

## 修法

一行：

`  - name: Prometheus
    type: prometheus
    uid: prometheus          # ← 顯式指定，不要讓它自動產
`

重啟 Grafana，uid 變成 prometheus，查詢回 200，九個面板全部有資料。

## 為什麼這個坑特別陰

因為它**壞在呈現層，而所有健康檢查都在資料層**。

  - Prometheus 在抓 → 綠
  - 指標有值 → 綠
  - Grafana 活著 → 綠
  - Dashboard 存在 → 綠
  - **有沒有人真的看過那個面板 → 沒有任何檢查在問這件事**

這是我在這條鏈上收集到的第四種「假綠」：

| 形態 | 為什麼騙得過人 |
|---|---|
| 1 | log 印 ALL DONE 但沒跑 → 看 log 不看產物時間戳 |
| 2 | gate 印 SUCCEEDED 但門檻被調鬆 → 看結果不看誰填的門檻 |
| 3 | 平台全綠但服務死的 → 檢查的範圍小於系統的範圍 |
| **4** | **監控上線但面板全壞 → 檢查在資料層，壞在呈現層** |

## 拿去驗收

**「監控上線了」這句話要拆成三個問題：**

  - 指標**有沒有被抓**？→ targets 是不是 up
  - 指標**有沒有值**？→ 直接查一個 query
  - **面板有沒有畫出來**？→ **打開它，看一眼**

前兩個可以自動化，第三個目前只能用眼睛。而它是唯一一個「壞了會被使用者發現」的。

> 一個沒有人打開過的 dashboard，跟沒有 dashboard 的差別，
> 只在於前者讓你以為自己有在監控。

**你們的 Grafana datasource 是 provisioning 進去的嗎？有顯式指定 uid 嗎？**

🧪 這篇的實驗環境（最後更新 2026-08-29）

**叢集**

  - CRC 2.63.0 · OpenShift **4.22.7** · Kubernetes v1.35.6
  - 單節點：**13 vCPU / 40 GiB RAM / 120 GB disk**（宿主為 16 核 / 62 GB 的筆電）
  - ⚠️ **叢集內看不到 GPU**（CRC 是 VM，RTX 5070 未 passthrough）

**Operator**

  - `opendatahub-operator.v3.5.0` ← 即 RHOAI 3.x 的上游開源版
  - `cert-manager-operator.v1.20.0`（3.x 的必要相依；2.x 不需要）
  - `openshift-pipelines-operator-rh.v1.23.2`、`openshift-gitops-operator.v1.21.3`

**DataScienceCluster 開啟的元件**

  - `kserve`、`aipipelines`、`dashboard`、`workbenches`、`modelregistry`、`kueue`（Unmanaged）
  - 其餘（`ray`／`trustyai`／`feast`／`aigateway`…）為 `Removed`

**叢集外的依賴**（跑在宿主的 podman 上，`crc start` 不會帶起來）

  - Harbor v2.15.2（私有 registry）· MinIO（S3）

**模型端**

  - Python 3.12.13 · PyTorch **2.11.0+cu128** · FastAPI + uvicorn · prometheus-client

⚠️ **ODH ≠ RHOAI**：元件同源，但 **namespace 與部分名稱不同**
（我這裡是 `opendatahub`，商用版是 `redhat-ods-*` 那一套）。
**指令的邏輯可以照用，字串要自己對一次。**

{% include lab-env.html %}
