---
layout: post
title: "怎麼自動截 OpenShift console 和 Grafana 的圖"
date: 2026-09-06 09:00:00 +0800
tags: [openshift, grafana, automation, chrome-devtools, oauth, documentation]
excerpt: "寫平台文件要大量截圖，手動截會過期。用 Chrome DevTools Protocol + OAuth cookie 全自動化，四個坑一次講完。"
feedback_question: "你們的平台文件截圖是手動的嗎？版本更新後怎麼重截？"
---

寫平台文件最花時間的不是文字，是**截圖**——而且它會過期。
版本升一次，所有截圖作廢。

所以我把它自動化了。19 張圖，一個指令重跑。這篇是做法和四個坑。

---

## 為什麼不能用 `--screenshot`

Chrome 有現成的：

```bash
google-chrome --headless --screenshot=out.png https://...
```

**對 SPA 沒用。** OpenShift console、ODH dashboard、Grafana 都是單頁應用，
`--screenshot` 在 `load` 事件就截，那時候畫面上只有骨架。

要用 **Chrome DevTools Protocol（CDP）**，才能做「導航 → 等 → 截」。

```python
# 起 Chrome，開 remote debugging
subprocess.Popen(["google-chrome-stable", "--headless=new",
    "--ignore-certificate-errors", "--no-sandbox",
    "--remote-debugging-port=9333", "--remote-allow-origins=*",  # ← 坑 1
    "about:blank"])

# 開新分頁（新版 Chrome 的 /json/new 只吃 PUT，不吃 POST）
req = urllib.request.Request("http://127.0.0.1:9333/json/new?about:blank", method="PUT")
ws_url = json.load(urllib.request.urlopen(req))["webSocketDebuggerUrl"]

ws = websocket.create_connection(ws_url)
send("Emulation.setDeviceMetricsOverride", {...})   # ← 坑 2
for c in cookies: send("Network.setCookie", c)      # ← 坑 3
send("Page.navigate", {"url": url})
time.sleep(WAIT)
send("Page.captureScreenshot", {"format": "png"})
```

---

## 坑 1：新版 Chrome 擋跨來源 WebSocket

第一次連就被拒：

```
Handshake status 403 Forbidden
Rejected an incoming WebSocket connection from the http://127.0.0.1:9333 origin.
Use the command line flag --remote-allow-origins=http://127.0.0.1:9333
```

加 `--remote-allow-origins=*` 就好。這是為了防止網頁去連你本機的 debug port，
自動化腳本得明確放行。

---

## 坑 2：懶載入的面板，`captureBeyondViewport` 沒有用

CDP 有一個看起來很對的參數：

```python
send("Page.captureScreenshot", {"captureBeyondViewport": True})
```

我用它截 Grafana，得到的是**上面幾格有東西、下面全是空白**。

原因：**Grafana（和 OCP console 的長表格）只渲染 viewport 內的面板。**
`captureBeyondViewport` 把畫布延長，但捲不到的面板**根本沒被畫出來**，
延長的部分就是背景色。

正解是把 viewport 直接開到內容的高度：

```python
send("Emulation.setDeviceMetricsOverride",
     {"width": 1680, "height": 2600, "deviceScaleFactor": 1, "mobile": False})
```

我的 Grafana 截圖從 16 KB（空白）變成 158 KB（九個面板都在）。
**檔案大小是最快的自我檢查**——截到空白的圖，size 會小得很不合理。

---

## 坑 3：每個前端有自己的 OAuth，cookie 名字還不一樣

console 和 ODH dashboard 都要登入，而且**不能共用 cookie**：

| 前端 | OAuth client | session cookie 名字 |
|---|---|---|
| OCP console | `console` | `openshift-session-token-<pod後綴>` ← **名字會變** |
| ODH dashboard | `data-science` | `_oauth2_proxy` ← **是 oauth2，有個 2** |

console 那個特別討厭：cookie 名字帶著 console pod 的後綴
（例如 `openshift-session-token-console-65d49ddcc7-xcfqp`），
**pod 重建就變了**，不能寫死。

拿 cookie 的流程用 curl 走完就好：

```bash
# 1) GET 登入頁（會種 csrf cookie，並拿到 then / csrf 兩個 hidden 欄位）
curl -sk -c jar -b jar -L -o login.html "$START_URL"

# 2) POST 帳密，一路跟到 callback，session cookie 就種好了
curl -sk -c jar -b jar -L -o /dev/null \
  --data-urlencode "then=$THEN" --data-urlencode "csrf=$CSRF" \
  --data-urlencode "username=$U" --data-urlencode "password=$P" \
  "https://oauth-openshift.apps-.../login"
```

`$START_URL` 換成 console 或 ODH 的網址，就分別拿到兩組 cookie。

**Harbor 又是另一套**：CSRF token 要問兩次——第一次 `GET /api/v2.0/systeminfo`
只種 `_gorilla_csrf` cookie，**第二次才會在 response header 給你
`X-Harbor-CSRF-Token`**，然後帶著它 POST `/c/login`。

---

## 坑 4：不要猜 SPA 的路徑

我要截 pipeline run 的詳情頁，照舊版的 URL 寫：

```
/pipelineRun/view/<runId>     → We can't find that page
```

ODH 3.5 改了：

```
/develop-train/pipelines/runs/<namespace>/runs/<runId>
```

**猜路徑是浪費時間。從列表頁把連結撈出來最快：**

```javascript
[...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))
```

用 `Runtime.evaluate` 執行它，答案直接出來。

> 這個坑本身也是一條情報：**網路上凡是給 ODH 舊版 URL 的教學，現在全部 404。**

---

## 額外好處：截圖變成可重跑的資產

自動化之後有一個沒預期到的效果：**截圖不再是「當初的樣子」，
而是「現在的樣子」。**

版本升級後重跑一次腳本，所有圖同時更新。
更重要的是——**如果某張圖跑不出來了，那本身就是一個發現**。
我就是這樣抓到 Grafana 面板全壞的：圖截出來是空的，
一路追下去才發現 datasource uid 對不上。

**手動截圖的話，我只會覺得「今天沒資料吧」，然後截一張別的。**

---

**你們的平台文件截圖是手動的嗎？版本更新後怎麼重截？**

{% include lab-env.html %}
