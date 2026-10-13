# Kubernetes AI Agent 智慧維運平台建置企劃書

## 目錄

1. [專案摘要](#一專案摘要)
2. [專案背景與動機](#二專案背景與動機)
3. [專案目標](#三專案目標)
4. [專案範圍](#四專案範圍)
5. [主要使用情境](#五主要使用情境)
6. [系統整體架構](#六系統整體架構)
7. [主要系統元件](#七主要系統元件)
8. [系統執行流程](#八系統執行流程)
9. [資訊安全與權限設計](#九資訊安全與權限設計)
10. [與既有工具之定位差異](#十與既有工具之定位差異)
11. [預期效益](#十一預期效益)
12. [專案特色](#十二專案特色)
13. [專案實施規劃](#十三專案實施規劃)
14. [系統驗證方式](#十四系統驗證方式)
15. [成效衡量指標](#十五成效衡量指標)
16. [風險與限制](#十六風險與限制)
17. [未來發展方向](#十七未來發展方向)
18. [結論](#十八結論)

---

# 一、專案摘要

隨著公司內部 Kubernetes 平台承載的應用服務逐漸增加，系統管理、狀態查詢與異常排查的複雜度亦隨之提升。

目前雖已建置 Kubernetes 管理與監控相關工具，提供資源狀態、系統指標及日誌等資訊，但在實際使用情境中，使用者仍須具備一定程度的 Kubernetes 知識，才能判斷問題發生時應查詢哪些資源，以及如何從各項資訊中找出可能的異常原因。

例如，當使用者遇到「系統無法存取」、「服務啟動失敗」或「應用程式反應異常」等問題時，通常需要進一步檢查 Pod、Deployment、Service、Event、Log、CPU、Memory 等資訊。

對於不熟悉 Kubernetes 的使用者而言，即使相關資訊已存在於平台中，仍可能因為不知道「應該查什麼」而無法快速進行問題排查。

因此，本專案規劃建置一套 **Kubernetes AI Agent 智慧維運平台**，整合大型語言模型（Large Language Model, LLM）、AI Agent、Model Context Protocol（MCP）、Kubernetes API、企業身分驗證機制及 Kubernetes RBAC。

使用者可直接透過自然語言描述需求或問題，例如：

> 我的服務為什麼無法正常啟動？

> 幫我查看目前服務是否正常。

> 哪些 Pod 的 CPU 使用率較高？

> 為什麼這個 Pod 一直重新啟動？

AI Agent 將理解使用者需求，自動判斷應查詢的 Kubernetes 資源，透過 MCP Tool 取得即時系統資訊，再將查詢結果整理為較容易理解的自然語言，並協助使用者判斷可能的問題方向。

本專案並非取代既有 Kubernetes 管理與監控平台，而是在既有工具之上建立一層 **自然語言智慧操作介面（AI Operation Layer）**，降低 Kubernetes 使用門檻，提升使用者自助查詢與初步問題排查能力。

---

# 二、專案背景與動機

## 2.1 Kubernetes 維運現況

目前 Kubernetes 平台已具備相關管理與監控工具，例如：

- Kubernetes 管理介面
- Grafana 監控平台
- Kubernetes API
- kubectl
- 系統日誌與事件資訊

上述工具皆能提供 Kubernetes 運作狀態及相關資訊。

然而，多數工具仍以「Kubernetes Resource」或「監控指標」為主要操作方式。

當使用者遇到應用系統問題時，通常需要先具備相關知識，例如：

- Pod 的用途與狀態
- Deployment 與 Pod 的關聯
- Service 與應用程式之間的關係
- Kubernetes Event 所代表的意義
- Container Log 應如何取得
- CPU / Memory 資源使用量如何判讀

因此，目前的主要問題並非缺少系統資訊，而是：

> 使用者必須先知道應該查詢哪些資訊，才能開始進行問題排查。

---

## 2.2 現有問題

### 2.2.1 Kubernetes 學習與操作門檻較高

Kubernetes 涉及多種資源與元件。

對 Kubernetes 經驗較少的使用者而言，當服務發生異常時，往往難以判斷應從哪個方向開始檢查。

例如，一個「服務無法啟動」的問題，背後可能涉及：

- Pod CrashLoopBackOff
- ImagePullBackOff
- Readiness / Liveness Probe Failed
- Container Application Error
- PVC Mount Failed
- Kubernetes Scheduling Failed
- CPU / Memory 資源不足
- Service 或 Endpoint 異常

若使用者不了解 Kubernetes 架構，即使可以進入管理平台，也不一定能快速找到真正需要查看的資訊。

---

### 2.2.2 資訊分散於不同工具

不同類型的 Kubernetes 資訊可能分散於：

- Kubernetes 管理介面
- Grafana
- Kubernetes API
- Container Logs
- Kubernetes Events
- kubectl

當使用者進行問題排查時，可能需要在不同工具之間切換，再自行整合各項資訊。

---

### 2.2.3 問題排查高度依賴平台維運人員

當一般使用者或應用系統人員無法自行判斷 Kubernetes 問題時，通常會轉由平台維運人員協助。

常見需求例如：

> 幫我查看服務是不是正常。

> 可以幫我看一下 Pod 為什麼一直重新啟動嗎？

> 可以幫我確認最近有沒有異常 Log 嗎？

> CPU 或 Memory 是否過高？

這類問題本身可能只是資訊查詢，但由於需要 Kubernetes 知識才能取得與判讀資料，因此仍需平台人員介入。

長期而言，容易增加平台團隊的重複性工作與支援負擔。

---

# 三、專案目標

本專案主要目標為：

> 建立一套以自然語言為操作入口的 Kubernetes AI Agent，使使用者即使不熟悉 Kubernetes，也能透過描述需求或問題的方式取得即時系統資訊，並進行初步問題分析。

具體目標如下。

---

## 3.1 降低 Kubernetes 使用門檻

使用者不需要熟悉 Kubernetes Resource 架構，也不需要記憶 kubectl 指令。

使用者只需要描述：

> 「我遇到什麼問題？」

再由 AI Agent 判斷：

> 「需要查看哪些 Kubernetes 資訊？」

藉此將 Kubernetes 的操作方式由「資源導向」逐步轉變為「問題導向」。

---

## 3.2 建立 Kubernetes Self-Service 能力

將部分原本需要平台維運人員協助處理的查詢工作，轉換為使用者自行透過 AI Agent 完成。

例如：

- 查詢服務運作狀態
- 查詢 Pod 狀態
- 查看 Container Logs
- 查看 Kubernetes Events
- 查詢 CPU / Memory 使用量
- 查看 Deployment 狀態
- 協助判斷常見 Kubernetes 異常

藉此降低一般查詢需求對平台維運團隊的依賴。

---

## 3.3 建立對話式問題排查方式

傳統 Kubernetes 工具通常需要使用者自行決定下一個查詢步驟。

AI Agent 則可依據目前取得的資訊，協助使用者進一步查看相關資源。

例如：

```text
使用者
│
│「為什麼我的服務無法正常啟動？」
▼
AI Agent
│
├─ 查詢 Pod 狀態
├─ 查詢 Kubernetes Events
├─ 查詢 Container Logs
├─ 檢查 Deployment 狀態
└─ 查看資源使用情況
