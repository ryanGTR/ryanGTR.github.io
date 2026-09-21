---
layout: post
title: "Day 17：監控——模型服務要看哪四個指標"
series: "OpenShift AI 入門 30 天"
date: 2026-09-17 09:00:00 +0800
tags: [grafana, prometheus, monitoring, openshift-ai, mlops, tutorial]
excerpt: "從應用埋指標、部署 Prometheus 與 Grafana、到面板真的畫出資料。四個步驟，附完整 manifest 與每一步的驗證指令。"
feedback_question: "你們的模型服務有在監控嗎？盯的是哪幾個數字？"
lab_env_note: "⚠️ 上面寫的 `v3.5.0` 是這台叢集長期以來的版本；**本篇所有指令與輸出是在自動升級後的 `3.6.0-ea.1` 上重跑的**（升版經過寫在 Day 5 的補記）。"
---

## 這是什麼、解決什麼問題

模型上線之後，你需要知道兩件事：**它還活著嗎**、**它有沒有在變差**。

第一件跟任何 API 一樣——延遲、錯誤率、吞吐。
第二件是模型服務特有的：輸入的分布有沒有變、輸出有沒有退化。

**組合**：你的服務吐 `/metrics` → Prometheus 定時去抓 → Grafana 畫成面板。

---

## 什麼時候你會用到

- 模型已經上線，而你答不出「它現在正不正常」
- 有人問「上禮拜那次變慢是什麼時候開始的」，你只能猜
- 你要換一版模型，但沒有基準可以比

**如果模型還在開發階段、只有你自己在打，可以先不用。**
這一套的價值在「你不在看的時候，它有沒有出事」。

---

## 前置條件

- 一個已經上線的模型服務（我這裡是 KServe 的 `InferenceService`）
- 服務要能吐 Prometheus 格式的 `/metrics`（下面第一步就是做這個）
- namespace 裡放得下兩個 pod（我給 Prometheus、Grafana 各 request 50m／limit 500m CPU）

---

## 步驟一：在應用裡埋指標

用 `prometheus_client`。以 FastAPI 為例，**請求數與延遲放在 middleware**，每個 endpoint 自動記：

```python
from prometheus_client import (Counter, Histogram, Gauge,
                               generate_latest, CONTENT_TYPE_LATEST)

REQS    = Counter("llm_requests_total", "請求總數", ["endpoint", "status"])
LATENCY = Histogram("llm_request_latency_seconds", "請求延遲(秒)", ["endpoint"])
TOKENS  = Counter("llm_generated_tokens_total", "累計生成 token 數")
DRIFT_PSI = Gauge("llm_drift_psi", "請求分布 vs 訓練分布的 PSI")
DRIFT_OOV = Gauge("llm_drift_oov_rate", "請求用到訓練外字元的比例")

@app.middleware("http")
async def observe(request, call_next):
    t0, status = time.perf_counter(), 500     # 先假設失敗
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:                                  # 丟例外也一定會記到
        REQS.labels(request.url.path, str(status)).inc()
        LATENCY.labels(request.url.path).observe(time.perf_counter() - t0)

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

🔴 **`try/finally` 是這篇最重要的一行。** 我服務原本是 `call_next` 之後才 `inc()`，
handler 一丟例外就走不到——實測回了 4 次 **HTTP 500**，指標裡**一筆 500 都沒有**。
錯誤率是 0，不是沒錯，是沒記。

⚠️ 常見的 `app.mount("/metrics", make_asgi_app())` 會讓 `GET /metrics` 回 **307**、body 空的——
Prometheus 會跟著轉，**手動 `curl` 驗證卻什麼都看不到**。

在 `/generate` 裡更新模型相關的指標：

```python
    drift.observe(req.prompt)
    DRIFT_PSI.set(drift.psi())
    DRIFT_OOV.set(drift.oov_rate())
    ...
    TOKENS.inc(len(out) - len(ids))           # 實際生成數，不是 max_new_tokens
```

### 選對型別

| 型別 | 特性 | 用在 |
|---|---|---|
| `Counter` | 只增 | 總量：請求數、累計 token |
| `Histogram` | 自動分桶 | **分布**：延遲 p50/p95、一致率 |
| `Gauge` | 可上可下 | 當下狀態：PSI、OOV 率 |

**延遲要用 Histogram，不要用 Gauge。**
Gauge 只留最後一筆，你會看不到 p95——而 p95 才是使用者感受到的數字。

⚠️ 更新指標在每個請求的關鍵路徑上，**要夠便宜**。
我的 PSI 只切 Top-50 常用字＋「其他」共 51 桶，每次重算成本固定，不隨請求量長大。

**驗證這一步：**

```bash
oc port-forward deploy/<你的服務> 8000:8000 &
curl -s localhost:8000/metrics | grep 'endpoint="/generate"'
# llm_requests_total{endpoint="/generate",status="500"} 3.0   ← 修好之後才出現
# llm_requests_total{endpoint="/generate",status="200"} 3.0
```

---

## 步驟二：部署 Prometheus

Prometheus 需要一份抓取設定，告訴它去哪裡抓：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: llm-api
    metrics_path: /metrics
    static_configs:
      - targets: ['llm-scratch-predictor.llm-serve-demo.svc.cluster.local:80']
        labels:
          service: llm-scratch
```

⚠️ **兩個容易寫錯的地方：**

1. **target 用 Service DNS，不是 localhost**（跨 pod 了）。
2. **port 填 Service 的 port，不是容器的**：我的 Service 是 80（targetPort 8000），填 **80**。

設定放進 ConfigMap 掛給 Prometheus。我用 kustomize 從檔案生，dashboard JSON 就不用內嵌進 YAML：

```yaml
# kustomization.yaml
configMapGenerator:
  - name: prometheus-config
    files: [prometheus.yml=files/prometheus.yml]
generatorOptions:
  disableNameSuffixHash: true    # 關掉名稱雜湊，改檔案重啟 pod 就生效
```

**驗證這一步：**

```bash
oc port-forward svc/prometheus 9090:9090 &
curl -s 'localhost:9090/api/v1/targets?state=active' | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"'
# llm-api up          ← 要看到 up

curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=llm_requests_total' | jq '.data.result | length'
# 5                   ← 要有序列
```

**`up` 但序列只有 `/health`、`/metrics`**，代表抓到了但還沒有真的推論流量——打幾發請求再看。

---

## 步驟三：部署 Grafana 並接上 Prometheus

Grafana 的 datasource 用 provisioning 給（不要手動在 UI 建，那樣重建 pod 就沒了）：

```yaml
# grafana-datasource.yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus          # ← ⭐ 這一行必填，理由見下
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

⭐ **`uid` 一定要顯式指定。**

不給的話 Grafana 會**從名字算**一個 uid（`Prometheus` → `PBFA97CFB590B2093`，重開不變），
而你的 dashboard JSON 裡通常寫的是：

```json
{"type": "prometheus", "uid": "prometheus"}
```

兩邊對不上，**每一個面板都查不到資料**——pod、target 全綠，只有面板是空的。

**驗證這一步：**

```bash
curl -sk https://<grafana-route>/api/datasources | jq -r '.[0].uid'
# prometheus          ← 要跟 dashboard JSON 裡寫的一致
```

---

## 步驟四：放上 dashboard

dashboard 也用 provisioning：一個 provider 設定指向一個目錄，
把 dashboard JSON 放進那個目錄的 ConfigMap。

模型服務值得畫的面板：

| 面板 | 查詢 | 看什麼 |
|---|---|---|
| 請求率 | `sum(rate(llm_requests_total{endpoint="/generate"}[5m])) by (status)` | 流量與錯誤 |
| 延遲 p95 | `histogram_quantile(0.95, sum(rate(llm_request_latency_seconds_bucket{endpoint="/generate"}[5m])) by (le))` | 使用者感受 |
| 生成吞吐 | `rate(llm_generated_tokens_total[5m])` | 成本 |
| **漂移 PSI** | `llm_drift_psi` | 輸入變了沒 |
| **OOV 率** | `llm_drift_oov_rate` | 有沒有訓練時沒看過的東西 |

⚠️ **記得過濾 `endpoint`。** 探針打的 `/health`、Prometheus 抓的 `/metrics` 也會被記——
我那 5 分鐘 `/generate` 只佔 21%，**不過濾，「流量」有八成是自己人**。

⚠️ **PSI 要等樣本夠了才能信。** 我用訓練語料本身模擬「完全沒漂移」：100 個請求時一半會誤報 >0.25，
每請求 5 字的話，大約 300 個請求起才不再誤報；prompt 越長，同一請求內的字越相關，要的請求不會等比例變少。**請求還少的時候，漂移面板亮紅燈不代表輸入真的變了。**
反過來，同一句 prompt 重複打，打 1 次跟打 1 萬次 PSI 一模一樣（我實測 3.05），**樣本再多也不會回到正常值**。另外沒看過的字會掉進「其他」桶，PSI 反而變低——**OOV 要另外看**。

---

## 怎麼確認做對了

**三個問題，前兩個可以自動化，第三個只能用眼睛：**

| # | 問題 | 怎麼驗 |
|---|---|---|
| 1 | 指標**有沒有被抓** | `targets` 是不是 `up` |
| 2 | 指標**有沒有值** | 直接跑一個 query |
| 3 | **面板有沒有畫出來** | **打開它，看一眼** |

第三個沒有捷徑。而它是唯一一個「壞了會被使用者發現」的。

---

## 常見問題

**Q：pod 是 Running、target 是 up、query 有資料，但面板是空的。**
A：檢查 datasource 的 `uid` 跟 dashboard JSON 裡寫的一不一致（步驟三）。

**Q：錯誤率一直是 0。**
A：先故意打一個會失敗的請求，看 `status="500"` 有沒有出現（步驟一）。**沒出現就是沒在記。**

**Q：OpenShift 內建的 monitoring 不能用嗎？**
A：可以，但要先開 **user workload monitoring**，`ServiceMonitor` 才有人抓。
我這台 CRC 把 cluster monitoring 整個關了，所以自己起一組；正式環境優先用內建的。

---

**你們的模型服務有在監控嗎？盯的是哪幾個數字？**

{% include lab-env.html %}
