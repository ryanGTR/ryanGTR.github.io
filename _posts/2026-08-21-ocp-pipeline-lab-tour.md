---
layout: post
title: "用 CRC 在筆電上重現一條企業 OpenShift 交付鏈"
date: 2026-08-21 18:00:00 +0800
tags: [openshift, tekton, harbor, jenkins, gitops, lab]
excerpt: "Jenkins 帶參數建置 → Tekton 11 個 task → Harbor → GitOps repo，加上變更單號閘門與人工放行。一台有 CRC 的機器，make up，十五分鐘。"
---

## 為什麼要重現

企業裡的交付鏈通常是這樣的：開發者在 Jenkins 按「帶參數建置」，填一個變更單號；Jenkins 丟給 OpenShift 上的 Tekton；
Tekton 跑十來個 task 把 image 推進 Harbor；最後改一個 GitOps repo，等 OP 在 Argo 按 Sync。

問題是，**用的人看不到中間**。Jenkins 綠了就當成功，出事不知道先看哪，image 有三個 tag 不知道是不是同一顆。
而生產環境不能讓你按壞。

所以我把它搬到筆電上。同樣的參數名、同樣的 task 順序、同樣的 Harbor 待審區與手動 replication、同樣「Argo 不自動 sync」。
差別只有一個：按壞了 `make up` 重來。

## 長什麼樣

![overview](https://raw.githubusercontent.com/ryanGTR/ocp-pipeline-lab/main/docs/overview.png)

| 角色 | 用什麼 |
|---|---|
| 叢集 | CRC（OpenShift Local）4.22 ＋ OpenShift Pipelines operator |
| Git | Gitea（代替 GitLab）：`demo-node`（程式）、`demo-gitops`（部署宣告） |
| Registry | Harbor 2.15，rootless podman-compose；project `demo` 與待審區 `demo-tmp`，手動 replication rule |
| 入口 | Jenkins，JCasC；job `node_build_pipeline`（`tkn pipeline start`）與 `scale_deployment`（`oc scale` 代理） |
| Pipeline | Tekton，task 改自 IBM Cloud-Native Toolkit v2.7.7 |

一次 build：

```
gate → setup → test → dockerfile-lint → build → deploy → health → tag-release → img-release → img-scan → helm-release → gitops
```

`gate` 是加的：變更單號格式不對（不是 `CRQ` + 12 碼）就整條不跑。其他十一個照上游。

## 三個驗收題

這個 lab 的設計目標就是讓用的人能回答這三題：

1. 從 Jenkins 按一次 build，在 OCP console 找到 PipelineRun，說出它卡在哪個 task。
2. 在 Harbor 找到剛建的 image，說出它有幾個 tag、為什麼 digest 一樣。
3. 說出「Jenkins 綠了但沒上線」要先看哪三個地方。

第三題的答案牽涉 [`--showlog` 假綠]({% post_url 2026-08-21-tkn-showlog-false-green %})，
第二題牽涉放行流程：pipeline 只能把 image 放到 `demo-tmp`，管理員手動觸發 replication 搬進 `demo`，
結果是**三個 tag（git sha、時間戳、變更單號）、一個 digest**。放行換的是名字，不是內容。

## 怎麼跑

```bash
git clone https://github.com/ryanGTR/ocp-pipeline-lab && cd ocp-pipeline-lab
make preflight                  # 缺什麼、怎麼補
cp lab.env.example lab.env
make up                         # gitea → harbor → ocp → jenkins → check
make build                      # Jenkins 觸發一次，等 Tekton 12/12
make build PUNCH=CRQ123         # 故意填錯
make promote SRC=<git sha> PUNCH=CRQ000000000200
```

有 CRC 的人大約十五分鐘看到第一條綠的；從零開始約一小時，多數時間在等 CRC 啟動與拉 image。
每支 script 都可以重跑。

## 踩過的坑

做這個 lab 撞了二十一個坑，每個都會讓人多花半天：rootless Harbor 的 `log` service、JCasC 的 `readFileFromWorkspace`、
容器裡解不到 `api.crc.testing`、toolkit 的 registry 啟發式、[buildah 的 privileged]({% post_url 2026-08-21-buildah-without-privileged-on-openshift %})……
全部在 repo 的 [docs/pitfalls.md](https://github.com/ryanGTR/ocp-pipeline-lab/blob/main/docs/pitfalls.md)，每條附解法。

## 它不是什麼

- 不是生產架構。HTTP 的 Harbor、寫在 `lab.env` 的密碼、`insecure-skip-tls-verify`——全是為了在筆電上十五分鐘跑起來。
- 沒有 Argo CD。lab 到「gitops repo 有新 commit」為止，因為重點是 pipeline 這一段；Argo 那一段在真實環境是 OP 按 Sync。
- 不含任何特定組織的設定。task 名字、namespace、參數名都是通用的。

repo：[github.com/ryanGTR/ocp-pipeline-lab](https://github.com/ryanGTR/ocp-pipeline-lab)
