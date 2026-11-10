---
layout: post
title: "用 C 解 Two Sum 三次：暴力、雙指標、手刻雜湊表"
date: 2026-11-10 12:00:00 +0800
tags: [c, data-structure, hash-table, leetcode]
excerpt: "一個 Java 工程師用 C 解 Two Sum 三次：踩過的坑，比解法還多。"
---

## 為什麼要用 C 重寫第 1 題

我是 Java 工程師。Two Sum 在 Java 裡，一個 `HashMap` 幾行就解決了。

換成 C，第一個反應是：**這是什麼鬼？** 沒有 `HashMap`，雜湊表要自己刻。

接著第二個反應：這麼底層的工具自己寫，會不會一不小心就變成一個大大的 CVE？

所以這篇不只是解題。我想知道：在 C 裡從零刻一個雜湊表，會踩到哪些 Java 從來不讓你踩的坑？

## 題目與測試

LeetCode 167（已排序）和 LeetCode 1（未排序）只差一個前提，解法就分家。測試案例我刻意塞了溢位、負數、重複值、找不到這幾類，編譯時開 ASan＋UBSan，錯了會當場炸，不會默默給錯答案。

讓我紅燈的有三個：

- **溢位**：`{ 1, INT_MAX - 1, INT_MAX }` 找 `INT_MAX`。`1 + INT_MAX` 在 C 是 signed overflow，屬於 undefined behavior（未定義行為），不是單純「繞回負數」。
- **重複值**：`{ 3, 3 }` 找 `6`。同一個值出現兩次，要回傳兩個不同的 index，不能自己跟自己配。
- **ASan 當場炸掉**：程式「看起來會跑」，但 AddressSanitizer 一開就報錯（細節在解法三的坑 4）。

## 解法一：暴力 O(n²)

```c
for (int x = 0; x < n; x++) {
	for (int y = x + 1; y < n; y++) {
		temp = (long)nums[x] + nums[y];
		if (target == temp) {
			*i = x;
			*j = y;
			return 1;
		}
	}
}
return 0;
```

暴力解本身沒什麼好說的，坑在那一行加法：兩個 `int` 相加會溢位，所以要先轉成 `long`。

**轉型要放在加法之前，不是之後。** 這題我第一次答反了。

我原本以為 `(long)(a + b)` 是用 `long` 在算。實際上括號先做，括號裡的 `a`、`b` 都是 `int`，所以用 `int` 加，`INT_MAX + 1` 當場溢位。`(long)` 是加完才套上去的，已經太晚。

我也以為 `a + (long)b` 裡只有 `b` 變成 `long`，`a` 不動。實際上 C 有 usual arithmetic conversions（一般算術轉換）：兩邊型別不同時，小的那邊會自動升成大的。所以只轉一邊，整個加法就會用 `long` 算。

我給自己的記法比較粗俗：**先脫褲子再放屁，就不會沾到。** 先把容器準備好，再做會出事的那個動作。

Java 其實是同一條規則（binary numeric promotion）：`long x = a * b;` 一樣會溢位，要寫 `(long) a * b`。差別只在 Java 溢位是繞回負數，C 是 UB。

## 解法二：雙指標 O(n) 時間、O(1) 空間

前提是陣列已排序。迴圈條件是 `l < r`，不能是 `<=`（`l == r` 等於自己跟自己配）。我第一版還把 `sum` 寫在迴圈外面，只算一次，之後指標怎麼移都在比同一個數字。

```c
int l = 0, r = n - 1;

while (l < r) {
	long sum = nums[l] + (long)nums[r];

	if (target == sum) {
		*i = l;
		*j = r;
		return 1;
	}
	if (target > sum)
		l++;
	else
		r--;
}
return 0;
```

為什麼 `nums[l] + nums[r]` 太小時，可以把 `l` 直接丟掉？

`nums[r]` 已經是剩下的數裡最大的。**跟最大的配，結果還是太小，代表 `l` 跟誰配都不可能。** 所以 `l` 這一整列可以一次淘汰，不用一個一個試。太大時反過來，`r` 跟最小的配都還太大，`r` 整行淘汰。

每比一次就刪掉一整列或一整行，所以 O(n) 就走完。

## 解法三：手刻雜湊表

Java 一行 `HashMap<Integer, Integer>`，C 要自己做：

| 要處理的事 | 我的做法 |
|---|---|
| 存什麼 → 什麼 | 值 → index（Java 的 `Map<Integer, Integer>`） |
| 碰撞 | open addressing，線性探測 |
| 走到底要繞回 | `if (i == mask + 1) i = 0;`（`& mask` 版之後再換） |
| 怎麼知道找不到 | 走到空格 `!used[i]` 就停 |
| 釋放 | 只 free 自己 malloc 的，struct 在 stack 上不用 free |

核心是 `ht_find`，兩個出口：找到了、確定找不到。

```c
static bool ht_find(const struct ht *h, int key, int *val)
{
	unsigned int i = hash(key) & h->mask;

	for (;;) {
		if (h->used[i] && h->keys[i] == key) {
			*val = h->vals[i];
			return true;	/* found */
		}
		if (!h->used[i])
			return false;	/* empty slot: key is not here */
		i = i + 1;
		if (i == h->mask + 1)
			i = 0;		/* wrap around */
	}
}
```

### 我踩的坑（全文最值錢的部分）

1. **停止條件 vs 繼續條件分不清**：`ht_find` 有兩個出口。「找到了」那個我一次寫對；「確定找不到」那個連錯三版：看到別的 key 就停、寫成 `used[i] && keys[i] != key`（這是「繼續走」的條件，不是「停下來」的條件）、最後亂湊。直到被問「`used[5]` 是 true 還是 false」這種是非題，才寫出 `!used[i]`：**走到空格就停**，因為線性探測不會跳過空格去放東西。

2. **繞回的 off-by-one**：這個坑我三種錯都犯過。
   - 當時以為 8 格的表，最後一格是 8；實際上編號是 0～7，最後一格是 7（`mask`），「格數」是 8（`mask + 1`）。
   - 走到底不知道怎麼繞，乾脆寫成 `return`，等於找不到空格就放棄。這不是修 bug，是換個寫法碰運氣。
   - 看到別人寫 `i = (i + 1) & mask` 完全不懂。所以我最後交的版本是笨方法：`i = i + 1; if (i == h->mask + 1) i = 0;`。先用 if 寫懂了，才看得懂 `& mask` 其實在做同一件事（容量是 2 的次方時，`& (cap - 1)` 等於 `% cap`）。

3. **變數遮蔽**：`for (int i = 0;; i++)` 在迴圈裡又宣告一個 `i`，把外層的 `i` 蓋掉了（shadowing）。`-Wall -Wextra` 預設不會警告這個，要另外加 `-Wshadow`；Java 則是直接編譯錯誤。

4. **index 和值搞混**：當時以為 `nums[k]` 就是「第 k 個」，實際上它是「第 k 格裡面的數字」。題目要的是位置，`[2,7,11,15]` 找 `9` 要回傳 `0, 1`，我卻回傳了 `2, 7`。同一段還寫了 `*j = nums[n-k+1]`：`k = 0` 時去讀 `nums[n+1]`，超出陣列尾巴，ASan 當場報 `stack-buffer-overflow`。Java 會丟 `ArrayIndexOutOfBoundsException`；C 不會擋，沒開 ASan 就是默默讀到垃圾。

5. **free 了不該 free 的東西**：`ht_free` 裡除了 `free(h->keys)`、`free(h->vals)`、`free(h->used)`，我還多寫了一行 `free(h)`。當時以為「有 free 就要全部 free 乾淨」，實際上 `h` 是呼叫端 `struct ht h;` 宣告在 **stack** 上的，根本不是 `malloc` 來的。規則只有一條：**誰 `malloc` 的，才 `free` 誰。** Java 有 GC，從來不用想「這塊記憶體是誰的」；C 每一塊都要知道它從哪來。

## 三種解法對照

| | 暴力 | 雙指標 | 雜湊表 |
|---|---|---|---|
| 時間 | O(n²) | O(n) | 平均 O(n) |
| 額外空間 | O(1) | O(1) | O(n) |
| 需要排序 | 否 | 是 | 否 |
| 回傳原本 index | 是 | 排序後會遺失 | 是 |
| C 實作難度（我的體感） | ★ | ★★★★ | ★★★★★ |

雙指標給四顆星，是因為它的程式碼很短，但每一行都要想清楚：`l < r` 還是 `<=`、sum 要放在迴圈裡面每圈重算。短，不代表簡單。

## 我學到的

**慢慢走最快。**

`& mask` 看不懂，就先寫笨的 if 版；停止條件寫不出來，就退回去回答「這格是 true 還是 false」這種是非題。每次想跳步，都是在碰運氣；退一步把小的弄懂，反而最快寫綠。

## 原始碼

練習 repo 沒有公開，核心程式碼都已經貼在上面。編譯時建議開 `-Wall -Wextra -Werror -fsanitize=address,undefined`，坑 4 那種越界讀取會當場被抓到。

