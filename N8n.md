# n8n + Kubernetes + Quay + HTTPRoute + Gemma + OpenWebUI + Dex 完整部署 SOP

## 0. 最終架構

目前最終建議架構：

```text
使用者
  |
  v
Dex / LDAP
  |
  v
OpenWebUI
  |
  | OpenWebUI Pipe
  v
n8n-main.n8n.svc.cluster.local:5678
  |
  v
n8n Chat Trigger
  |
  v
n8n AI Workflow
  |
  v
Gemma OpenAI-compatible API
```

管理者則可以直接透過：

```text
https://utcsyn8n.k8sstag.unimicron.com
```

進入 n8n 管理介面。

---

# 1. 環境資訊

Kubernetes：

```text
Kubernetes version:
1.36.1
```

Namespace：

```text
n8n
```

StorageClass：

```text
rook-ceph-block
AccessMode: RWO
```

內部 Quay：

```text
utcsyquay.unimicron.com/u02506
```

n8n 網址：

```text
https://utcsyn8n.k8sstag.unimicron.com
```

Gemma API：

```text
https://gemma-4-12b-it-sm-sma.k8ssy.unimicron.com/v1
```

n8n Helm Chart：

```text
Chart: 1.13.0
n8n: 2.40.5
```

---

# 2. 建立 Namespace

確認：

```bash
kubectl get ns n8n
```

如果不存在：

```bash
kubectl create namespace n8n
```

驗證：

```bash
kubectl get ns | grep n8n
```

預期：

```text
n8n   Active
```

---

# 3. Mirror n8n Image 到公司 Quay

因為 Kubernetes Node 無法直接存取：

```text
docker.n8n.io
```

所以先將 image mirror 到公司 Quay。

來源：

```text
docker.io/n8nio/n8n:2.40.5
```

登入公司 Quay：

```bash
skopeo login utcsyquay.unimicron.com
```

確認登入：

```bash
skopeo login --get-login utcsyquay.unimicron.com
```

Mirror：

```bash
skopeo copy \
  docker://docker.io/n8nio/n8n:2.40.5 \
  docker://utcsyquay.unimicron.com/u02506/n8n:2.40.5
```

驗證：

```bash
skopeo inspect \
  docker://utcsyquay.unimicron.com/u02506/n8n:2.40.5
```

如果有回 image metadata 即代表成功。

---

# 4. 建立 n8n Secret

n8n 建議至少保存：

```text
N8N_HOST
N8N_PORT
N8N_PROTOCOL
N8N_ENCRYPTION_KEY
```

產生 encryption key：

```bash
openssl rand -hex 32
```

建立 Secret：

```bash
kubectl create secret generic n8n-core-secrets \
  -n n8n \
  --from-literal=N8N_HOST='utcsyn8n.k8sstag.unimicron.com' \
  --from-literal=N8N_PORT='5678' \
  --from-literal=N8N_PROTOCOL='https' \
  --from-literal=N8N_ENCRYPTION_KEY='<YOUR_ENCRYPTION_KEY>'
```

確認：

```bash
kubectl get secret n8n-core-secrets -n n8n
```

之後不要隨意更換：

```text
N8N_ENCRYPTION_KEY
```

否則既有 Credentials 可能無法解密。

---

# 5. n8n values.yaml

建立：

```text
values.yaml
```

內容：

```yaml
# ============================================================
# n8n Kubernetes Standalone
# ============================================================

image:
  repository: utcsyquay.unimicron.com/u02506/n8n
  tag: "2.40.5"
  pullPolicy: IfNotPresent


# ============================================================
# Deployment mode
# ============================================================

queueMode:
  enabled: false

replicaCount: 1


# ============================================================
# Database
# Standalone 使用 SQLite
# ============================================================

database:
  type: sqlite
  useExternal: false


# ============================================================
# Redis
# ============================================================

redis:
  enabled: false


# ============================================================
# Persistent Storage
# ============================================================

persistence:
  enabled: true
  storageClassName: rook-ceph-block
  accessModes:
    - ReadWriteOnce
  size: 10Gi


# ============================================================
# Service
# ============================================================

service:
  type: ClusterIP
  port: 5678


# ============================================================
# Ingress
# 使用 Gateway API / HTTPRoute，因此關閉
# ============================================================

ingress:
  enabled: false


# ============================================================
# Existing Secret
# ============================================================

secretRefs:
  existingSecret: "n8n-core-secrets"


# ============================================================
# n8n Configuration
# ============================================================

config:
  timezone: Asia/Taipei

  extraEnv:
    - name: N8N_PROXY_HOPS
      value: "1"

    - name: N8N_EDITOR_BASE_URL
      value: "https://utcsyn8n.k8sstag.unimicron.com"

    - name: WEBHOOK_URL
      value: "https://utcsyn8n.k8sstag.unimicron.com/"


# ============================================================
# SQLite + RWO PVC
# ============================================================

strategy:
  type: Recreate
```

---

# 6. 安裝 / Upgrade n8n

使用習慣的：

```bash
helm upgrade --install n8n \
  oci://ghcr.io/n8n-io/n8n-helm-chart/n8n \
  --version 1.13.0 \
  -n n8n \
  -f values.yaml
```

檢查 Pod：

```bash
kubectl get pod -n n8n
```

預期：

```text
n8n-main-xxxxxx   1/1   Running
```

檢查 PVC：

```bash
kubectl get pvc -n n8n
```

預期：

```text
STATUS: Bound
STORAGECLASS: rook-ceph-block
```

檢查 Service：

```bash
kubectl get svc -n n8n
```

預期類似：

```text
n8n-main   ClusterIP   ...   5678/TCP
```

---

# 7. 如果出現 ImagePullBackOff

查看：

```bash
kubectl describe pod -n n8n <POD_NAME>
```

如果看到：

```text
Failed to pull image
docker.n8n.io/...
```

代表 values 沒有正確使用公司 Quay。

確認目前 Deployment image：

```bash
kubectl get deploy n8n-main -n n8n \
  -o jsonpath='{.spec.template.spec.containers[*].image}'
```

正確應為：

```text
utcsyquay.unimicron.com/u02506/n8n:2.40.5
```

---

# 8. 建立 HTTPRoute

建立：

```text
n8n-httproute.yaml
```

內容：

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute

metadata:
  name: n8n
  namespace: n8n

spec:
  parentRefs:
    - name: https-gateway
      namespace: istio-gateway

  hostnames:
    - "utcsyn8n.k8sstag.unimicron.com"

  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /

      backendRefs:
        - name: n8n-main
          port: 5678
```

套用：

```bash
kubectl apply -f n8n-httproute.yaml
```

驗證：

```bash
kubectl get httproute -n n8n
```

詳細：

```bash
kubectl describe httproute n8n -n n8n
```

確認：

```text
Accepted: True
ResolvedRefs: True
```

HTTP 測試：

```bash
curl -k -I \
  https://utcsyn8n.k8sstag.unimicron.com
```

---

# 9. 第一次登入 n8n

瀏覽器：

```text
https://utcsyn8n.k8sstag.unimicron.com
```

第一次會看到：

```text
Set up owner account
```

建立 n8n Owner。

Email 建議使用真實可管理的公司帳號，不建議亂填。

---

# 10. 公司 CA 加入 n8n

Gemma HTTPS 使用公司內部 CA，因此 n8n Node.js 必須信任公司 CA。

建立 Secret：

```bash
kubectl create secret generic unimicron-ca \
  -n n8n \
  --from-file=unimicron-ca.crt=/path/to/unimicron-ca.crt
```

確認：

```bash
kubectl get secret unimicron-ca -n n8n
```

檢查 key：

```bash
kubectl describe secret unimicron-ca -n n8n
```

預期：

```text
unimicron-ca.crt
```

---

# 11. 將 CA mount 到 n8n Pod

values.yaml 需要增加：

```yaml
extraVolumes:
  - name: unimicron-ca
    secret:
      secretName: unimicron-ca

extraVolumeMounts:
  - name: unimicron-ca
    mountPath: /etc/ssl/unimicron
    readOnly: true
```

以及 config：

```yaml
config:
  timezone: Asia/Taipei

  extraEnv:
    - name: N8N_PROXY_HOPS
      value: "1"

    - name: N8N_EDITOR_BASE_URL
      value: "https://utcsyn8n.k8sstag.unimicron.com"

    - name: WEBHOOK_URL
      value: "https://utcsyn8n.k8sstag.unimicron.com/"

    - name: NODE_EXTRA_CA_CERTS
      value: "/etc/ssl/unimicron/unimicron-ca.crt"
```

重新部署：

```bash
helm upgrade --install n8n \
  oci://ghcr.io/n8n-io/n8n-helm-chart/n8n \
  --version 1.13.0 \
  -n n8n \
  -f values.yaml
```

---

# 12. 驗證 CA 是否 mount 成功

檢查：

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  ls -l /etc/ssl/unimicron
```

應看到：

```text
unimicron-ca.crt
```

確認環境變數：

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  sh -c 'echo $NODE_EXTRA_CA_CERTS'
```

預期：

```text
/etc/ssl/unimicron/unimicron-ca.crt
```

---

# 13. 驗證 n8n → Gemma HTTPS

先測 root：

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  node -e "
fetch('https://gemma-4-12b-it-sm-sma.k8ssy.unimicron.com')
.then(r=>console.log(r.status))
.catch(console.error)
"
```

回：

```text
404
```

其實代表：

```text
DNS       OK
TCP       OK
TLS       OK
HTTP      OK
```

只是 `/` 沒有 route。

---

# 14. 驗證 OpenAI-compatible API

測：

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  node -e "
fetch('https://gemma-4-12b-it-sm-sma.k8ssy.unimicron.com/v1/models')
.then(async r=>{
  console.log(r.status);
  console.log(await r.text());
})
.catch(console.error)
"
```

如果回：

```text
200
```

代表 `/v1` 正確。

---

# 15. n8n Assistant / Model 設定

Provider：

```text
Self-hosted or OpenAI-compatible endpoint
```

Base URL：

```text
https://gemma-4-12b-it-sm-sma.k8ssy.unimicron.com/v1
```

API Key：

如果 Model Server 不驗證 API Key，也仍然可以填一個非空值：

```text
dummy
```

Model：

```text
gemma-4-12b-it
```

---

# 16. 建立 n8n Chat Workflow

建立：

```text
When chat message received
```

設定：

```text
Make Chat Publicly Available:
ON

Mode:
Hosted Chat

Authentication:
None
```

Authentication 可以是 None，因為目前最終架構使用：

```text
Dex -> OpenWebUI
```

做使用者登入。

接著串：

```text
Chat Trigger
    |
    v
AI Agent
    |
    v
Gemma Chat Model
```

最後 Publish Workflow。

---

# 17. Chat Trigger URL

目前 Chat Trigger：

```text
https://utcsyn8n.k8sstag.unimicron.com/webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat
```

如果 UUID 未來改變，以 n8n UI 顯示的 Production URL 為準。

---

# 18. OpenWebUI 與 n8n 位於同一 Cluster

現在已經不需要走：

```text
https://utcsyn8n.k8sstag.unimicron.com
```

讓 OpenWebUI backend 呼叫 n8n。

同 cluster 直接使用 Kubernetes Service DNS：

```text
http://n8n-main.n8n.svc.cluster.local:5678
```

因此完整 webhook：

```text
http://n8n-main.n8n.svc.cluster.local:5678/webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat
```

---

# 19. 從 OpenWebUI Pod 驗證 n8n Service

先找到 OpenWebUI：

```bash
kubectl get pod -n open-webui
```

直接 GET：

```bash
kubectl exec -it -n open-webui deploy/open-webui -- \
  curl -v \
  http://n8n-main.n8n.svc.cluster.local:5678/webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat
```

更重要的是 POST：

```bash
kubectl exec -it -n open-webui deploy/open-webui -- \
  curl -v \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{
    "chatInput": "你好",
    "sessionId": "test-001"
  }' \
  http://n8n-main.n8n.svc.cluster.local:5678/webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat
```

如果有得到 AI 回覆，代表：

```text
OpenWebUI Pod -> n8n Service -> Workflow -> Gemma
```

整條都正常。

---

# 20. OpenWebUI 新增 Pipe

進入：

```text
Admin Panel
  ->
Functions
  ->
Create Function
```

Function Name：

```text
N8n AI Agent
```

Function ID：

```text
n8n_ai_agent
```

貼入以下程式：

```python
"""
title: n8n AI Agent
author: unimicron
version: 0.2
"""

from pydantic import BaseModel, Field
from typing import Optional
import httpx


class Pipe:
    class Valves(BaseModel):
        N8N_WEBHOOK_URL: str = Field(
            default=(
                "http://n8n-main.n8n.svc.cluster.local:5678/"
                "webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat"
            ),
            description="n8n Chat Trigger production URL",
        )

        TIMEOUT_SECONDS: int = Field(
            default=120,
            description="Timeout for n8n requests",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __session_id__: Optional[str] = None,
    ):
        messages = body.get("messages", [])

        if not messages:
            return "No message received."

        user_message = ""

        for message in reversed(messages):
            if message.get("role") == "user":
                user_message = message.get("content", "")
                break

        if not user_message:
            return "No user message found."

        session_id = (
            __session_id__
            or __chat_id__
            or (__user__ or {}).get("id")
            or "openwebui-session"
        )

        payload = {
            "chatInput": user_message,
            "sessionId": session_id,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.valves.TIMEOUT_SECONDS,
            ) as client:
                response = await client.post(
                    self.valves.N8N_WEBHOOK_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json"
                    },
                )

                response.raise_for_status()

        except Exception as e:
            return (
                f"Failed to connect to n8n: "
                f"{type(e).__name__}: {repr(e)}"
            )

        try:
            data = response.json()
        except Exception:
            return response.text

        if isinstance(data, dict):
            for key in [
                "output",
                "response",
                "text",
                "message",
            ]:
                if key in data:
                    return str(data[key])

        return str(data)
```

Save。

然後確認 Function：

```text
Active / Enabled
```

必須打開。

回 OpenWebUI Chat。

Model selector 應看到：

```text
N8n AI Agent
```

---

# 21. 驗證 OpenWebUI Pipe

選：

```text
N8n AI Agent
```

輸入：

```text
你好，請回覆測試成功
```

資料流：

```text
OpenWebUI UI
    |
    v
Pipe
    |
    v
n8n-main.n8n.svc.cluster.local:5678
    |
    v
Chat Trigger
    |
    v
AI Agent
    |
    v
Gemma
    |
    v
OpenWebUI
```

---

# 22. OpenWebUI Pipe 加入使用者資訊

如果之後要做權限控制，可以把 Pipe payload 改成：

```python
payload = {
    "chatInput": user_message,
    "sessionId": session_id,

    "user": {
        "id": (__user__ or {}).get("id"),
        "email": (__user__ or {}).get("email"),
        "name": (__user__ or {}).get("name"),
        "role": (__user__ or {}).get("role"),
        "groups": (__user__ or {}).get("groups", []),
    },
}
```

n8n 就可以收到：

```json
{
  "chatInput": "幫我查系統",
  "sessionId": "xxx",

  "user": {
    "id": "...",
    "email": "user@unimicron.com",
    "name": "User",
    "role": "user",
    "groups": [
      "IT"
    ]
  }
}
```

---

# 23. n8n Workflow 做 RBAC / 權限控制

可以加：

```text
Chat Trigger
    |
    v
Switch
```

例如依群組：

```text
IT
HR
GENERAL
ADMIN
```

JavaScript / Code Node 範例：

```javascript
const user = $json.user || {};
const groups = user.groups || [];

if (groups.includes("admin")) {
  return [{
    json: {
      ...$json,
      accessLevel: "admin"
    }
  }];
}

if (groups.includes("IT")) {
  return [{
    json: {
      ...$json,
      accessLevel: "it"
    }
  }];
}

return [{
  json: {
    ...$json,
    accessLevel: "basic"
  }
}];
```

再用 Switch：

```text
accessLevel = admin
 -> Kubernetes Admin Tool

accessLevel = it
 -> IT Knowledge / API

accessLevel = basic
 -> General FAQ only
```

重要：

```text
不要只在 OpenWebUI 隱藏按鈕。

真正敏感的權限控制應該再次在 n8n workflow 驗證。
```

---

# 24. Dex / OpenWebUI

最終架構中：

```text
Dex
  |
  v
OpenWebUI
```

負責：

```text
Authentication
LDAP
User identity
Group
```

而：

```text
OpenWebUI
  |
  v
n8n
```

走 Kubernetes 內部 Service。

因此不需要再讓 n8n Chat Trigger 自己做 Dex OAuth。

---

# 25. 之前建立、現在可以刪除的資源

之前為：

```text
ai-agent.k8sstag.unimicron.com
```

做過：

```text
oauth2-proxy
ai-agent HTTPRoute
n8n-api HTTPRoute
```

現在 OpenWebUI 與 n8n 同 cluster，而且使用：

```text
OpenWebUI Pipe
  ->
n8n Service
```

這些可以移除。

查看：

```bash
kubectl get httproute -n n8n
```

如果有：

```text
ai-agent
n8n-api
n8n
```

保留：

```text
n8n
```

刪除：

```bash
kubectl delete httproute ai-agent -n n8n
```

```bash
kubectl delete httproute n8n-api -n n8n
```

---

# 26. 移除 oauth2-proxy

如果已經確定不使用：

```text
ai-agent.k8sstag.unimicron.com
```

則：

```bash
kubectl delete deployment ai-agent-oauth2-proxy -n n8n
```

```bash
kubectl delete svc ai-agent-oauth2-proxy -n n8n
```

```bash
kubectl delete secret ai-agent-oauth2-proxy -n n8n
```

確認：

```bash
kubectl get all -n n8n | grep oauth
```

應該沒有結果。

---

# 27. Dex 裡之前新增的 ai-agent Client

如果之後完全不再使用：

```text
ai-agent.k8sstag.unimicron.com
```

OAuth 登入，可以從 Dex：

```yaml
staticClients:
```

移除：

```yaml
- id: ai-agent
  name: AI Agent
  ...
```

也可以移除：

```text
AI_AGENT_CLIENT_SECRET
```

對應 Secret：

```bash
kubectl delete secret ai-agent-dex-client -n dex
```

以及 Dex Deployment 裡：

```yaml
env:
  - name: AI_AGENT_CLIENT_SECRET
```

注意：

```text
不要移除 OpenWebUI 本身使用的 Dex Client。
```

---

# 28. 最終應留下的 n8n 檔案

目錄可以整理成：

```text
n8n/
├── values.yaml
├── n8n-httproute.yaml
└── README.md
```

不再需要：

```text
ai-agent-httproute.yaml
n8n-api-httproute.yaml
oauth2-proxy.yaml
```

---

# 29. 日常檢查指令

## Pod

```bash
kubectl get pod -n n8n -o wide
```

## Deployment

```bash
kubectl get deploy -n n8n
```

## Service

```bash
kubectl get svc -n n8n
```

## PVC

```bash
kubectl get pvc -n n8n
```

## HTTPRoute

```bash
kubectl get httproute -n n8n
```

## Helm

```bash
helm list -n n8n
```

## n8n Logs

```bash
kubectl logs -n n8n deploy/n8n-main --tail=100
```

即時：

```bash
kubectl logs -n n8n deploy/n8n-main -f
```

## Pod Events

```bash
kubectl describe pod -n n8n <POD_NAME>
```

---

# 30. 驗證 n8n Service DNS

從 OpenWebUI Pod：

```bash
kubectl exec -it -n open-webui deploy/open-webui -- \
  curl -v http://n8n-main.n8n.svc.cluster.local:5678
```

只要收到 HTTP Response，即代表 Service/DNS 正常。

---

# 31. 驗證完整 Workflow

從 OpenWebUI Pod：

```bash
kubectl exec -it -n open-webui deploy/open-webui -- \
  curl -v \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{
    "chatInput": "你好，請回覆測試成功",
    "sessionId": "manual-test-001"
  }' \
  http://n8n-main.n8n.svc.cluster.local:5678/webhook/16542c81-be4e-4263-9a7b-333459cc5f2e/chat
```

如果回 AI 回答：

```text
OpenWebUI -> n8n -> Gemma
```

完整鏈路正常。

---

# 32. 驗證 Gemma

從 n8n Pod：

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  node -e "
fetch(
  'https://gemma-4-12b-it-sm-sma.k8ssy.unimicron.com/v1/models'
)
.then(async r=>{
  console.log('HTTP:', r.status);
  console.log(await r.text());
})
.catch(console.error)
"
```

預期：

```text
HTTP: 200
```

---

# 33. 驗證 n8n Environment

```bash
kubectl exec -n n8n deploy/n8n-main -- \
  env | grep -E \
'N8N_HOST|N8N_PROTOCOL|N8N_EDITOR_BASE_URL|WEBHOOK_URL|N8N_PROXY_HOPS|NODE_EXTRA_CA_CERTS'
```

預期類似：

```text
N8N_HOST=utcsyn8n.k8sstag.unimicron.com
N8N_PROTOCOL=https
N8N_EDITOR_BASE_URL=https://utcsyn8n.k8sstag.unimicron.com
WEBHOOK_URL=https://utcsyn8n.k8sstag.unimicron.com/
N8N_PROXY_HOPS=1
NODE_EXTRA_CA_CERTS=/etc/ssl/unimicron/unimicron-ca.crt
```

---

# 34. 更新 n8n

修改：

```text
values.yaml
```

然後：

```bash
helm upgrade --install n8n \
  oci://ghcr.io/n8n-io/n8n-helm-chart/n8n \
  --version 1.13.0 \
  -n n8n \
  -f values.yaml
```

檢查：

```bash
kubectl rollout status deployment/n8n-main -n n8n
```

---

# 35. 備份

目前使用 SQLite，因此最重要的是 PVC 裡的：

```text
/home/node/.n8n
```

裡面包含：

```text
database.sqlite
config
其他 n8n state
```

正式環境建議對 Ceph PVC 做 Snapshot / Backup。

同時保存：

```text
N8N_ENCRYPTION_KEY
```

沒有 Encryption Key，即使 database.sqlite 還在，Credentials 仍可能無法解密。

---

# 36. Git Repository 建議結構

```text
K8s-ai-agent/
├── n8n/
│   ├── values.yaml
│   ├── n8n-httproute.yaml
│   └── README.md
│
└── open-webui/
    └── README.md
```

Secret 不應 commit：

```text
N8N_ENCRYPTION_KEY
LDAP bind password
Dex Client Secret
API keys
Quay passwords
```

---

# 37. 安全注意事項

## 不要 commit Secret

例如：

```yaml
password: xxx
secret: xxx
bindPW: xxx
```

都不要放 Git。

## LDAP bind password

如果 LDAP bind password 曾經出現在：

```text
Screenshot
Chat
Git
Terminal history
```

建議 rotate。

## n8n Encryption Key

必須安全保存。

## OpenWebUI Pipe

Pipe 執行在 OpenWebUI server 端，因此只有管理者應該能新增 / 修改 Function。

## 權限

敏感 API / K8s / DB 操作必須在 n8n workflow 再做 RBAC 驗證。

---

# 38. 最終架構總結

```text
                   +----------------------+
                   |         Dex          |
                   |       + LDAP         |
                   +----------+-----------+
                              |
                              v
                   +----------------------+
                   |      OpenWebUI       |
                   |                      |
                   |   n8n AI Agent Pipe  |
                   +----------+-----------+
                              |
                              | Cluster Internal HTTP
                              |
                              v
              http://n8n-main.n8n.svc.cluster.local:5678
                              |
                              v
                   +----------------------+
                   |         n8n          |
                   |                      |
                   |     Chat Trigger     |
                   |          |           |
                   |       AI Agent       |
                   +----------+-----------+
                              |
                              | HTTPS
                              v
                   +----------------------+
                   |        Gemma         |
                   |       /v1 API        |
                   +----------------------+
```

管理者：

```text
Browser
  |
  v
https://utcsyn8n.k8sstag.unimicron.com
  |
  v
HTTPRoute
  |
  v
n8n-main:5678
```

一般使用者：

```text
Browser
  |
  v
Dex Login
  |
  v
OpenWebUI
  |
  v
n8n AI Agent Pipe
  |
  v
n8n internal Service
```

這就是目前最終、最乾淨的架構。
