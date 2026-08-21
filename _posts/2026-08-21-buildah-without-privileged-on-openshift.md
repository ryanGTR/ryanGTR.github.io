---
layout: post
title: "OpenShift 上的 Tekton buildah 不需要 privileged：SCC、SETFCAP 與 vfs"
date: 2026-08-21 14:30:00 +0800
tags: [tekton, openshift, buildah, security]
excerpt: "IBM Cloud-Native Toolkit 的 build task 預設 privileged: true。在沒有 privileged SCC 的 SA 上會 PodAdmissionFailed；其實只要 SETFCAP 加 vfs storage driver 就能跑。"
---

## 現象

用 [IBM Cloud-Native Toolkit](https://github.com/IBM/ibm-garage-tekton-tasks)（v2.7.7）的 `ibm-build-tag-push` 跑 build，
PipelineRun 停在 `build`，狀態 `PodAdmissionFailed`：

```
pods "node-pipeline-run-xxxxx-build-pod" is forbidden: unable to validate against any security context constraint:
  provider "pipelines-scc": .containers[0].privileged: Invalid value: true: Privileged containers are not allowed
```

看 task 的定義就知道為什麼：

```yaml
- name: build
  image: quay.io/buildah/stable:v1.15.0
  securityContext:
    privileged: true
```

上游 v2.7.7 裡 `privileged: true` 的 task：`build-tag-push`、`image-release`、`img-scan`（trivy）、`img-scan-trivy`、`operator-*`、`build-tag-push-ace-bar`。
前三個就在一條標準 pipeline 的路徑上。

## 為什麼上游這樣寫

buildah 在容器裡建 image 要做幾件平常容器不給做的事：建 user namespace、掛 overlay、設 file capabilities。
最省事的解法是 `privileged: true`，一次拿到全部。2020 年左右的 toolkit 就這樣寫了，一直沒改。

在 OpenShift 上這件事由 **SCC（Security Context Constraints）** 管。
OpenShift Pipelines operator 給 pipeline 的 SA 綁的是 `pipelines-scc`——它允許的比 `restricted` 多，但**不含 privileged**。
要跑 privileged 得平台管理員另外綁 `privileged` SCC 給那個 SA，等於把整個 pipeline namespace 的 build 都開成 root 等級。

## 實際需要的只有兩樣

```yaml
- name: build
  image: quay.io/buildah/stable:v1.15.0
  securityContext:
    capabilities:
      add: [SETFCAP]
  env:
    - name: STORAGE_DRIVER
      value: vfs
```

- **`SETFCAP`**：buildah 解開 base image 時要把 file capabilities（例如 `ping` 的 `cap_net_raw`）寫回檔案。沒有這個 capability 會在解 layer 時出錯。`pipelines-scc` 允許加 `SETFCAP`。
- **`STORAGE_DRIVER=vfs`**：overlay 在非 privileged 容器裡需要 fuse-overlayfs 或 kernel 支援 unprivileged overlay，兩者在 pod 裡都不保證有。`vfs` 用純複製，慢但一定能跑。

改完以後 pod 的 annotation 是 `openshift.io/scc: pipelines-scc`，build 正常推進 Harbor。
lab 裡一個 22 MB 的 demo app，build 加 push 約 5 分鐘——vfs 的代價很明顯，每一層都是整份複製。教學 lab 可以接受；真要快就得談 fuse-overlayfs 或 kernel 的 unprivileged overlay。

## skopeo 根本不需要

`image-release` 和 `img-scan` 的 skopeo step 也標了 `privileged: true`。
skopeo 只是 registry 到 registry 的複製，不建 image、不掛任何東西。把 `securityContext` 整段拿掉，照跑。

猜測是當初複製貼上 build task 的模板留下來的。這類「沿襲下來的 privileged」在老 task 庫裡很常見，值得逐一檢查。

## 一個相關的坑：toolkit 的 registry 啟發式

同一批 task 裡還有一段：registry host 含 `:`（有 port）就判定為 OpenShift 內建 registry，**不帶帳密**。
lab 的 Harbor 跑在 `host:8088`，結果 skopeo 匿名去拉 private project，`unauthorized`。
生產環境的 Harbor 通常走 443 沒有 port，不會中；lab 或任何非標準 port 的 registry 就會。
這段判斷在 lab 的 shim 裡直接拿掉了。

## 在 lab 裡看

[ocp-pipeline-lab](https://github.com/ryanGTR/ocp-pipeline-lab) 的 `tekton/tasks/lab-build-tag-push.yaml` 是改過的版本，
`NOTICE` 列了相對上游的每一項改動。想重現原始錯誤：把 `capabilities` 換回 `privileged: true`，`make build`，看 `PodAdmissionFailed`。
