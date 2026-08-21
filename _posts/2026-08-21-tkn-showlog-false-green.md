---
layout: post
title: "tkn pipeline start --showlog 會讓 Jenkins 永遠綠"
date: 2026-08-21 17:00:00 +0800
tags: [tekton, jenkins, openshift]
excerpt: "Jenkins 用 tkn 觸發 Tekton 是很常見的組合。--showlog 把 log 串回來很方便，但它不會把 PipelineRun 的失敗變成非零 exit code——Jenkins 看到的永遠是 SUCCESS。"
---

## 現象

Jenkins 的 job 長這樣，很多地方都這麼寫：

```groovy
stage('Trigger Tekton') {
  steps {
    sh 'tkn pipeline start node-pipeline -n tpipeline -s pipeline --showlog \
          -p git-url=$csp_git -p git-revision=$git_revision ...'
  }
}
```

Tekton 那邊 `build` 這一格紅了（推 image 失敗），Jenkins 的 build **SUCCESS**。
Console Output 裡其實看得到錯誤訊息，但沒人會去看綠色 build 的 log。

我在 lab 裡重現它只花了一次：把 Harbor 的 TLS 設定弄錯，Tekton 的 `build` task 在 `buildah login` 就死了，
Jenkins 照樣綠。

## 原因

`tkn pipeline start --showlog` 做兩件事：建 PipelineRun，然後**跟著串 log**。
它的 exit code 反映的是「log 串完了沒」，不是「PipelineRun 成功了沒」。
PipelineRun 失敗時 log 一樣會串完，所以 exit 0。

這不是 bug 而是設計：`tkn pipeline start` 是「啟動」指令，不是「等結果」指令。
但任何把它包進 CI 的人，直覺都會以為綠 = 成功。

## 修法

從 `tkn` 的輸出抓 PipelineRun 名字，再自己問 Kubernetes：

```groovy
sh '''tkn pipeline start node-pipeline -n tpipeline -s pipeline --showlog \
        --use-param-defaults -p ... 2>&1 | tee tkn.log'''
sh '''PR=$(sed -n "s/^PipelineRun started: //p" tkn.log | head -1)
      ST=$(oc get pr "$PR" -n tpipeline \
           -o jsonpath="{.status.conditions[0].status}/{.status.conditions[0].reason}")
      echo "PipelineRun=$PR status=$ST"
      case "$ST" in True/*) ;; *) echo "PipelineRun 未成功"; exit 1;; esac'''
```

兩個小坑：

1. `tkn` 第一行輸出是 `PipelineRun started: node-pipeline-run-xxxxx`。**別用 `awk '{print $3}'`**——在 Groovy 的三引號字串裡 `$3` 會被當成 Groovy 變數吃掉，拿到空字串。`sed` 沒這個問題。
2. 沒給的 optional 參數，`tkn` 會**互動式詢問**。Jenkins 沒有 TTY，直接 `Error: EOF`。所有參數明傳，再加 `--use-param-defaults` 保險。

## 怎麼確認你自己的環境有沒有中

找一對同時間的 Jenkins build 和 PipelineRun：Jenkins SUCCESS、PipelineRun `Failed`。
有一對就是中了。不需要看 Jenkinsfile，結果會說話。

修完以後反向再驗一次：Tekton 12/12 綠、Jenkins 也要綠。我第一版的 `awk` 寫法就是反向壞掉——Tekton 全綠、Jenkins 因為抓不到名字而紅。**兩個方向都對了才算修好。**

## 更一般的版本

這是「fire-and-forget 包裝」的通病：任何 `xxx start`、`xxx trigger`、`xxx submit` 指令，
exit code 都只代表「送出去了」。包進 CI 時要問自己：**我檢查的是送出，還是結果？**

在 lab 裡按一次看看：[ocp-pipeline-lab](https://github.com/ryanGTR/ocp-pipeline-lab) 的 `make build`，Jenkins 的 `node_build_pipeline` job 已經帶狀態檢查；
把第二個 `sh` 拿掉再跑一次壞的 build，就能親眼看到假綠。
