---
layout: post
title: "怎麼把資料放上平台——以及放上去之後你答不答得出它哪來的"
series: "OpenShift AI 實戰紀錄"
date: 2026-09-28 09:00:00 +0800
tags: [openshift-ai, mlops, data, s3, lineage, governance, dataset-card]
excerpt: "四種把資料弄上平台的方式，各自的代價。但真正的問題不是「怎麼放」——是我查了自己平台上那份 99 MB 的語料，除了檔名、日期、大小之外，什麼都查不到。"
feedback_question: "你們平台上的訓練資料，查得到「哪來的、什麼時候抓的、誰處理過」嗎？"
---

模型要有資料才能訓練。而資料在你的筆電、在資料庫、在某個 NAS 上——
**怎麼弄到平台上，讓 pipeline 和 workbench 讀得到？**

四種方式，然後是一個更重要的問題。

---

## 四種方式

### ① 手動上傳到 S3（最快，也最沒紀錄）

```bash
mc cp corpus.txt local/models/llm/clean_corpus.txt
```

**三十秒搞定，適合 lab 和第一次試。**
我自己就是這樣放的——而這篇後半就是在講它的代價。

### ② 從 workbench 拉

在 Jupyter 裡直接抓：

```python
import boto3
s3 = boto3.client("s3", endpoint_url=os.environ["AWS_S3_ENDPOINT"])
s3.download_file("models", "llm/corpus.txt", "/opt/app-root/src/corpus.txt")
```

憑證來自你掛上去的 [Connection](/2026/09/connection-is-just-a-labelled-secret/)，
**不用寫死在程式裡**。

適合探索階段。缺點是**那個過程留在某個人的 notebook 裡**——
別人重現不了，而且 notebook 常常不進版控。

### ③ 用 pipeline 的一棒去抓（推薦）

把「取得資料」變成 pipeline 的第一棒，跟訓練、評估同一條鏈：

```python
@dsl.container_component
def ingest_data(source_url: str, run_id: str):
    return dsl.ContainerSpec(image=IMAGE, command=["bash","-c", INGEST], args=[source_url, run_id])
```

**好處是它會留下紀錄**：哪一次 run、用了什麼參數、產出放哪。
這是唯一能讓「資料怎麼來的」變成可查詢事實的做法。

### ④ 直接掛外部儲存（PVC / NFS / CSI）

資料太大、或本來就在企業儲存上時用這個。

⚠️ 但它有一個治理問題:**掛進來的東西不受平台版本控制**。
NFS 上那個檔案被別人改了，你的訓練結果就變了，而且沒有任何紀錄。

---

## ⭐ 但真正的問題不是「怎麼放」

我去查了自己平台上那份訓練語料，**能查到的全部資訊只有這些**：

```bash
mc stat local/models/llm/clean_corpus.txt
```
```
Name      : clean_corpus.txt
Date      : 2026-08-24 03:35:32 UTC
Size      : 99 MiB
ETag      : 9e4a738f546d71860ae83a1ac165112f-7
Metadata  :
  Content-Type: text/plain
```

**檔名、日期、大小、ETag。就這樣。**

查不到的東西：

| 問題 | 答案 |
|---|---|
| 這是什麼資料？ | ？（檔名叫 clean_corpus，那是處理後的名字） |
| 從哪裡來的？ | ？ |
| 什麼時候抓的？ | ？（8/24 是**上傳**時間，不是抓取時間） |
| 用什麼腳本處理的？哪一版？ | ？ |
| 授權是什麼？可以拿來訓練嗎？ | ？ |
| 誰放上去的？ | ？ |

**而這份資料訓練出來的模型，現在正在對外服務。**

---

## 為什麼這件事比它看起來嚴重

**模型會把資料的一切學進去——包括你不知道自己收了什麼。**

出事的時候，你會被問這些：

- 「這個模型講出了不該講的東西，訓練資料裡有什麼？」
- 「這批資料的授權允許商業使用嗎？」
- 「有個人資料在裡面嗎？」
- 「重跑一次能不能得到同一顆模型？」

**這四題都不是模型的問題，是資料的問題。**
而如果你的資料只有「檔名、日期、大小」，**你一題都答不出來。**

⚠️ 更麻煩的是：這些問題通常在**你最不想回答的時候**被問到。

---

## 補法：給資料一張身分證

跟資料一起放一個 `dataset_card.json`：

```json
{
  "name": "zhwiki-clean",
  "source_url": "https://dumps.wikimedia.org/zhwiki/20260601/...",
  "downloaded_at": "2026-06-21T08:05:00+08:00",
  "raw_sha256": "sha256:...",
  "raw_size_bytes": 110234567,
  "license": "CC BY-SA 4.0",
  "processing": {
    "script": "pipeline/01_prepare_data.py",
    "code_commit": "2d4f691",
    "steps": ["collect", "clean", "quality-filter", "exact-dedup", "near-dedup"],
    "dropped": {"quality": 12, "exact": 0, "near": 161}
  },
  "output_sha256": "sha256:...",
  "docs_out": 11126,
  "chars": 39850979
}
```

**六個欄位是最低限度**：

| 欄位 | 為什麼要 |
|---|---|
| `source_url` | 出事時要能回到源頭 |
| `downloaded_at` | 資料有時效性；**上傳時間 ≠ 抓取時間** |
| `raw_sha256` | 證明「我抓的就是那一份」 |
| `license` | **法遵**。這一欄沒有，前面五欄都沒意義 |
| `code_commit` | 同一份原始資料 + 不同版本的清理腳本 = 不同的訓練資料 |
| `output_sha256` | 讓模型的 lineage 指得到這一份 |

⭐ **然後把 `output_sha256` 寫進模型台帳的 lineage。**
這樣「線上這顆模型用了哪份資料」就是一個可查詢的事實，不是一段回憶。

---

## 怎麼開始（不用一次做完）

**第一步不是建系統，是補現有那幾份的身分證。**

你平台上大概有三到五份重要的資料。**手寫那幾張卡，半天就能寫完。**
半天換到「出事時答得出來」，這個交易很划算。

**第二步才是把它自動化**——在 pipeline 加一棒 `ingest`，
產出資料的同時產出那張卡。

⚠️ 順序不要反過來。先做自動化，你會花兩週寫一個沒人用的東西；
先手寫幾張，你會知道哪些欄位真的需要。

---

## 誠實標一下

**我也還沒做。**

我的 lab 上那份 99 MB 語料，到今天為止仍然只有檔名、日期、大小。
我知道它是中文維基（因為是我自己下載的），
**但那份知識在我腦子裡，不在平台上。**

而這正是問題所在：**「我知道」和「查得到」是兩件事，
而只有後者能在三個月後、或在我不在的時候，回答問題。**

---

## 給驗收的一句

> **「請給我看訓練資料的來源、下載日期、和授權。」**

如果對方指著一個 S3 路徑說「就在那裡」，那你們有的是**一個檔案**，
不是**一份可以追溯的訓練資料**。

而模型的所有問題，最後都會追回到這裡。

---

**你們平台上的訓練資料，查得到「哪來的、什麼時候抓的、誰處理過」嗎？**

{% include lab-env.html %}
