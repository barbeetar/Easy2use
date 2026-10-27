
# Kubernetes Troubleshooting - Kyverno 與 Kueue Admission Webhook 循環依賴造成 Istio Gateway 故障

| 項目 | 說明 |
|---|---|
| 故障日期 | 2026/10/27（排查日期） |
| 環境 | Kubernetes Staging（k8sstag） |
| 影響元件 | Kyverno、Kueue、Istio Gateway、Dex OIDC |
| 故障類型 | Admission Webhook Failure / Circular Dependency |
| 故障狀態 | 已恢復 |
| Root Cause | Kyverno 與 Kueue Admission Webhook 互相阻擋，導致 Pod 無法重新建立 |
| 修復方式 | 處理 Webhook 阻擋問題，重啟 Kueue Controller 與 Istio Gateway |

---

## 1. 環境資訊

### 1.1 Kubernetes 環境

| 項目 | 設定 |
|---|---|
| Kubernetes Cluster | Staging |
| Kubernetes API Server | `https://10.13.241.109:6443` |
| Bastion | `utcsyk8sbas01` |
| Master | `utcsyk8smast01` |
| 認證方式 | OIDC（Dex） |
| Dex URL | `https://utcsyk8sdex.k8sstag.unimicron.com` |
| Gateway VIP | `10.13.241.125` |
| Gateway Namespace | `istio-gateway` |
| Gateway Deployment | `https-gateway-istio` |
| Istio Proxy Version | `1.30.2-distroless` |
| Kyverno Version | `v1.19.1` |
| Kueue Version | `v0.18.3` |

### 1.2 相關 Kubernetes 元件

**Kyverno**

- Namespace：`kyverno`
- Deployment：`kyverno-admission-controller`
- Webhook Service：`kyverno-svc`
- Service IP：`10.99.166.192`

**Kueue**

- Namespace：`kueue-system`
- Deployment：`kueue-controller-manager`
- Webhook Service：`kueue-webhook-service`
- Service IP：`10.101.209.70`

**Istio Gateway**

- Namespace：`istio-gateway`
- Deployment：`https-gateway-istio`
- Gateway VIP：`10.13.241.125`
- Proxy Image：`utcsyquay.unimicron.com/istio/proxyv2:1.30.2-distroless`

---

## 2. 故障現象（Symptoms）

### 2.1 Bastion 無法使用 kubectl

在 `utcsyk8sbas01` 執行：

```bash
kubectl get pods -A
```

出現以下錯誤：

```text
error: get-token: authentication error:
oidc error: oidc discovery error:

Get "https://utcsyk8sdex.k8sstag.unimicron.com/.well-known/openid-configuration":

dial tcp 10.13.241.125:443:
connect: connection refused

Unable to connect to the server:
getting credentials:
exec: executable kubectl failed with exit code 1
```

**初步判斷：**

kubectl 透過 OIDC 取得 Token 時，需要連線 Dex。

但 Dex 對外網址透過 Istio Gateway VIP `10.13.241.125` 提供服務，目前 TCP 443 無法連線。

因此，Bastion 上所有需要透過 Dex 取得憑證的 kubectl 操作都失敗。

此階段尚不能判斷 Dex 本身是否故障。

---

### 2.2 Master 上的 kubectl 正常

切換至 Master：

```bash
ssh utcsyk8smast01
```

使用 Kubernetes 管理員憑證：

```bash
export KUBECONFIG=/etc/kubernetes/admin.conf

kubectl get nodes
```

結果：正常。

**判斷：**

- Kubernetes API Server 可正常處理查詢。
- Master 使用 Client Certificate，不需要連線 Dex。
- 問題不是整個 Kubernetes API Server 無法使用。
- 需要繼續檢查 Istio Gateway 與 Admission Webhook。

注意：能執行 kubectl 查詢，不代表 Pod 建立或 Deployment 更新一定正常，因為寫入操作仍可能受到 Admission Webhook 影響。

---

## 3. Troubleshooting 排查過程

### Step 1：檢查 Istio Gateway Deployment

執行：

```bash
kubectl get deploy -n istio-gateway

kubectl describe deploy https-gateway-istio \
  -n istio-gateway
```

發現：

```text
Replicas: 1 desired | 0 updated | 0 total | 0 available

Conditions:

Progressing      True
Available        False   MinimumReplicasUnavailable
ReplicaFailure   True    FailedCreate
```

**分析：**

Gateway 預期應有一個 Pod，但實際為 0。

`ReplicaFailure=True` 與 `FailedCreate` 表示 Deployment 所屬 ReplicaSet 無法成功建立 Pod。

因此，需繼續檢查 ReplicaSet Events。

---

### Step 2：嘗試重啟 Istio Gateway

執行：

```bash
kubectl rollout restart \
  deploy/https-gateway-istio \
  -n istio-gateway
```

結果：失敗。

錯誤：

```text
error: failed to patch:

Internal error occurred:
failed calling webhook "mdeployment.kb.io"

failed to call webhook:

Post "https://kueue-webhook-service.kueue-system.svc:443/mutate-apps-v1-deployment?timeout=10s":

dial tcp 10.101.209.70:443:
connect: connection refused
```

**分析：**

API Server 在更新 Deployment 時，需要呼叫 Kueue Mutating Admission Webhook。

但 Kueue Webhook Service 無法連線，因此 Deployment 更新遭到拒絕。

這時發現問題可能不在 Istio Gateway 本身，而是 Kueue Admission Webhook。

---

### Step 3：檢查 Istio Gateway ReplicaSet

執行：

```bash
kubectl get rs -n istio-gateway
```

接著查看：

```bash
kubectl describe rs \
  https-gateway-istio-5586854948 \
  -n istio-gateway
```

發現：

```text
Replicas: 0 current / 1 desired

Conditions:
ReplicaFailure True FailedCreate
```

Events：

```text
Warning FailedCreate

Error creating:
Internal error occurred:

failed calling webhook "mpod.kb.io"

Post "https://kueue-webhook-service.kueue-system.svc:443/mutate--v1-pod?timeout=10s"

dial tcp 10.101.209.70:443:
connect: connection refused
```

事件紀錄：

```text
x353 over 3d19h
```

**分析：**

Istio Gateway ReplicaSet 在建立 Pod 時，會呼叫 Kueue Pod Mutating Webhook。

由於 Kueue Webhook 連線失敗，API Server 拒絕建立 Gateway Pod。

因此確認：

```text
Kueue Webhook 無法連線
        ↓
Istio Gateway Pod FailedCreate
        ↓
Gateway 無法提供外部 HTTPS 入口
```

---

### Step 4：檢查 Kueue Controller

執行：

```bash
kubectl get pods,deploy,svc,endpoints \
  -n kueue-system -o wide
```

結果：

```text
deployment.apps/kueue-controller-manager

READY       0/1
UP-TO-DATE  0
AVAILABLE   0
```

Webhook Service：

```text
kueue-webhook-service

CLUSTER-IP:
10.101.209.70

ENDPOINTS:
<none>
```

**分析：**

Kueue Controller 本身沒有成功建立 Pod。

因此 Kueue Webhook Service 雖然存在，但沒有可用後端 Endpoint。

API Server 呼叫：

```text
kueue-webhook-service.kueue-system.svc:443
```

時發生：

```text
connect: connection refused
```

進一步確認 EndpointSlice：

```bash
kubectl get endpointslice \
  -n kueue-system \
  -l kubernetes.io/service-name=kueue-webhook-service
```

---

### Step 5：檢查 Kueue ReplicaSet

執行：

```bash
kubectl get rs -n kueue-system
```

結果：

```text
NAME                                  DESIRED CURRENT READY
kueue-controller-manager-5854c789c4  1       0       0
```

檢查：

```bash
kubectl describe rs \
  kueue-controller-manager-5854c789c4 \
  -n kueue-system
```

Events：

```text
Warning FailedCreate

Error creating:
Internal error occurred:

failed calling webhook
"vpol.validate.kyverno.svc-fail"

Post "https://kyverno-svc.kyverno.svc:443/vpol/namespace-storage-group-quota/require-company-registry/require-resource-settings?timeout=10s"

dial tcp 10.99.166.192:443:
connect: connection refused
```

**分析：**

Kueue Controller 無法建立 Pod 的原因，是 Kyverno Validating Webhook 無法連線。

因此形成：

```text
Kyverno Webhook 無法連線
        ↓
Kueue Controller Pod FailedCreate
        ↓
Kueue Webhook 沒有 Endpoint
        ↓
Istio Gateway Pod FailedCreate
```

繼續往上檢查 Kyverno。

---

### Step 6：檢查 Kyverno Controller 狀態

執行：

```bash
kubectl get pods,deploy,svc -n kyverno -o wide
```

結果：

| Deployment | Ready |
|---|---|
| kyverno-admission-controller | 0/1 |
| kyverno-background-controller | 1/1 |
| kyverno-cleanup-controller | 0/1 |
| kyverno-reports-controller | 0/1 |

發現 Kyverno 有三個 Controller 無法正常建立 Pod。

其中最重要的是：

```text
kyverno-admission-controller 0/1
```

因此 `kyverno-svc` 沒有正常的 Admission Controller 後端。

---

### Step 7：過濾 Kyverno FailedCreate Events

因為 `kubectl describe rs` 輸出較長，改用 Events 過濾方式查看。

```bash
kubectl get events -n kyverno \
  --field-selector reason=FailedCreate \
  -o custom-columns='OBJECT:.involvedObject.name,MESSAGE:.message' \
  --no-headers
```

結果：

```text
kyverno-admission-controller-7ff955755c
Error creating:
failed calling webhook "mpod.kb.io"
dial tcp 10.101.209.70:443:
connect: connection refused
```

```text
kyverno-cleanup-controller-58779679b7
Error creating:
failed calling webhook "mpod.kb.io"
dial tcp 10.101.209.70:443:
connect: connection refused
```

```text
kyverno-reports-controller-57cfc8bcb5
Error creating:
failed calling webhook "mpod.kb.io"
dial tcp 10.101.209.70:443:
connect: connection refused
```

**分析：**

Kyverno Controller 自己的 Pod 建立時，同樣被 Kueue Admission Webhook 阻擋。

至此確認 Kyverno 與 Kueue 形成雙向阻擋。

```text
Kyverno Controller 無法建立
          ↑          ↓
       互相阻擋（Webhook）
          ↑          ↓
Kueue Controller 無法建立
```

這是本次故障無法自行恢復的主要原因。

---

## 4. Root Cause Analysis（RCA）

### 4.1 Root Cause：Admission Webhook 循環依賴

本次排查確認：

**Kyverno 與 Kueue 的 Admission Webhook 相互攔截對方 Controller Pod 的建立請求。**

當兩邊的 Webhook Service 都無法提供服務時，API Server 在建立 Pod 時遇到 Webhook Connection Refused。

對採用 `failurePolicy: Fail` 的 Webhook，API Server 會拒絕相關建立請求，導致 Controller 無法恢復。

實際觀察到：

```text
Kueue Controller Pod
  → Kyverno Validating Webhook
  → Connection Refused
  → FailedCreate
```

以及：

```text
Kyverno Controller Pod
  → Kueue Mutating Webhook
  → Connection Refused
  → FailedCreate
```

兩者共同形成 Admission Webhook Circular Dependency。

### 4.2 Webhook 設定調查

使用以下指令查詢：

```bash
for type in mutating validating; do
  kubectl get "${type}webhookconfigurations" -o json |
    jq -r --arg type "$type" '
      .items[]
      | .metadata.name as $config
      | (.webhooks // []) | to_entries[]
      | select(
          .value.name == "mpod.kb.io" or
          .value.name == "vpol.validate.kyverno.svc-fail"
        )
      | [
          $type,
          $config,
          (.key | tostring),
          .value.name,
          (.value.failurePolicy // "Fail")
        ]
      | @tsv
    '
done
```

排查時查到：

| 類型 | Webhook | Index | failurePolicy |
|---|---|---|---|
| Mutating | mpod.kb.io | 9 | Ignore |
| Validating | vpol.validate.kyverno.svc-fail | 0 | Fail |

注意：

- Kueue `mpod.kb.io` 在查詢當下已顯示 `Ignore`。
- 先前 Events 仍記錄其連線失敗並造成 FailedCreate。
- 目前無法確認整段故障期間是否曾使用不同的 failurePolicy。
- 因此不能斷言兩個 Webhook 在整段故障期間都設定為 `Fail`。

但 Events 已確認兩個 Admission Webhook 曾分別阻擋對方 Controller Pod 的建立。

### 4.3 最初觸發原因尚未確認

本次已確認造成系統無法自動恢復的原因是 Admission Webhook 循環依賴。

但尚未取得證據確認最初是哪個事件造成 Kyverno 或 Kueue Controller Pod 消失。

後續可調查：

- Node 故障或重啟
- Pod Eviction
- Deployment Rollout
- 維護期間的 Pod 刪除
- Admission Webhook 設定異動
- API Server / Controller Manager 日誌

因此需要區分：

**已確認：循環依賴造成 Pod 重建失敗。**

**待確認：最初造成 Webhook 後端不可用的觸發事件。**

---

## 5. 修復過程（Recovery）

### Step 1：備份 Admission Webhook 設定

在 Master 執行：

```bash
mkdir -p ~/webhook-recovery-20261027

cd ~/webhook-recovery-20261027
```

備份：

```bash
kubectl get mutatingwebhookconfigurations -o yaml \
  > mutatingwebhooks-backup.yaml

kubectl get validatingwebhookconfigurations -o yaml \
  > validatingwebhooks-backup.yaml
```

目的是保留原始設定，便於緊急調整後比對及還原。

### Step 2：排查 Webhook failurePolicy

排查時發現：

```text
mpod.kb.io                         Ignore
vpol.validate.kyverno.svc-fail      Fail
```

曾嘗試使用 JSON Patch 修改 Webhook，但因索引不符，API Server 拒絕了請求。

錯誤：

```text
The request is invalid:
the server rejected our request due to an error in our request
```

原因：

- Kueue Webhook 實際 Index 為 `9`，先前 Patch 使用 `0`。
- Kyverno Webhook 實際 Index 為 `0`，先前 Patch 使用 `2`。

JSON Patch 的 `test` 不通過，因此該 Patch 未成功。

不能將這兩次失敗的 Patch 記錄為已完成的設定變更。

### Step 3：恢復 Kueue Controller

故障處理期間，最後有重新啟動 Kueue Controller Deployment。

```bash
kubectl rollout restart \
  deploy/kueue-controller-manager \
  -n kueue-system
```

驗證：

```bash
kubectl rollout status \
  deploy/kueue-controller-manager \
  -n kueue-system
```

查看：

```bash
kubectl get pods -n kueue-system
```

確認 Webhook Service 有可用 Endpoint：

```bash
kubectl get endpointslice \
  -n kueue-system \
  -l kubernetes.io/service-name=kueue-webhook-service \
  -o wide
```

目的：

讓 Kueue Controller 恢復提供 Admission Webhook 服務。

注意：

若 Webhook 仍處於 fail-closed 且無法連線，`rollout restart` 本身仍可能被阻擋，因此必須先確認 admission 阻擋已排除。

### Step 4：重新啟動 Istio Gateway

Kueue 恢復後，重新啟動 Gateway Deployment。

```bash
kubectl rollout restart \
  deploy/https-gateway-istio \
  -n istio-gateway
```

確認 Rollout：

```bash
kubectl rollout status \
  deploy/https-gateway-istio \
  -n istio-gateway
```

查看 Gateway Pod：

```bash
kubectl get pods -n istio-gateway -o wide
```

目的：

重新建立 Istio Gateway Pod，恢復 Envoy Proxy 對外提供 HTTPS 流量。

### Step 5：驗證 Kyverno

```bash
kubectl get deploy -n kyverno
```

重點確認：

```text
kyverno-admission-controller
kyverno-background-controller
kyverno-cleanup-controller
kyverno-reports-controller
```

再次查看是否還有新的 FailedCreate：

```bash
kubectl get events -n kyverno \
  --field-selector reason=FailedCreate \
  --sort-by=.lastTimestamp
```

注意：舊 Events 可能仍存在，必須比對事件時間與 ReplicaSet 狀態，避免把歷史錯誤當成目前故障。

### Step 6：驗證 Dex OIDC

在 Bastion 執行：

```bash
curl -vk \
  https://utcsyk8sdex.k8sstag.unimicron.com/.well-known/openid-configuration
```

預期：

```text
HTTP/2 200
```

並回傳 OpenID Connect Discovery JSON。

注意：`-k` 僅供故障排查使用，正式 TLS 驗證應確認 CA 憑證鏈有效。

接著測試：

```bash
kubectl get nodes

kubectl get pods -A
```

確認透過 Dex 的 Kubernetes OIDC 認證流程恢復。

---

## 6. 故障影響範圍

| 元件 | 影響 |
|---|---|
| Kubernetes API Server | 可使用 admin.conf 存取 |
| Kyverno | Admission Controller 無法正常提供 Webhook |
| Kueue | Controller 無 Pod，Webhook Service 無 Endpoint |
| Istio Gateway | Pod FailedCreate，無法提供正常入口 |
| Dex OIDC | 經 Gateway 的 Discovery Endpoint 無法連線 |
| Bastion kubectl | OIDC Token 取得失敗 |
| Grafana / OpenProject / n8n | 若共用相同 Gateway，可能受到影響，未逐一驗證 |

---

## 7. Preventive Actions（預防改善）

### 7.1 避免 Admission Webhook 循環依賴

評估調整 Kyverno 與 Kueue Webhook 的匹配範圍。

例如：

- Kueue Pod Webhook 避免攔截 Kyverno Controller 的關鍵 Pod。
- Kyverno Policy / Webhook 避免在恢復階段阻擋 Kueue Controller。
- 針對系統 Namespace 設計明確的排除規則。
- 避免無差別套用到所有 Namespace。

可以透過：

- `namespaceSelector`
- `objectSelector`
- `matchConditions`
- Kyverno Policy 的 Match / Exclude 設定

實際使用哪種方式，需依各 webhook 的用途、Policy 類型及版本決定，並在變更後測試。

### 7.2 檢查 failurePolicy

目前排查發現：

```text
Kueue mpod.kb.io:
Ignore

Kyverno vpol.validate.kyverno.svc-fail:
Fail
```

建議檢討關鍵 Controller 對 Admission Webhook 的依賴，避免失效時所有系統元件都無法建立。

不建議永久將所有 webhook 改為 `Ignore`，因為可能失去 Registry、Quota、Resource 等 Policy 的強制驗證。

### 7.3 建立監控告警

可監控：

**Deployment Available Replicas**

```promql
kube_deployment_spec_replicas{
  namespace=~"kyverno|kueue-system|istio-gateway"
}
-
kube_deployment_status_replicas_available{
  namespace=~"kyverno|kueue-system|istio-gateway"
}
> 0
```

此查詢可用來觀察目標 Namespace 中 Deployment 預期副本數大於可用副本數的情況。

建議搭配適當的 `for` 持續時間，避免短暫 Rollout 造成誤報。

另外可監控：

- Admission Webhook Service 無 Ready Endpoint
- Kubernetes API Server Admission Webhook Rejection
- Kyverno Admission Controller Availability
- Kueue Controller Availability
- Istio Gateway Availability

### 7.4 Gateway 高可用性

目前 Istio Gateway Deployment 只有一個副本。

建議評估：

```yaml
replicas: 2
```

並搭配：

- Pod Anti-Affinity 或 Topology Spread Constraints
- PodDisruptionBudget
- 多 Worker Node 分散部署
- Gateway Readiness / Availability Monitoring

注意：若 Admission Webhook 阻擋所有新 Pod 建立，增加副本數本身仍無法解決循環依賴，因此應優先修正 Admission Webhook 設計。

---

## 8. 常用 Troubleshooting 指令整理

### 檢查 Deployment

```bash
kubectl get deploy -n kyverno

kubectl get deploy -n kueue-system

kubectl get deploy -n istio-gateway
```

### 檢查 ReplicaSet

```bash
kubectl get rs -n kyverno

kubectl get rs -n kueue-system

kubectl get rs -n istio-gateway
```

### 快速查看 FailedCreate

```bash
kubectl get events -n kyverno \
  --field-selector reason=FailedCreate \
  -o custom-columns='OBJECT:.involvedObject.name,MESSAGE:.message' \
  --no-headers
```

```bash
kubectl get events -n kueue-system \
  --field-selector reason=FailedCreate \
  -o custom-columns='OBJECT:.involvedObject.name,MESSAGE:.message' \
  --no-headers
```

```bash
kubectl get events -n istio-gateway \
  --field-selector reason=FailedCreate \
  -o custom-columns='OBJECT:.involvedObject.name,MESSAGE:.message' \
  --no-headers
```

### 檢查 Webhook Configuration

```bash
kubectl get mutatingwebhookconfigurations

kubectl get validatingwebhookconfigurations
```

### 檢查 Webhook Service EndpointSlice

```bash
kubectl get endpointslice -n kyverno \
  -l kubernetes.io/service-name=kyverno-svc
```

```bash
kubectl get endpointslice -n kueue-system \
  -l kubernetes.io/service-name=kueue-webhook-service
```

### 重啟 Kueue Controller

```bash
kubectl rollout restart \
  deploy/kueue-controller-manager \
  -n kueue-system
```

### 重啟 Istio Gateway

```bash
kubectl rollout restart \
  deploy/https-gateway-istio \
  -n istio-gateway
```

---

## 9. Troubleshooting 結論

### 問題原因

本次 Kubernetes Staging 環境發生 Kyverno 與 Kueue Admission Webhook 的循環依賴問題。

當 Controller Pod 無法提供 Webhook Service 時，API Server 在建立相關 Pod 時遭遇 webhook connection refused，導致關鍵元件無法自行恢復。

此問題進一步影響 Istio Gateway Deployment，造成 Gateway Pod 無法建立，最終使 Dex OIDC 對外入口無法連線，導致 Bastion kubectl 認證失敗。

### 本次修復操作

排查 Webhook 故障及設定後，重新啟動：

1. `kueue-system/kueue-controller-manager`
2. `istio-gateway/https-gateway-istio`

使用者確認環境已恢復正常。

### 最終 Root Cause

**Admission Webhook Circular Dependency（Admission Webhook 循環依賴）**

### 後續改善重點

**調整 Kyverno 與 Kueue Webhook 的適用範圍，避免關鍵 Controller Pod 在 Webhook 故障時互相阻擋，造成 Kubernetes 核心服務連鎖中斷。**

---

## 10. 參考資料

- Kubernetes Admission Webhook 官方文件  
  https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/

- Kubernetes Admission Webhook Good Practices  
  https://kubernetes.io/docs/concepts/cluster-administration/admission-webhooks-good-practices/

- Kyverno Troubleshooting  
  https://kyverno.io/docs/guides/troubleshooting/

- Kueue 官方文件  
  https://kueue.sigs.k8s.io/

