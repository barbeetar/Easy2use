# Kubernetes Grafana MCP Server 部署與 n8n 串接 SOP

## 1. 文件目的

本文件記錄 Grafana MCP Server 在 Kubernetes 環境中的部署、設定、驗證及 n8n AI Agent 串接流程。

透過 Grafana MCP Server，讓 AI Agent 可以存取既有 Grafana 的監控資源，例如：

- 搜尋 Grafana Dashboard
- 查詢 Dashboard 及 Panel 資訊
- 取得 Grafana Dashboard 連結
- 查詢 Prometheus / Mimir Metrics
- 查詢 Loki Logs
- 取得 Grafana Panel 圖片

本文件主要說明 Grafana MCP 的基礎建置與連線。

n8n 部分僅記錄 MCP Client 的連線設定，不涵蓋 AI Prompt、Explore 圖片處理及後續 AI 分析流程。

---

# 2. 系統架構

## 2.1 整體架構

```text
                     使用者
                       |
                       v
                  n8n Chat
                       |
                       v
                    AI Agent
                       |
                       v
               Grafana MCP Client
                       |
                       | HTTP Streamable MCP
                       | Bearer Token
                       v
              Grafana MCP Server
                Kubernetes Pod
                       |
                       | Grafana Service Account Token
                       v
                 Grafana Server
                       |
          +------------+------------+
          |            |            |
          v            v            v
     Dashboard       Mimir         Loki
       Panel         Metrics       Logs
          |
          v
    Image Renderer
          |
          v
       PNG 圖片
```

## 2.2 主要元件

| 元件 | 功能 |
|---|---|
| Kubernetes | 執行 Grafana、MCP Server、Image Renderer |
| Grafana | 提供 Dashboard、Panel 與 Data Source |
| Grafana MCP Server | 將 Grafana 功能提供為 MCP Tools |
| Grafana Image Renderer | 將 Dashboard / Panel 渲染為 PNG |
| n8n | AI Agent 工作流程 |
| MCP Client Tool | 讓 n8n AI Agent 呼叫 Grafana MCP |
| Mimir | 儲存與查詢 Metrics |
| Loki | 儲存與查詢 Logs |

---

# 3. 環境資訊

以下為本次實際使用的主要設定。

| 項目 | 設定 |
|---|---|
| Grafana Namespace | monitoring |
| Grafana Helm Release | grafana |
| Grafana Helm Chart | 12.4.1 |
| Grafana MCP Namespace | grafana-mcp |
| Grafana MCP Helm Release | grafana-mcp |
| Grafana MCP Helm Chart | 0.27.2 |
| Grafana MCP Image | mcp-grafana:2.0.2 |
| Grafana MCP Port | 8000 |
| MCP Transport | Streamable HTTP |
| Grafana Service Port | 80 |
| Grafana Image Renderer Image | v5.12.6 |
| Container Registry | utcsyquay.unimicron.com |

### Grafana 對外網址

```text
https://utckfk8sgrafana.k8skf.unimicron.com
```

### Grafana Kubernetes Service

```text
http://grafana.monitoring.svc.cluster.local:80
```

### Grafana MCP Kubernetes Service

```text
http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp
```

注意：

上述 Kubernetes Service DNS 適用於相同叢集，或已設定跨叢集 DNS 與網路連線的環境。

若 Grafana MCP 與 Grafana 部署在不同 Kubernetes 叢集，不能直接假設 `grafana.monitoring.svc.cluster.local` 可以跨叢集存取，必須改用實際可連通的 Grafana URL。

---

# 4. Grafana MCP Container Image 準備

## 4.1 確認 Image

本次使用的 Grafana MCP Image：

```text
grafana/mcp-grafana:2.0.2
```

公司 Quay：

```text
utcsyquay.unimicron.com/grafana/mcp-grafana:2.0.2
```

確認官方映像：

```bash
skopeo inspect \
  docker://docker.io/grafana/mcp-grafana:2.0.2
```

## 4.2 登入公司 Quay

```bash
skopeo login utcsyquay.unimicron.com
```

輸入具有 Image 推送權限的 Quay 帳號或 Robot Account 憑證。

## 4.3 推送 Image

```bash
skopeo copy --all \
  docker://docker.io/grafana/mcp-grafana:2.0.2 \
  docker://utcsyquay.unimicron.com/grafana/mcp-grafana:2.0.2
```

若公司 Quay 已有相同 Image，可跳過這個步驟。

## 4.4 驗證 Quay Image

```bash
skopeo inspect \
  docker://utcsyquay.unimicron.com/grafana/mcp-grafana:2.0.2
```

確認 Image 可以正常查詢。

---

# 5. Grafana MCP Helm Chart 準備

## 5.1 Chart 資訊

本次使用：

```text
oci://utcsyquay.unimicron.com/helm-charts/grafana-mcp
```

Chart Version：

```text
0.27.2
```

注意：

Chart Version 與 Container Image Version 是不同的版本資訊。

本次：

```text
Chart Version = 0.27.2

Container Image = 2.0.2
```

## 5.2 確認公司 Quay Chart

```bash
helm show chart \
  oci://utcsyquay.unimicron.com/helm-charts/grafana-mcp \
  --version 0.27.2
```

## 5.3 取得 Chart 預設 Values

```bash
helm show values \
  oci://utcsyquay.unimicron.com/helm-charts/grafana-mcp \
  --version 0.27.2 \
  > grafana-mcp-default-values.yaml
```

如果需要重新推送 Chart，可先取得官方同版本 Chart，確認套件來源與版本後，再使用：

```bash
helm push grafana-mcp-0.27.2.tgz \
  oci://utcsyquay.unimicron.com/helm-charts
```

此指令前提是本機已有正確的 Chart `.tgz` 檔案，且具備 Quay Push 權限。

---

# 6. 建立 Grafana Service Account Token

Grafana MCP Server 需要使用 Token 存取 Grafana API。

本次採用 Grafana Service Account Token，不使用 Dex ID Token 作為 Grafana MCP 的後端憑證。

## 6.1 建立 Service Account

登入 Grafana：

```text
https://utckfk8sgrafana.k8skf.unimicron.com
```

進入：

```text
Administration
  → Users and access
  → Service accounts
```

建立 Grafana MCP 專用 Service Account。

建議名稱：

```text
grafana-mcp-reader
```

角色建議：

```text
Viewer
```

原則上採用最小權限，不需要直接給予 Admin。

若後續查詢工具回報權限不足，應確認 Dashboard、Folder 與 Data Source 的實際權限，再依需要調整。

## 6.2 建立 Token

進入剛建立的 Service Account：

```text
Add service account token
```

建立 Token，例如：

```text
grafana-mcp-token
```

建立後保存 Token。

注意：

Grafana Token 通常只會在建立時顯示完整內容。

不可將 Token 寫入 Git Repository、公開文件或執行日誌。

---

# 7. 建立 Grafana MCP Kubernetes Secret

## 7.1 Token 說明

本次使用兩組不同 Token。

| Secret Key | 用途 |
|---|---|
| grafana-token | Grafana MCP 存取 Grafana |
| mcp-client-token | n8n 存取 Grafana MCP |

兩者不能混用。

### grafana-token

由 Grafana Service Account 建立。

### mcp-client-token

使用 OpenSSL 產生隨機 Token：

```bash
openssl rand -hex 32
```

## 7.2 建立 Namespace

```bash
kubectl create namespace grafana-mcp \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 7.3 建立 Secret YAML

檔案名稱：

```text
grafana-mcp-secret.yaml
```

內容：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: grafana-mcp-auth
  namespace: grafana-mcp
type: Opaque
stringData:
  grafana-token: "REPLACE_GRAFANA_SERVICE_ACCOUNT_TOKEN"
  mcp-client-token: "REPLACE_MCP_CLIENT_TOKEN"
```

需要替換：

```text
REPLACE_GRAFANA_SERVICE_ACCOUNT_TOKEN
```

以及：

```text
REPLACE_MCP_CLIENT_TOKEN
```

實際部署時，建議避免將含明文 Token 的檔案提交到版本控制系統。

也可改用互動式輸入建立 Secret，減少磁碟上保存明文的風險。

## 7.4 建立 Secret

```bash
kubectl apply -f grafana-mcp-secret.yaml
```

## 7.5 驗證 Secret

```bash
kubectl get secret -n grafana-mcp
```

預期：

```text
NAME                 TYPE     DATA
grafana-mcp-auth     Opaque   2
```

`DATA=2` 表示存在兩個 Secret Key。

---

# 8. 建立 Grafana MCP values.yaml

## 8.1 建立檔案

```bash
mkdir -p ~/grafana-mcp
cd ~/grafana-mcp

vi values.yaml
```

以下為本次環境所需的主要客製化設定。

其餘欄位繼承 Chart 0.27.2 預設值。

因此這是一份完整可供部署使用的「覆寫 values」，並不需要把官方所有預設欄位重複貼入。

## 8.2 values.yaml

```yaml
# ============================================================
# Grafana MCP Server
# Chart Version: 0.27.2
# MCP Image Version: 2.0.2
# ============================================================

replicas: 1

# ------------------------------------------------------------
# Image
# ------------------------------------------------------------

image:
  registry: utcsyquay.unimicron.com
  repository: grafana/mcp-grafana
  tag: "2.0.2"
  pullPolicy: IfNotPresent

imagePullSecrets: []

# ------------------------------------------------------------
# Existing Grafana
# ------------------------------------------------------------

grafana:
  url: "http://grafana.monitoring.svc.cluster.local:80"
  apiKey: ""

  apiKeySecret:
    name: grafana-mcp-auth
    key: grafana-token

# ------------------------------------------------------------
# MCP Settings
# ------------------------------------------------------------

debug: false

disableWrite: true

disabledCategories: []

extraArgs:
  - "-t"
  - "streamable-http"

  - "--address"
  - "0.0.0.0:8000"

  - "--enabled-tools"
  - "search,dashboard,navigation,datasource,prometheus,loki,rendering"

# ------------------------------------------------------------
# Environment
# ------------------------------------------------------------

env: {}

envValueFrom:
  MCP_GRAFANA_SERVER_TOKEN:
    secretKeyRef:
      name: grafana-mcp-auth
      key: mcp-client-token

envFrom: []

# ------------------------------------------------------------
# Service
# ------------------------------------------------------------

service:
  enabled: true
  type: ClusterIP
  port: 8000

# ------------------------------------------------------------
# Ingress
# ------------------------------------------------------------

ingress:
  enabled: false

# ------------------------------------------------------------
# Resources
# ------------------------------------------------------------

resources:
  requests:
    cpu: 100m
    memory: 128Mi

  limits:
    cpu: 500m
    memory: 512Mi

# ------------------------------------------------------------
# Service Account
# ------------------------------------------------------------

serviceAccount:
  create: true
  automountServiceAccountToken: false

automountServiceAccountToken: false

# ------------------------------------------------------------
# Security
# ------------------------------------------------------------

containerSecurityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000

securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000

# ------------------------------------------------------------
# Monitoring
# ------------------------------------------------------------

metrics:
  enabled: false

serviceMonitor:
  enabled: false

hpa:
  enabled: false
```

### 重要說明

本次 MCP Server 採用：

```text
Streamable HTTP
```

監聽：

```text
0.0.0.0:8000
```

並使用：

```text
disableWrite: true
```

限制 Grafana 寫入工具。

本次設定保留 `prometheus`、`loki`、`rendering` 等工具分類，但不代表後續所有功能都已驗證成功。

此 values 應使用實際的私有 Chart 0.27.2 進行 Helm Template 驗證。

如果 Chart 不支援個別欄位，必須以該版本實際模板為準。

---

# 9. 部署 Grafana MCP Server

## 9.1 檢查 Helm Template

部署前先執行：

```bash
helm template grafana-mcp \
  oci://utcsyquay.unimicron.com/helm-charts/grafana-mcp \
  --version 0.27.2 \
  -n grafana-mcp \
  -f values.yaml \
  > rendered.yaml
```

確認產生的 Image：

```bash
grep -n 'image:' rendered.yaml
```

確認啟動參數：

```bash
grep -n -A 20 'args:' rendered.yaml
```

確認 Secret：

```bash
grep -n -A 8 'MCP_GRAFANA_SERVER_TOKEN' rendered.yaml
```

確認設定有正確被 Helm Chart 套用。

## 9.2 安裝 Grafana MCP

```bash
helm upgrade --install grafana-mcp \
  oci://utcsyquay.unimicron.com/helm-charts/grafana-mcp \
  --version 0.27.2 \
  -n grafana-mcp \
  --create-namespace \
  -f values.yaml \
  --wait \
  --timeout 5m
```

## 9.3 確認 Helm 狀態

```bash
helm status grafana-mcp -n grafana-mcp
```

預期：

```text
STATUS: deployed
```

## 9.4 確認 Deployment

```bash
kubectl get deploy -n grafana-mcp
```

本次實際 Deployment 名稱：

```text
grafana-mcp
```

確認：

```text
READY 1/1
```

## 9.5 確認 Pod

```bash
kubectl get pods -n grafana-mcp
```

確認 Pod 為：

```text
Running
```

## 9.6 確認 Service

```bash
kubectl get svc -n grafana-mcp
```

本次部署結果：

```text
NAME          TYPE        CLUSTER-IP    PORT(S)
grafana-mcp   ClusterIP   10.104.8.70   8000/TCP
```

因此 MCP Endpoint 為：

```text
http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp
```

建議使用 Service DNS，不要直接使用 ClusterIP。

---

# 10. Grafana MCP 部署驗證

## 10.1 確認 Container Image

```bash
kubectl get deploy grafana-mcp \
  -n grafana-mcp \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

本次實際確認：

```text
utcsyquay.unimicron.com/grafana/mcp-grafana:2.0.2
```

## 10.2 查看 Logs

```bash
kubectl logs \
  -n grafana-mcp \
  deploy/grafana-mcp \
  --tail=100
```

若需要即時觀察：

```bash
kubectl logs \
  -n grafana-mcp \
  deploy/grafana-mcp \
  -f
```

## 10.3 確認服務與監聽狀態

```bash
kubectl get pods,svc -n grafana-mcp
```

若 Pod 無法啟動：

```bash
kubectl describe pods -n grafana-mcp
```

查看事件：

```bash
kubectl get events \
  -n grafana-mcp \
  --sort-by=.lastTimestamp
```

## 10.4 確認 Token 驗證

MCP Server 使用：

```text
MCP_GRAFANA_SERVER_TOKEN
```

n8n 連線時需要在 HTTP Authorization Header 提供：

```http
Authorization: Bearer <mcp-client-token>
```

可使用支援 Streamable HTTP 的 MCP Client 實際執行初始化與工具列舉，確認 Token 有效。

不可只靠 Pod Running 或普通 HTTP GET 的結果，認定 MCP 工具已經可以正常呼叫。

---

# 11. Grafana Image Renderer 建置

## 11.1 建置目的

Grafana Dashboard 可以在網頁正常顯示，不代表可以直接將 Panel 輸出為 PNG。

若需要 Grafana Panel 圖片功能，Grafana 必須具有可用的 Image Renderer。

本次實際遇到：

```text
Image renderer plugin not installed
```

因此另外導入 Grafana Image Renderer。

本次採用 Grafana Helm Chart 內建的 `imageRenderer` 設定管理 Renderer，而非手動建立另一份 Deployment YAML。

---

# 12. 準備 Image Renderer Container Image

## 12.1 Image 資訊

本次使用：

```text
grafana/grafana-image-renderer:v5.12.6
```

公司 Quay：

```text
utcsyquay.unimicron.com/u02506/grafana-image-renderer:v5.12.6
```

## 12.2 確認官方 Image

```bash
skopeo inspect \
  docker://docker.io/grafana/grafana-image-renderer:v5.12.6
```

## 12.3 使用 Skopeo 推送

本次使用：

```bash
skopeo copy --all \
  docker://docker.io/grafana/grafana-image-renderer:v5.12.6 \
  docker://utcsyquay.unimicron.com/u02506/grafana-image-renderer:v5.12.6
```

### 曾遇到的錯誤

```text
writing blob: Patch ... EOF
```

代表推送 Image Layer 過程中連線中斷。

若所有 Kubernetes Node 均使用 amd64，可改為只推送 amd64：

```bash
skopeo copy \
  --override-os linux \
  --override-arch amd64 \
  --retry-times 3 \
  docker://docker.io/grafana/grafana-image-renderer:v5.12.6 \
  docker://utcsyquay.unimicron.com/u02506/grafana-image-renderer:v5.12.6
```

必要時減少並行傳輸：

```bash
skopeo copy \
  --override-os linux \
  --override-arch amd64 \
  --image-parallel-copies 1 \
  --retry-times 5 \
  docker://docker.io/grafana/grafana-image-renderer:v5.12.6 \
  docker://utcsyquay.unimicron.com/u02506/grafana-image-renderer:v5.12.6
```

## 12.4 驗證 Image

```bash
skopeo inspect \
  docker://utcsyquay.unimicron.com/u02506/grafana-image-renderer:v5.12.6
```

---

# 13. 建立 Renderer Secret

Renderer 與 Grafana 必須使用相同的驗證 Token。

注意：

Renderer Token 不等於 Grafana MCP Token。

## 13.1 建立 Token

```bash
openssl rand -hex 32
```

本次 Secret 名稱：

```text
grafana-renderer-auth
```

Namespace：

```text
monitoring
```

## 13.2 建立 Secret

如果是首次建立，可使用：

```bash
TOKEN=$(openssl rand -hex 32)

kubectl create secret generic grafana-renderer-auth \
  -n monitoring \
  --from-literal=renderer-token="$TOKEN"

unset TOKEN
```

## 13.3 增加 Chart 使用的 Key

本次部署時，Grafana Image Renderer Chart 設定引用了 `token` Key。

原本 Secret 使用：

```text
renderer-token
```

因此將同一個值同步到：

```text
token
```

執行：

```bash
TOKEN_B64=$(kubectl get secret grafana-renderer-auth \
  -n monitoring \
  -o jsonpath='{.data.renderer-token}')

test -n "$TOKEN_B64" || {
  echo "找不到 renderer-token"
  exit 1
}

kubectl patch secret grafana-renderer-auth \
  -n monitoring \
  --type merge \
  -p "{\"data\":{\"token\":\"${TOKEN_B64}\"}}"

unset TOKEN_B64
```

驗證：

```bash
kubectl get secret grafana-renderer-auth -n monitoring
```

預期：

```text
NAME                    TYPE     DATA
grafana-renderer-auth   Opaque   2
```

注意：

這是本次 Chart / Secret 配置採用的 Key 結構。

若改用其他 Chart 版本，必須先確認其 Secret Key 規格。

---

# 14. 修改既有 Grafana Helm Values

## 14.1 既有 Grafana 環境

本次 Grafana 使用：

```text
Release: grafana
Namespace: monitoring
Chart: grafana-12.4.1
```

原本 Grafana 已設定：

- Dex OAuth
- Grafana PVC
- Dashboard ConfigMap Sidecar
- Mimir Data Sources
- Loki Data Sources
- Tempo Data Sources

因此本次只新增 Image Renderer 相關設定。

不重新部署 Dashboard，也不修改原有 Data Source。

## 14.2 修改原本 values.yaml

在原本 Grafana `values.yaml` 最外層新增：

```yaml
# ============================================================
# Grafana Image Renderer
# ============================================================

imageRenderer:
  enabled: true

  replicas: 1

  image:
    registry: utcsyquay.unimicron.com
    repository: u02506/grafana-image-renderer
    tag: "v5.12.6"
    pullPolicy: IfNotPresent

  existingSecret: grafana-renderer-auth

  service:
    enabled: true
    portName: http
    port: 8081
    targetPort: 8081

  grafanaProtocol: http
  grafanaSubPath: ""

  token: ""

  env:
    HTTP_HOST: "0.0.0.0"
    XDG_CONFIG_HOME: /tmp/.chromium
    XDG_CACHE_HOME: /tmp/.chromium

  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "2"
      memory: 2Gi

  affinity:
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          preference:
            matchExpressions:
              - key: node-role.kubernetes.io/infra
                operator: In
                values:
                  - "true"

  tolerations:
    - key: infra-node
      operator: Equal
      value: reserved
      effect: NoSchedule
```

這是本次使用的主要 Renderer 客製化設定。

應使用公司 Quay 的 Grafana Chart `12.4.1` 進行模板驗證，確認上述值實際套用於 Renderer Deployment。

## 14.3 特別注意重複環境變數

本次曾遇到：

```text
UPGRADE FAILED:
duplicate entries for key
GF_RENDERING_RENDERER_TOKEN
```

原因是：

Grafana Helm Chart 開啟：

```yaml
imageRenderer:
  enabled: true
```

後，已經自動建立 Rendering 相關環境變數。

如果又在 `envValueFrom` 手動加入：

```yaml
GF_RENDERING_RENDERER_TOKEN:
  secretKeyRef:
    name: grafana-renderer-auth
    key: renderer-token
```

就可能產生重複設定。

### 修正方式

在本次 Chart 內建 Renderer 模式下，移除先前手動加入的：

```text
GF_RENDERING_RENDERER_TOKEN
```

保留原本 Dex OAuth 的設定：

```yaml
envValueFrom:
  GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET:
    secretKeyRef:
      name: grafana-dex-secret
      key: client-secret
```

Renderer Token 交由 Chart 的 `imageRenderer` 設定管理。

此外，若使用 Chart 自動設定 Render Server URL 與 Callback URL，應避免同時使用另外一套手動 `grafana.ini.rendering` 設定而產生覆蓋。

部署前確認 Helm 最終產生的 Deployment 為準。

---

# 15. Grafana 更新前注意事項

原本 Grafana 已有 Dashboard 與資料來源，因此更新前應先確認並備份。

## 15.1 確認 Helm Release

```bash
helm list -n monitoring
```

本次環境：

```text
grafana    monitoring    grafana-12.4.1
```

## 15.2 備份 Helm Values

```bash
helm get values grafana \
  -n monitoring \
  -o yaml \
  > grafana-values-backup.yaml
```

## 15.3 確認 PVC

```bash
kubectl get pvc -n monitoring
```

## 15.4 確認 Dashboard ConfigMap

```bash
kubectl get cm \
  -n monitoring \
  -l grafana_dashboard
```

## 15.5 確認資料庫

必須確認 Grafana 實際使用 SQLite 或 PostgreSQL。

```bash
kubectl get deploy grafana \
  -n monitoring \
  -o yaml \
  | grep -A 12 -E 'GF_DATABASE_|GF_PATHS_DATA'
```

如果沒有 `GF_DATABASE_*`，不代表一定使用 SQLite，仍需檢查實際 Grafana Configuration。

重要：

- Helm Values 備份不等於 Dashboard 資料備份
- 更新時保留原資料庫與 PVC
- 不要刪除 Dashboard ConfigMap
- 不要任意切換 Grafana Image
- 不要為了新增 Renderer 而重建 Grafana 資料庫

若 Grafana 使用資料庫保存 Dashboard，正式變更前還應完成實際資料備份。

---

# 16. 更新 Grafana

本次使用公司 Quay Helm Chart：

```text
oci://utcsyquay.unimicron.com/helm-charts/grafana
```

Chart Version：

```text
12.4.1
```

更新前先確認模板：

```bash
helm template grafana \
  oci://utcsyquay.unimicron.com/helm-charts/grafana \
  --version 12.4.1 \
  -n monitoring \
  -f new-values-fixed.yaml \
  > rendered-grafana.yaml
```

檢查是否建立 Renderer 資源與 Secret 引用。

確認沒有重複 Environment Variable。

然後執行：

```bash
helm upgrade --install grafana \
  oci://utcsyquay.unimicron.com/helm-charts/grafana \
  --version 12.4.1 \
  -n monitoring \
  -f new-values-fixed.yaml \
  --wait \
  --timeout 5m
```

注意：

`new-values-fixed.yaml` 是本次實際更新所使用的檔名。

正式環境應使用目前已驗證、保留原有 Grafana 設定的 values 檔案。

---

# 17. Grafana Image Renderer 驗證

## 17.1 查看 Deployment

```bash
kubectl get deploy -n monitoring
```

## 17.2 查看 Pod

```bash
kubectl get pods -n monitoring
```

確認 Grafana 與 Renderer Pod 均正常執行。

## 17.3 查看 Service

```bash
kubectl get svc -n monitoring
```

找到 Renderer Service。

服務名稱及 Port 以 Helm 實際建立的資源為準。

## 17.4 查看 Renderer Logs

先找名稱：

```bash
kubectl get deploy -n monitoring | grep -i render
```

再查看：

```bash
kubectl logs \
  -n monitoring \
  deploy/<實際RendererDeployment名稱> \
  --tail=100
```

## 17.5 Grafana Web UI 驗證

開啟 Grafana。

進入：

```text
Dashboard
    → 找到一張 Panel
    → Export
    → Export as image
```

確認可以產生並下載 PNG。

本次在完成 Renderer 部署及 Grafana 更新後，已成功產生 Panel 圖片。

這代表 Grafana Image Renderer 已可正常處理部分 Panel Render 請求。

不代表所有 Grafana MCP 圖片工具及 Explore Render 都已驗證完成。

---

# 18. n8n 連接 Grafana MCP

本章僅說明 MCP Client 連線，不涵蓋 AI Agent Prompt 或進階圖片處理。

## 18.1 n8n Workflow 架構

本次使用：

```text
When chat message received
          |
          v
       AI Agent
          |
          +---- OpenAI Chat Model
          |
          +---- Kubernetes MCP Client
          |
          +---- Grafana MCP Client
```

Grafana MCP Client 是接在 AI Agent 下方的 Tool 接口。

不是接在 Trigger 後面的主流程節點。

## 18.2 新增 MCP Client Tool

進入 n8n 主 Workflow。

點選：

```text
AI Agent
   → Tool
   → +
   → MCP Client Tool
```

名稱可設定為：

```text
MCP Client(grafana)
```

## 18.3 設定 Endpoint

```text
http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp
```

如果 Service 名稱不同，請使用：

```bash
kubectl get svc -n grafana-mcp
```

確認實際 DNS 名稱。

n8n 所在的 Pod 必須能連接該 Endpoint。

## 18.4 設定 Authentication

選
## 17.4 查看 Renderer Logs

先確認 Grafana Image Renderer 的實際 Deployment 名稱：

```bash
kubectl get deploy -n monitoring | grep -i render
```

找到 Renderer Deployment 後，查看 Logs：

```bash
kubectl logs \
  -n monitoring \
  deploy/<實際RendererDeployment名稱> \
  --tail=100
```

若需要即時查看：

```bash
kubectl logs \
  -n monitoring \
  deploy/<實際RendererDeployment名稱> \
  -f
```

注意：

`<實際RendererDeployment名稱>` 必須替換成 Kubernetes 查詢到的 Deployment 名稱。

若查不到 Renderer Deployment，應確認 Grafana Helm Chart 的 `imageRenderer.enabled` 是否已正確套用。

## 17.5 Grafana Web UI 驗證

開啟 Grafana：

```text
https://utckfk8sgrafana.k8skf.unimicron.com
```

登入 Grafana 後，進入既有 Dashboard。

操作路徑：

```text
Grafana
  → Dashboard
  → 選擇監控圖表
  → Export
  → Export as image
```

確認可以成功產生或下載 PNG 圖片。

本次環境在部署 Image Renderer 並更新 Grafana Helm 後，已成功透過 Web UI 產生 Panel 圖片。

### 驗證重點

1. Grafana Dashboard 可以正常開啟。
2. 原本的 Dashboard 沒有遺失。
3. Mimir、Loki、Tempo Data Source 設定仍然存在。
4. Grafana Image Renderer Pod 正常執行。
5. Grafana 可以成功產生 Panel PNG。

注意：

Grafana Web UI 成功產生 PNG，代表 Image Renderer 已具備基本功能。

但不代表 Grafana MCP 的所有圖片渲染模式均已測試成功。

---

# 18. n8n 連接 Grafana MCP

本章僅記錄 n8n 如何連接已部署的 Grafana MCP Server。

不包含：

- AI Agent System Prompt
- Grafana Explore 圖片產生
- Dashboard Panel ID 自動判斷
- 圖片 Base64 處理
- OpenWebUI 圖片顯示

## 18.1 n8n Workflow 架構

本次使用 n8n AI Agent 搭配 MCP Client Tool。

架構如下：

```text
When chat message received
          |
          v
       AI Agent
          |
          +---- OpenAI Chat Model
          |
          +---- Kubernetes MCP Client
          |
          +---- Grafana MCP Client
```

其中：

`Grafana MCP Client` 必須連接在 AI Agent 下方的 Tool 接口。

不是直接連接在 Chat Trigger 後面。

## 18.2 新增 Grafana MCP Client Tool

進入 n8n Workflow。

點擊：

```text
AI Agent
   |
   v
Tool
   |
   v
+
   |
   v
MCP Client Tool
```

新增後，建議命名：

```text
MCP Client(grafana)
```

此節點負責讓 AI Agent 呼叫 Grafana MCP Server 提供的工具。

## 18.3 設定 Grafana MCP Endpoint

在 MCP Client Tool 的 Parameters 中設定：

| 設定項目 | 設定值 |
|---|---|
| Endpoint | http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp |
| Authentication | Bearer Auth |
| Tools to Include | Selected |

本次 Grafana MCP Kubernetes Service：

```text
Service Name: grafana-mcp
Namespace: grafana-mcp
Port: 8000
Transport: Streamable HTTP
```

對應 MCP Endpoint：

```text
http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp
```

確認 Service：

```bash
kubectl get svc -n grafana-mcp
```

本次實際 Service 資訊：

```text
NAME          TYPE        CLUSTER-IP    PORT(S)
grafana-mcp   ClusterIP   10.104.8.70   8000/TCP
```

建議使用 Service DNS，而不是直接使用 ClusterIP。

注意：

n8n Pod 必須能夠透過 Kubernetes 網路連接 Grafana MCP Service。

如果 n8n 與 Grafana MCP 位於不同叢集，必須另外確認跨叢集網路與 DNS 設定。

## 18.4 設定 Bearer Authentication

Grafana MCP Server 已設定：

```yaml
envValueFrom:
  MCP_GRAFANA_SERVER_TOKEN:
    secretKeyRef:
      name: grafana-mcp-auth
      key: mcp-client-token
```

因此 n8n 需要使用相同的 Token 連線。

在 MCP Client Tool 選擇：

```text
Authentication
    |
    v
Bearer Auth
    |
    v
Create New Credential
```

Credential 名稱：

```text
Grafana MCP Auth
```

Bearer Token：

```text
填入 grafana-mcp-auth Secret 中
mcp-client-token 的原始值
```

### Token 用途區分

| Token | 使用位置 | 功能 |
|---|---|---|
| grafana-token | Grafana MCP Server | 存取 Grafana API |
| mcp-client-token | n8n MCP Client Tool | 驗證 Grafana MCP Server |
| renderer-token | Grafana / Image Renderer | 驗證圖片渲染服務 |

n8n 必須使用：

```text
mcp-client-token
```

不要使用：

```text
grafana-token
```

也不需要使用 Kubernetes Dex ID Token。

## 18.5 選擇 Grafana MCP Tools

進入：

```text
MCP Client(grafana)
    |
    v
Tools to Include
    |
    v
Selected
```

第一階段建議先選擇：

```text
search_dashboards
get_dashboard_summary
generate_deeplink
```

這三個工具適合驗證基礎 Grafana MCP 連線。

| Tool | 功能 |
|---|---|
| search_dashboards | 搜尋 Grafana Dashboard |
| get_dashboard_summary | 取得 Dashboard 與 Panel 摘要 |
| generate_deeplink | 產生 Grafana 連結 |

若後續需要其他監控功能，可以再加入：

```text
list_datasources
query_prometheus
query_loki_logs
get_panel_image
```

### 工具使用注意事項

Grafana MCP Server 提供的工具，取決於：

1. MCP Server 版本。
2. Helm Values 啟用的工具分類。
3. Grafana Service Account 權限。
4. Grafana Data Source 是否存在。

本次 Grafana MCP Server Image Version：

```text
2.0.2
```

建議不要直接將所有 Grafana MCP Tools 開放給 AI Agent。

原因是過多工具定義及大量查詢結果，可能增加 AI 模型的 Context Token 使用量。

本次使用的模型 Context Window：

```text
32768 Tokens
```

因此建議優先選擇必要工具。

## 18.6 驗證 n8n 與 Grafana MCP 連線

完成 MCP Client Tool 設定後，回到 n8n Chat。

輸入：

```text
請幫我搜尋 Grafana 裡現有的 Dashboard，
列出 Dashboard 名稱與連結。
```

接著進入 n8n：

```text
Executions
    |
    v
AI Agent
    |
    v
MCP Client(grafana)
```

查看工具執行結果。

成功時，應可觀察到：

```text
Tool: search_dashboards
```

並取得 Grafana Dashboard 的相關資訊。

若需要測試 Dashboard 詳細資訊，可詢問：

```text
請幫我查詢 SRE - Node / Node Detail
這個 Grafana Dashboard 裡有哪些 Panel。
```

確認是否呼叫：

```text
get_dashboard_summary
```

若成功回傳 Panel 清單，表示 n8n 已具備透過 MCP 查詢 Grafana Dashboard 資訊的能力。

---

# 19. 常見問題排查

## 19.1 Grafana MCP Pod 出現 ImagePullBackOff

檢查 Pod：

```bash
kubectl get pods -n grafana-mcp
```

查看詳細資訊：

```bash
kubectl describe pods -n grafana-mcp
```

常見原因：

- Quay 中沒有對應 Image。
- Image Registry 路徑錯誤。
- Image Tag 不存在。
- Kubernetes Node 無法連線 Quay。
- 缺少 ImagePullSecret。

確認 Image：

```bash
skopeo inspect \
  docker://utcsyquay.unimicron.com/grafana/mcp-grafana:2.0.2
```

## 19.2 Grafana MCP 無法連接 Grafana

先確認 Grafana Service：

```bash
kubectl get svc -n monitoring
```

Grafana MCP 的 Grafana URL 設定：

```yaml
grafana:
  url: "http://grafana.monitoring.svc.cluster.local:80"
```

注意：

這個 URL 是 Grafana MCP Server 連接 Grafana 的位置。

不是 n8n 連接 Grafana MCP 的 Endpoint。

兩者不可混用。

## 19.3 Grafana MCP 回傳 401 Unauthorized

可能原因：

- n8n Bearer Token 錯誤。
- Token 與 Kubernetes Secret 不一致。
- MCP Server 尚未載入新的 Secret。
- Grafana Service Account Token 已失效。

檢查 Secret：

```bash
kubectl get secret grafana-mcp-auth -n grafana-mcp
```

如果更新了 Secret，需要確認 Pod 已載入新的環境變數。

可執行：

```bash
kubectl rollout restart \
  deployment/grafana-mcp \
  -n grafana-mcp
```

等待完成：

```bash
kubectl rollout status \
  deployment/grafana-mcp \
  -n grafana-mcp
```

然後重新測試 n8n Credential。

## 19.4 Grafana MCP 回傳 403 Forbidden

可能是 Grafana Service Account 權限不足。

建議檢查：

- Service Account Role
- Dashboard 權限
- Folder 權限
- Data Source 權限
- Data Source Query 權限

本次 Grafana MCP 主要用於唯讀查詢。

因此優先使用：

```text
Viewer
```

如有特定權限不足，應針對缺少的權限進行調整。

不建議直接將 Service Account 設為 Admin。

## 19.5 Image Renderer 無法產生圖片

先確認 Renderer Pod：

```bash
kubectl get pods -n monitoring
```

查看 Grafana Logs：

```bash
kubectl logs \
  -n monitoring \
  deploy/grafana \
  -c grafana \
  --since=10m
```

查看 Renderer Deployment：

```bash
kubectl get deploy -n monitoring | grep -i render
```

再查看 Renderer Logs：

```bash
kubectl logs \
  -n monitoring \
  deploy/<實際RendererDeployment名稱> \
  --since=10m
```

若 Grafana Web UI 顯示：

```text
Image renderer plugin not installed
```

表示 Grafana 尚未成功設定圖片渲染功能。

應檢查：

- Grafana Image Renderer Deployment
- Grafana Image Renderer Service
- Renderer Token
- Grafana Rendering Server URL
- Grafana Rendering Callback URL

## 19.6 Grafana Helm Upgrade 出現重複環境變數

本次曾遇到：

```text
UPGRADE FAILED

duplicate entries for key
GF_RENDERING_RENDERER_TOKEN
```

原因：

Grafana Helm Chart 啟用內建 Image Renderer 後，已自動產生 Rendering Token 環境變數。

但原本 values.yaml 又手動加入相同環境變數。

解決方式：

在使用 Chart 內建 Renderer 的情況下，移除重複的：

```text
GF_RENDERING_RENDERER_TOKEN
```

保留原本 Dex OAuth Secret 設定。

重新執行：

```bash
helm template grafana \
  oci://utcsyquay.unimicron.com/helm-charts/grafana \
  --version 12.4.1 \
  -n monitoring \
  -f new-values-fixed.yaml \
  > rendered-grafana.yaml
```

確認 Deployment 沒有重複的環境變數後，再執行 Helm Upgrade。

## 19.7 Grafana MCP 圖片渲染 HTTP 500

若 Grafana MCP 執行：

```text
get_panel_image
```

出現：

```text
failed to render image: HTTP 500
```

代表圖片渲染請求失敗。

可能涉及：

- Grafana Render API
- Image Renderer
- Dashboard Panel 參數
- Explore Render 參數
- Grafana Service Account 權限

本次 Grafana Web UI 的一般 Panel 圖片匯出已成功測試。

但 Grafana MCP Explore 模式曾出現 HTTP 500。

因此此功能仍屬後續排查項目。

建議同時查看 Grafana MCP、Grafana 與 Renderer 的 Logs，不應直接認定是某一層發生問題。

---

# 20. 維運指令整理

## 20.1 Grafana MCP

查看 Helm：

```bash
helm status grafana-mcp -n grafana-mcp
```

查看 Pod：

```bash
kubectl get pods -n grafana-mcp
```

查看 Service：

```bash
kubectl get svc -n grafana-mcp
```

查看 Deployment：

```bash
kubectl get deploy -n grafana-mcp
```

查看 Logs：

```bash
kubectl logs \
  -n grafana-mcp \
  deploy/grafana-mcp \
  --tail=100
```

即時查看：

```bash
kubectl logs \
  -n grafana-mcp \
  deploy/grafana-mcp \
  -f
```

重新啟動：

```bash
kubectl rollout restart \
  deployment/grafana-mcp \
  -n grafana-mcp
```

確認 Rollout：

```bash
kubectl rollout status \
  deployment/grafana-mcp \
  -n grafana-mcp
```

## 20.2 Grafana

查看 Helm：

```bash
helm status grafana -n monitoring
```

查看 Pod：

```bash
kubectl get pods -n monitoring
```

查看 Service：

```bash
kubectl get svc -n monitoring
```

查看 Grafana Logs：

```bash
kubectl logs \
  -n monitoring \
  deploy/grafana \
  -c grafana \
  --tail=100
```

## 20.3 Helm History

Grafana：

```bash
helm history grafana -n monitoring
```

Grafana MCP：

```bash
helm history grafana-mcp -n grafana-mcp
```

## 20.4 Helm Rollback

Grafana：

```bash
helm rollback grafana <REVISION> -n monitoring
```

Grafana MCP：

```bash
helm rollback grafana-mcp <REVISION> -n grafana-mcp
```

執行 Rollback 前，應先確認要回復的 Revision。

注意：

Helm Rollback 只會回復 Helm 管理的 Kubernetes 資源設定。

不代表會還原 Grafana 資料庫或 Dashboard 資料。

---

# 21. 安全注意事項

## 21.1 Token 管理

本次涉及三種主要 Token。

| Token | 使用目的 |
|---|---|
| Grafana Service Account Token | Grafana MCP 存取 Grafana |
| MCP Client Token | n8n 存取 Grafana MCP |
| Renderer Token | Grafana 與 Image Renderer 驗證 |

三種 Token 不應混用。

建議：

- 使用 Kubernetes Secret 管理 Token。
- 不將 Token 提交到 Git Repository。
- 不在截圖中顯示完整 Token。
- 避免 Token 出現在 n8n Execution Logs。
- 定期輪替憑證。
- 採用最小權限原則。

## 21.2 唯讀設定

Grafana MCP 使用：

```yaml
disableWrite: true
```

目的為停用 MCP 提供的寫入工具。

但仍需要注意：

唯讀不等於 Namespace 資料隔離。

目前 Grafana MCP 使用共用 Grafana Service Account。

因此能存取哪些 Dashboard 和 Data Source，取決於該 Service Account 的實際權限。

## 21.3 n8n 與 Grafana MCP 連線

本次採用：

```text
HTTP Streamable MCP
```

並使用：

```text
Bearer Token
```

因為 HTTP 在叢集內不會自動提供傳輸加密，正式環境應依資訊安全需求評估：

- NetworkPolicy
- TLS
- Service Mesh
- Kubernetes Secret
- 最小權限
- 存取日誌保護

---

# 22. 建置成果

本次已完成的項目如下。

| 項目 | 狀態 |
|---|---|
| Grafana MCP Image 推送至公司 Quay | 已完成 |
| Grafana MCP Helm Chart 0.27.2 | 已準備 |
| Grafana Service Account Token | 已建立 |
| Grafana MCP Secret | 已建立 |
| Grafana MCP Helm 部署 | 已完成 |
| Grafana MCP Deployment | Running |
| Grafana MCP Service | Port 8000 |
| Grafana MCP Image | 2.0.2 |
| Grafana Image Renderer Image | v5.12.6 |
| Grafana Image Renderer 整合 | 已完成 |
| Grafana Export as image | 測試成功 |
| n8n Grafana MCP Client | 已連線 |
| Dashboard 搜尋 | 已成功 |
| Dashboard Summary 查詢 | 已成功 |
| Grafana Explore Metrics 查詢 | Web UI 測試成功 |
| Grafana MCP Explore 圖片渲染 | 尚待排查 HTTP 500 |

---

# 23. 總結

本次完成 Grafana MCP Server 在 Kubernetes 環境中的部署與 n8n 基礎串接。

透過 Grafana Service Account Token，Grafana MCP Server 可使用既有 Grafana 提供的 Dashboard、Panel 及 Data Source 查詢介面。

Grafana MCP Server 使用公司 Quay 管理 Container Image 與 Helm Chart，並透過 Kubernetes ClusterIP Service 提供內部 MCP Endpoint。

同時導入 Grafana Image Renderer，讓既有 Grafana 具備 PNG 圖片產生功能。

n8n AI Agent 則透過 MCP Client Tool，以 Bearer Token 連接 Grafana MCP Server。

目前已完成基礎 MCP 連線與 Dashboard 資訊查詢能力。

後續可再擴充自然語言 Metrics 查詢、Logs 分析、Panel 圖片輸出，以及依使用者身分進行監控資料存取控制。

---

# 24. 官方參考文件

## Grafana MCP

https://grafana.com/docs/grafana/latest/developer-resources/mcp/

## Grafana MCP Helm 部署

https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/deploy-with-helm/

## Grafana MCP Command-Line Flags

https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/command-line-flags/

## Grafana Image Renderer

https://github.com/grafana/grafana-image-renderer

## Grafana Helm Chart

https://github.com/grafana-community/helm-charts
