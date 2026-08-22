---
layout: post
title: "OpenShift AI 離線安裝：先寫驗收清單，再看廠商怎麼裝"
date: 2026-09-01 09:00:00 +0800
tags: [openshift-ai, rhoai, mlops, disconnected, acceptance, oc-mirror]
excerpt: "當你是驗收的人而不是安裝的人，手冊的本體是驗收清單。版本對位、離線鏡像、七層檢查——每條一個指令、一個預期輸出、一種證據。"
---

> 草稿狀態：本文的檢查項目來自 Red Hat 公開文件（OpenShift AI Self-Managed 3.4《Installing and uninstalling OpenShift AI Self-Managed in a disconnected environment》與 3.2 Release notes）。lab 實跑與截圖補上後才發布。

## 為什麼是驗收清單，不是安裝手冊

安裝通常是廠商做的。你要做的是在它裝完之後回答一個問題：**裝對了嗎？** 這個問題沒有指令和預期輸出就答不了，所以手冊的本體是驗收清單，安裝步驟只是讓你看得懂廠商在做什麼。

原則只有一條：每一條驗收＝一個指令或一張畫面＋預期輸出＋證據形式。不收口頭。

## 先對版本：OpenShift 4.20 配哪個 OpenShift AI

| 候選 | 文件寫的支援範圍 | 意義 |
|---|---|---|
| OpenShift AI 3.4 | OpenShift 4.19–4.20；llm-d 分散推論要 4.20+ | 與 4.20 對位。3.x 自 3.0 起**不能從 2.25 升級**——是新裝專用；新 PoC 正好 |
| OpenShift AI 2.25 | 4.16 以上 | 2.x 末代；KServe Serverless 自 2.25 起 deprecated、ModelMesh 自 2.19 deprecated。裝了之後要重裝 3.x |

3.x 跟 2.x 的差別會直接反映在驗收清單：依賴 operator 從 Authorino／Service Mesh／Serverless 換成 **cert-manager**（KServe、Kueue、llm-d 都要）與 **Kueue**；KServe 預設 RawDeployment；Accelerator Profile 換成 **Hardware Profile**；CodeFlare operator 移除。

## 叢集門檻（文件原文，先對這個）

- 至少 2 個 worker，各 8 CPU／32 GiB；單節點要 32 CPU／128 GiB。
- 預設 storage class 要能動態配置（`oc get storageclass` 看 `(default)`）。
- 要設身分提供者，而且要用 cluster-admin 帳號，**不能用 kubeadmin**。
- S3 相容物件儲存是必要的：單一模型服務平台存模型、AI pipelines 存 artifact 都要；model registry 另要外部 MySQL。
- 叢集上**不能**裝 Open Data Hub。
- 離線鏡像用 **oc-mirror v2**（v1 已 deprecated），鏡像集「約 75 GB」，明顯少就是沒抓完。

## 驗收清單：七層

### L0 叢集前置（廠商裝之前）

| 檢查 | 指令 | 預期 |
|---|---|---|
| OCP 版本 | `oc get clusterversion` | 4.20.x |
| worker 規格 | `oc describe node`（Capacity） | ≥2 worker、各 ≥8 CPU／32 GiB |
| 預設 storage class | `oc get storageclass` | 有 `(default)` 且可動態配置 |
| 身分提供者、非 kubeadmin | `oc whoami`、`oc get oauth cluster -o yaml` | 非 `kube:admin` |
| S3 | 任一 S3 client 建 bucket、put／get | 成功 |
| 沒有 ODH | `oc get csv -A \| grep -i opendatahub` | 空 |
| 鏡像 registry 可拉、叢集信任憑證 | `oc get imagedigestmirrorset`；`oc debug node -- crictl pull …` | 拉得到 |

### L1 鏡像

| 檢查 | 指令 | 預期 |
|---|---|---|
| ImageSetConfiguration | 廠商提供檔案 | catalog 對位叢集版本；`rhods-operator` 釘 `minVersion=maxVersion`；相依 operator 全列；runtime／workbench image 在 `additionalImages` |
| 鏡像集大小 | `du -h --max-depth=1 <mirror>` | 約 75 GB |
| 叢集資源 | `ls <mirror>/working-dir/cluster-resources/`；`oc get imagedigestmirrorset,catalogsource -A` | IDMS＋CatalogSource 存在 |
| CatalogSource pod | `oc get pods -n openshift-marketplace` | Running |
| **digest 清單交付** | 廠商提供全部 `name@sha256` | 可逐顆對 registry |

### L2 Operator 與 DataScienceCluster

| 檢查 | 指令 | 預期 |
|---|---|---|
| 相依 operator | Installed Operators；`oc get csv -A` | cert-manager、NFD、GPU operator（依範圍再加 Kueue、Service Mesh 3.x） |
| DSC | `oc get dsc default-dsc -o yaml` | `Phase: Ready`；沒開的元件 `managementState: Removed` |
| Dashboard | 登入、看元件版本表 | 與 DSC 一致 |
| Route／DNS | `oc get route -n redhat-ods-applications` | 可解析（CRC／私有雲文件明說要手動設 DNS） |

### L3 GPU

| 檢查 | 指令 | 預期 |
|---|---|---|
| NFD＋GPU operator＋KMM | Installed Operators | 三個都 Succeeded |
| 節點看到 GPU | `oc describe node <gpu-node>` | `Capacity: nvidia.com/gpu: N` |
| Hardware Profile | Settings → Hardware profiles | 有對應 GPU 的 profile（Accelerator Profile 已 deprecated） |
| 真的用到 | pod `resources.limits["nvidia.com/gpu"]` 或 `nvidia-smi` | 配到且跑得動 |

### L4 功能

| 檢查 | 指令 | 預期 |
|---|---|---|
| workbench | dashboard → Workbenches；`oc get pod -o jsonpath='{.spec.containers[*].image}'` | Running，image 來自私有 registry |
| 模型從 S3 上線 | `oc get inferenceservice`；curl 推論端點 | READY=True、回應正確 |
| image 釘 digest | `…jsonpath='{.status.containerStatuses[*].imageID}'` | `@sha256:` 且在 L1 清單內 |
| pipeline（若開） | run 一條 | Succeeded，artifact 在 S3 |
| model registry（若開） | 登錄→部署 | 有 lineage、可部署 |

### L5 治理

| 檢查 | 做法 | 預期 |
|---|---|---|
| 誰能做什麼 | 管理員／使用者群組對應目錄服務群組 | 明確 |
| 鏡像清單＝實際拉取 | L1 清單 vs 全叢集 `imageID` | 全在清單內 |
| 鏡像 CVE | 用既有掃描器掃 L1 清單 | 報表 |
| 模型身份 | InferenceService 指向的模型檔 sha256 | 能回答「線上這顆是哪顆」 |
| as-code 交付 | ImageSetConfiguration、Subscription、DSC、Hardware Profile、ServingRuntime、InferenceService 全部 YAML | 進 git |

### L6 可重現

照 L5 的 YAML 重裝一次，計時。不用口頭補充就裝得起來，才算驗收完。

## 在筆電上能練哪些

OpenShift AI 本體需要訂閱與規格（單節點 32 CPU／128 GiB），CRC 裝不了；上游 Open Data Hub 元件相同，可以在 CRC 上練 L2／L4。GPU 那層只能在真叢集驗。離線鏡像可以在家用 oc-mirror 對 Open Data Hub 練一次流程——但鏡像集以 GB 計，網路要夠。

（lab 實跑截圖與踩到的坑，待補。）
