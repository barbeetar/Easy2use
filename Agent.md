# Kubernetes AI Agent 智慧維運平台建置企劃書

## 一、專案摘要

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

## 二、專案背景與動機

### 2.1 Kubernetes 維運現況

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

### 2.2 現有問題

#### 2.2.1 Kubernetes 學習與操作門檻較高

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

#### 2.2.2 資訊分散於不同工具

不同類型的 Kubernetes 資訊可能分散於：

- Kubernetes 管理介面
- Grafana
- Kubernetes API
- Container Logs
- Kubernetes Events
- kubectl

當使用者進行問題排查時，可能需要在不同工具之間切換，再自行整合各項資訊。

#### 2.2.3 問題排查高度依賴平台維運人員

當一般使用者或應用系統人員無法自行判斷 Kubernetes 問題時，通常會轉由平台維運人員協助。

常見需求例如：

> 幫我查看服務是不是正常。

> 可以幫我看一下 Pod 為什麼一直重新啟動嗎？

> 可以幫我確認最近有沒有異常 Log 嗎？

> CPU 或 Memory 是否過高？

這類問題本身可能只是資訊查詢，但由於需要 Kubernetes 知識才能取得與判讀資料，因此仍需平台人員介入。

長期而言，容易增加平台團隊的重複性工作與支援負擔。

---

## 三、專案目標

本專案主要目標為：

> 建立一套以自然語言為操作入口的 Kubernetes AI Agent，使使用者即使不熟悉 Kubernetes，也能透過描述需求或問題的方式取得即時系統資訊，並進行初步問題分析。

具體目標如下。

### 3.1 降低 Kubernetes 使用門檻

使用者不需要熟悉 Kubernetes Resource 架構，也不需要記憶 kubectl 指令。

使用者只需要描述：

> 「我遇到什麼問題？」

再由 AI Agent 判斷：

> 「需要查看哪些 Kubernetes 資訊？」

### 3.2 建立 Kubernetes Self-Service 能力

將部分原本需要平台維運人員協助處理的查詢工作，轉換為使用者自行透過 AI Agent 完成。

例如：

- 查詢服務運作狀態
- 查詢 Pod 狀態
- 查看 Container Logs
- 查看 Kubernetes Events
- 查詢 CPU / Memory 使用量
- 查看 Deployment 狀態
- 協助判斷常見 Kubernetes 異常

### 3.3 建立對話式問題排查方式

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
```

最後將資訊整理為：

- 目前系統狀況
- 發現的異常資訊
- 可能原因
- 建議後續檢查方向

### 3.4 維持企業既有安全與權限機制

AI Agent 不應因為具備 Kubernetes 查詢能力，而取得超出使用者原本權限的資訊。

因此，本系統沿用企業既有身分驗證及 Kubernetes RBAC 權限控管機制。

整體概念為：

```text
企業帳號
↓
LDAP / OIDC 身分驗證
↓
取得 User / Group
↓
傳遞使用者身分 Token
↓
Kubernetes API Authentication
↓
Kubernetes RBAC Authorization
↓
允許或拒絕資源存取
```

最終 Kubernetes 資源的存取權限仍由 Kubernetes API Server 與 RBAC 機制決定。

---

## 四、專案範圍

### 4.1 第一階段範圍

第一階段以 **Kubernetes Read-Only AI Agent** 為主要建置方向。

預計支援：

- Namespace
- Pod
- Deployment
- StatefulSet
- Service
- PVC
- Kubernetes Events
- Container Logs
- CPU 使用率
- Memory 使用率
- Pod Status
- Deployment Status

系統主要提供：

```text
查詢
+
資訊整理
+
初步異常分析
+
建議檢查方向
```

第一階段原則上不開放 AI Agent 執行：

- Create
- Delete
- Patch
- Update
- Scale
- Restart
- 修改 Kubernetes Resource

藉此降低 AI Agent 誤判或錯誤操作對正式環境造成影響的風險。

---

## 五、主要使用情境

### 5.1 Kubernetes 資源查詢

使用者可直接詢問：

> 目前有哪些 Pod？

> 幫我查看某個服務相關的 Deployment。

> 現在哪些 Pod 狀態不是 Running？

AI Agent 將自然語言需求轉換為 Kubernetes Resource 查詢。

### 5.2 系統資源使用量查詢

例如：

> 哪一個 Pod CPU 使用率最高？

> 幫我列出目前 Pod 的 Memory 使用量。

> 哪些服務目前資源使用量比較高？

AI Agent 可取得相關監控或 Metrics 資訊，並將資料整理後回覆使用者。

### 5.3 Log 查詢

使用者不需要預先知道完整 Pod 或 Container 名稱。

例如：

> 幫我查看這個服務最近有沒有 Error。

AI Agent 可先找出與目標服務相關的 Kubernetes Workload，再進一步取得 Container Logs。

### 5.4 Kubernetes 異常排查

例如：

> 為什麼我的服務起不來？

AI Agent 可依據問題情境進一步查詢：

```text
Pod Status
↓
Kubernetes Events
↓
Container Logs
↓
Deployment Status
↓
Resource Usage
```

並根據實際取得的資訊整理可能的問題方向。

---

## 六、系統整體架構

本系統整體架構如下：

```text
User
 │
 ▼
Open WebUI
 │
 │ Natural Language
 │ User / Group
 │ OIDC Token
 ▼
Pipe
 │
 ▼
n8n Webhook
 │
 ▼
Prepare Context
 │
 ▼
Identity / Context Validation
 │
 ▼
AI Agent
 │
 ├── LLM
 │
 └── MCP Client
       │
       ▼
Kubernetes MCP Server
       │
       │ User Token Passthrough
       ▼
Kubernetes API Server
       │
       ▼
Kubernetes RBAC
       │
       ▼
Authorized Kubernetes Resources
```

---

## 七、主要系統元件

| 元件 | 主要功能 |
|---|---|
| LDAP | 提供企業使用者帳號與群組資訊 |
| Dex | 整合 LDAP 並提供 OIDC 身分驗證 |
| Open WebUI | 提供使用者自然語言對話介面 |
| Pipe | 負責 Open WebUI 與後端 Workflow 之間的請求傳遞 |
| n8n | 執行 AI Agent Workflow、Context 建立與流程控制 |
| LLM | 提供自然語言理解、推理及 Tool Calling 能力 |
| AI Agent | 分析使用者需求並選擇適合的工具 |
| MCP Client | 提供 AI Agent 與 MCP Server 之間的標準化介面 |
| Kubernetes MCP Server | 將 Kubernetes 查詢能力封裝為 AI 可使用的 Tools |
| Kubernetes API Server | 提供 Kubernetes Resource API |
| Kubernetes RBAC | 控制使用者實際可存取的 Kubernetes 資源範圍 |
| Monitoring / Metrics | 提供 CPU、Memory 等系統資源資訊 |

---

## 八、系統執行流程

### 8.1 使用者登入

使用者透過 Open WebUI 進行登入。

系統透過 Dex 與企業 LDAP 完成使用者身分驗證，並取得使用者相關資訊，例如：

- User Identity
- Group
- OIDC Token

### 8.2 使用者提出問題

使用者直接透過自然語言提出需求，例如：

> 幫我查看目前哪一個 Pod CPU 使用率最高。

Open WebUI 透過 Pipe 將相關資訊傳送至 n8n，包括：

```text
User
Group
Message
OIDC Token
```

### 8.3 建立使用者 Context

n8n 接收到請求後，執行：

```text
Prepare Context
↓
Validate Identity / Context
↓
AI Agent
```

將使用者身分資訊、問題內容及後續 Tool Calling 所需資訊整理後，交由 AI Agent 處理。

### 8.4 AI Agent 理解使用者需求

AI Agent 使用 LLM 分析自然語言內容。

例如：

> 哪一個 Pod CPU 使用率最高？

AI Agent 可推論為：

```text
Intent：
查詢 Pod CPU Usage

Scope：
使用者授權範圍

Required Tool：
Kubernetes / Metrics Tool
```

並選擇適合的 MCP Tool 執行查詢。

### 8.5 MCP 查詢 Kubernetes

AI Agent 透過 MCP Client 呼叫 Kubernetes MCP Server 所提供的 Kubernetes Tools。

MCP Server 使用 User Token Passthrough 機制，將目前登入使用者的身分 Token 傳遞至 Kubernetes API Server。

因此 Kubernetes API 所看到的請求身分，仍然對應實際登入的使用者，而非所有使用者共用單一高權限身分。

### 8.6 Kubernetes RBAC 執行授權

Kubernetes API Server 驗證使用者身分後，根據 User / Group 與 Kubernetes RBAC 判斷實際資源存取權限。

例如：

```text
平台管理群組
↓
較廣泛的唯讀權限

特定業務群組
↓
指定 Namespace Read Only

一般使用者
↓
依授權範圍查詢

未授權資源
↓
403 Forbidden
```

因此，即使 AI Agent 嘗試查詢超出使用者權限範圍的 Kubernetes Resource，最終仍會受到 Kubernetes RBAC 限制。

### 8.7 AI Agent 整理查詢結果

Kubernetes 查詢結果經由：

```text
Kubernetes API
↓
MCP Server
↓
AI Agent
↓
n8n
↓
Open WebUI
```

回傳給使用者。

AI Agent 再將原始 Kubernetes 資訊轉換為較容易閱讀的自然語言內容。

例如原始結果：

```text
Pod-A CPU: 100m
Pod-B CPU: 530m
Pod-C CPU: 230m
```

回覆可整理為：

> 目前 Pod-B 的 CPU 使用量最高，約為 530m，其次為 Pod-C。若系統目前有效能異常，可進一步查看 Pod-B 的 CPU Limit 與近期使用趨勢。

---

## 九、資訊安全與權限設計

將 AI Agent 與 Kubernetes 整合時，資訊安全與權限控管為本專案的重要設計原則。

本系統採用多層防護方式。

### 9.1 使用者身分驗證

透過：

```text
LDAP
+
Dex
+
OIDC
```

確認實際登入使用者及所屬群組。

### 9.2 AI Agent 功能限制

第一階段僅提供 Read-Only Tool。

AI Agent 可進行資訊查詢與分析，但不直接修改 Kubernetes Resource。

即使模型產生錯誤判斷，也能降低對 Kubernetes 環境造成直接影響的可能性。

### 9.3 Kubernetes RBAC

最終資源授權仍由 Kubernetes API Server 執行。

AI Agent 本身並非 Kubernetes 最終的 Authorization Engine。

也就是：

```text
AI Agent
↓
提出查詢請求

Kubernetes API
↓
確認使用者身分

RBAC
↓
決定是否允許存取
```

此架構可避免僅依賴 Prompt 或 Workflow 進行安全控制。

### 9.4 Token Passthrough

系統查詢 Kubernetes 時保留實際使用者身分。

其主要目的為：

> AI Agent 代替使用者進行查詢，但不取代使用者原本的 Kubernetes 權限。

如此可確保：

- 不同使用者取得不同的資料範圍
- 使用者無法透過 AI Agent 繞過既有 RBAC
- 權限管理仍集中於 Kubernetes 原生機制

---

## 十、與既有工具之定位差異

| 項目 | kubectl | Kubernetes GUI | Grafana | Kubernetes AI Agent |
|---|---|---|---|---|
| 操作方式 | Command | GUI | Dashboard | Natural Language |
| Kubernetes 知識需求 | 高 | 中 | 中 | 低 |
| Kubernetes Resource 查詢 | ✓ | ✓ | 部分 | ✓ |
| Logs 查詢 | ✓ | ✓ | 視整合方式 | ✓ |
| Metrics 查詢 | 需相關工具 | 部分 | ✓ | ✓ |
| 問題分析 | 人工 | 人工 | 人工 | AI 輔助 |
| 跨資源資訊整合 | 人工 | 人工 | 人工 | Agent 協助 |
| 對話式追查 | ✕ | ✕ | ✕ | ✓ |
| RBAC | ✓ | ✓ | 依整合方式 | ✓ |
| 初學者使用門檻 | 高 | 中 | 中 | 低 |

因此，本專案並非取代現有工具，而是建立於既有 Kubernetes 管理、監控與權限機制之上的：

> **AI Operation Layer**

---

## 十一、預期效益

### 11.1 對一般使用者

降低 Kubernetes 使用門檻。

原本使用者必須思考：

> 「我要查看哪一個 Kubernetes Resource？」

導入 AI Agent 後，可改為：

> 「我目前遇到什麼問題？」

再由 Agent 協助決定應查詢哪些資訊。

### 11.2 對應用系統與開發人員

使用者可自行完成部分基本排查，例如：

- 查看 Pod 狀態
- 查看 Logs
- 查看 Events
- 查看 Deployment
- 查詢 CPU / Memory
- 判斷常見 Kubernetes 異常

進而縮短等待平台團隊協助的時間。

### 11.3 對平台維運團隊

可降低部分重複性查詢需求，例如：

- 服務是否正常
- Pod 為什麼重新啟動
- 最近是否有錯誤 Log
- CPU / Memory 是否異常
- Deployment 是否正常

使平台團隊能將更多時間投入較複雜的系統架構、平台維護及異常處理工作。

---

## 十二、專案特色

### 12.1 自然語言 Kubernetes 操作

將傳統 Kubernetes Resource 查詢方式轉換成自然語言介面。

降低使用者必須了解 Kubernetes Resource 與指令的門檻。

### 12.2 企業身分整合

與既有 LDAP / OIDC 身分機制整合，而非建立另一套獨立 AI 使用者帳號。

### 12.3 Kubernetes 原生 RBAC

使用 Kubernetes RBAC 作為最終 Authorization 機制，沿用既有平台的權限管理方式。

### 12.4 User Token Passthrough

每次 Kubernetes 查詢保留實際登入使用者的身分，使 AI Agent 無法因共用高權限帳號而繞過使用者原本的存取限制。

### 12.5 Agent-based Troubleshooting

AI Agent 不僅回答 Kubernetes 知識問題，也可以根據使用者描述，選擇適合的 Tool 取得 Kubernetes 即時資訊。

形成：

```text
問題理解
↓
工具選擇
↓
即時資料取得
↓
資訊關聯
↓
初步問題分析
```

---

## 十三、專案實施規劃

### Phase 1：基礎架構建置

完成：

- Open WebUI
- n8n
- LLM
- AI Agent
- MCP Server
- Kubernetes API Connection

驗證 AI Agent 可以正確查詢 Kubernetes Resource。

### Phase 2：身分驗證與權限控管

完成：

- LDAP
- Dex
- OIDC
- Group Mapping
- Kubernetes RBAC
- User Token Passthrough

驗證不同身分的使用者，只能查詢其被授權的 Kubernetes Resource。

### Phase 3：Observability 能力整合

逐步增加：

- Container Logs
- Kubernetes Events
- CPU
- Memory
- Metrics
- 系統狀態

使 Agent 能取得更多維運相關資訊。

### Phase 4：AI 問題排查能力強化

建立常見 Kubernetes 異常測試情境，持續改善：

- Tool Selection
- Tool Calling
- Context Handling
- Troubleshooting Flow
- 回覆格式

使 Agent 從單純的資訊查詢工具，逐漸發展為問題排查輔助工具。

### Phase 5：企業維運知識整合

後續可視需求整合：

- SOP
- Runbook
- FAQ
- 系統架構文件
- Known Issues
- Incident Report

使 Agent 除了理解 Kubernetes 即時狀態外，也能進一步參考企業內部既有維運知識。

---

## 十四、系統驗證方式

本專案可從功能、權限、安全性及 AI 回覆品質等面向進行驗證。

### 14.1 功能驗證

驗證系統能否正確執行：

```text
Pod Query
Deployment Query
Log Query
Event Query
CPU Query
Memory Query
Resource Status Query
```

### 14.2 權限驗證

建立不同權限層級的測試帳號或群組。

確認：

- 可存取授權範圍內資源
- 無法存取未授權 Namespace
- 無法存取未授權 Resource
- 越權請求由 Kubernetes RBAC 拒絕

### 14.3 AI Agent 驗證

建立常見異常情境，例如：

- CrashLoopBackOff
- ImagePullBackOff
- OOMKilled
- PVC Mount Failed
- Probe Failed
- Scheduling Failed

驗證 AI Agent 是否能：

1. 選擇適合的 Tool
2. 傳遞正確查詢參數
3. 取得相關 Kubernetes 資訊
4. 正確整理查詢結果
5. 提供合理的初步判斷方向

### 14.4 安全性驗證

確認：

- AI Agent 無法執行未開放的寫入操作
- 使用者 Token 不會被其他使用者共用
- 不同使用者查詢結果符合 RBAC 權限
- 無權限存取時系統正確回傳拒絕資訊
- AI Agent 無法透過 Prompt 繞過 Kubernetes Authorization

---

## 十五、成效衡量指標

為評估專案實際效益，可設定下列指標。

| 評估面向 | 指標 |
|---|---|
| 查詢成功率 | AI Agent 是否能正確取得使用者所需 Kubernetes 資訊 |
| Tool Calling 成功率 | Tool 選擇及參數是否正確 |
| 問題定位效率 | 使用 AI Agent 前後的平均問題排查時間 |
| Self-Service 比例 | 使用者自行完成查詢而不需平台團隊介入的比例 |
| 權限正確率 | 不同使用者是否只能取得授權範圍內的資訊 |
| 使用者體驗 | 使用者是否能透過自然語言完成原本需要 Kubernetes 知識的操作 |

第一階段可先以功能正確率與權限正確率作為主要驗證項目，後續取得實際使用數據後，再逐步建立量化 KPI。

---

## 十六、風險與限制

### 16.1 LLM 判斷可能發生錯誤

LLM 可能出現：

- 錯誤推論
- Tool 選擇錯誤
- 查詢參數錯誤
- 誤解使用者意圖

因此第一階段以 Read-Only 為原則，避免 AI 判斷錯誤直接影響 Kubernetes 環境。

### 16.2 AI 分析不代表最終 Root Cause

AI Agent 可根據 Kubernetes Status、Events、Logs 與 Metrics 提供：

- 異常資訊整理
- Possible Cause
- Troubleshooting Direction

但不應將模型推論直接視為已確認的 Root Cause。

複雜或重大異常仍應由相關技術人員進行最終確認。

### 16.3 Context Window 與資料量限制

大量 Kubernetes Resource、Events 或 Logs 可能造成模型 Context 過大。

因此需對：

- Log 行數
- 查詢時間範圍
- Resource 數量
- Tool Response

進行適當限制與整理。

### 16.4 Tool Calling 穩定性

AI Agent 是否能準確選擇工具，會直接影響系統可用性。

因此後續需透過：

- Tool Description
- System Prompt
- Workflow
- Model Selection
- 測試案例

持續進行優化。

### 16.5 AI Agent 不取代維運人員

本專案定位為：

```text
Information Retrieval
+
Information Correlation
+
Initial Diagnosis
+
Troubleshooting Assistance
```

而非完全取代平台維運人員。

重大異常、架構問題及具有風險的操作，仍由專業技術人員負責。

---

## 十七、未來發展方向

### 17.1 Kubernetes Observability Agent

未來可進一步整合：

```text
Metrics
+
Logs
+
Events
+
Traces
```

讓 Agent 可以從不同 Observability Data Source 取得資訊，提升跨資料分析能力。

### 17.2 Internal Knowledge Agent

進一步整合企業內部：

- SOP
- Runbook
- FAQ
- Architecture Document
- Known Issues
- Incident Report

形成：

```text
Real-time Kubernetes State
+
Observability Data
+
Internal Knowledge
```

使 AI Agent 同時具備即時系統資訊與企業維運知識。

### 17.3 Incident Diagnosis Agent

未來可逐步朝以下流程發展：

```text
使用者描述問題
↓
AI Agent 判斷應取得的資料
↓
自動蒐集 Kubernetes / Observability 資訊
↓
關聯異常資訊
↓
提出 Root Cause Hypothesis
↓
提供 Troubleshooting 建議
```

進一步朝企業內部 AIOps / Intelligent Operations 平台發展。

---

## 十八、結論

本專案透過 LLM、AI Agent、MCP、Open WebUI、n8n、Dex、LDAP 與 Kubernetes RBAC 等技術，規劃建置一套企業內部 Kubernetes 自然語言查詢與智慧維運平台。

本專案核心價值並非單純將 AI 與 Kubernetes 連接，而是改變使用者與 Kubernetes 互動的方式。

傳統方式為：

```text
了解 Kubernetes
↓
判斷應查看的 Resource
↓
選擇工具
↓
執行查詢
↓
閱讀系統資訊
↓
人工分析問題
```

導入 Kubernetes AI Agent 後：

```text
描述需求或問題
↓
AI Agent 理解需求
↓
自動選擇適合的 Tool
↓
取得即時系統資訊
↓
協助整理與分析
↓
提供問題方向與建議
```

藉此降低 Kubernetes 使用門檻、提升 Self-Service 能力、縮短資訊取得與問題定位時間，並降低平台維運團隊處理重複性查詢的負擔。

長期而言，本專案可進一步整合企業內部維運知識與 Observability Data，逐步發展為企業內部的智慧維運與 AIOps 輔助平台。
